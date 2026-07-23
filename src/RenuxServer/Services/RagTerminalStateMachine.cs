using System.Text.Json;

namespace RenuxServer.Services;

public enum RagTerminalStage
{
    Open,
    CompletionSeen,
    DoneSeen,
    Invalid,
}

/// <summary>
/// Enforces the backend-owned SSE terminal contract. Every data event is
/// observed so no content can arrive after completion or done unnoticed.
/// </summary>
public sealed class RagTerminalStateMachine(string backendRequestId)
{
    public RagTerminalStage Stage { get; private set; } = RagTerminalStage.Open;
    public int CompletionCount { get; private set; }
    public int DoneCount { get; private set; }
    public string? FailureReason { get; private set; }

    public bool IsSuccessful => Stage == RagTerminalStage.DoneSeen
        && CompletionCount == 1
        && DoneCount == 1;

    public bool Observe(JsonElement payload, out string? eventType)
    {
        eventType = null;
        if (Stage is RagTerminalStage.DoneSeen or RagTerminalStage.Invalid)
        {
            Invalidate("event_after_terminal");
            return false;
        }

        if (!payload.TryGetProperty("type", out JsonElement typeProperty)
            || typeProperty.ValueKind != JsonValueKind.String
            || string.IsNullOrWhiteSpace(typeProperty.GetString()))
        {
            Invalidate("missing_event_type");
            return false;
        }

        eventType = typeProperty.GetString();
        if (eventType == "completion")
        {
            CompletionCount += 1;
            if (Stage != RagTerminalStage.Open || !HasMatchingRequestId(payload) || !HasValidCompletionShape(payload))
            {
                Invalidate("invalid_completion");
                return false;
            }

            Stage = RagTerminalStage.CompletionSeen;
            return true;
        }

        if (eventType == "done")
        {
            DoneCount += 1;
            if (Stage != RagTerminalStage.CompletionSeen || !HasMatchingRequestId(payload))
            {
                Invalidate("invalid_done");
                return false;
            }

            Stage = RagTerminalStage.DoneSeen;
            return true;
        }

        if (Stage == RagTerminalStage.CompletionSeen)
        {
            Invalidate("event_between_completion_and_done");
            return false;
        }

        return true;
    }

    public void ObserveMalformedData() => Invalidate("malformed_data_event");

    public void ObserveEndOfStream()
    {
        if (Stage != RagTerminalStage.DoneSeen)
        {
            Invalidate("incomplete_end_of_stream");
        }
    }

    public void ObserveTransportFailure() => Invalidate("transport_failure");

    public void ObserveCancellation() => Invalidate("stream_cancelled");

    private bool HasMatchingRequestId(JsonElement payload)
        => payload.TryGetProperty("request_id", out JsonElement requestIdProperty)
           && requestIdProperty.ValueKind == JsonValueKind.String
           && string.Equals(requestIdProperty.GetString(), backendRequestId, StringComparison.Ordinal);

    private static bool HasValidCompletionShape(JsonElement payload)
    {
        if (!payload.TryGetProperty("sources", out JsonElement sources) || sources.ValueKind != JsonValueKind.Array)
            return false;
        if (!payload.TryGetProperty("suggested_questions", out JsonElement suggestions) || suggestions.ValueKind != JsonValueKind.Array)
            return false;
        if (suggestions.EnumerateArray().Any(item => item.ValueKind != JsonValueKind.String))
            return false;
        if (!payload.TryGetProperty("resolved_intents", out JsonElement resolvedIntents)
            || resolvedIntents.ValueKind != JsonValueKind.Array
            || resolvedIntents.EnumerateArray().Any(item => item.ValueKind != JsonValueKind.String))
            return false;
        if (!HasValidSourceLineage(sources, suggestions, payload)) return false;
        if (!HasNullableBoolean(payload, "grounded")) return false;
        if (!HasNullableUnitScore(payload, "grounding_score")) return false;
        if (!HasNullableString(payload, "fallback_reason")) return false;
        return true;
    }

    private static bool HasValidSourceLineage(
        JsonElement sources,
        JsonElement suggestions,
        JsonElement payload)
    {
        var transportedSourceRefs = new HashSet<string>(StringComparer.Ordinal);
        foreach (JsonElement source in sources.EnumerateArray())
        {
            if (source.ValueKind != JsonValueKind.Object
                || !source.TryGetProperty("source_ref", out JsonElement sourceRef)
                || sourceRef.ValueKind != JsonValueKind.String
                || !IsSha256Reference(sourceRef.GetString()))
                return false;
            transportedSourceRefs.Add(sourceRef.GetString()!);
        }

        if (!payload.TryGetProperty("suggested_question_details", out JsonElement details)
            || details.ValueKind != JsonValueKind.Array)
            return false;
        string[] suggestionTexts = suggestions.EnumerateArray()
            .Select(item => item.GetString()!)
            .ToArray();
        var detailedTexts = new List<string>();
        foreach (JsonElement detail in details.EnumerateArray())
        {
            if (detail.ValueKind != JsonValueKind.Object
                || !detail.TryGetProperty("question", out JsonElement question)
                || question.ValueKind != JsonValueKind.String
                || string.IsNullOrWhiteSpace(question.GetString())
                || !detail.TryGetProperty("source_refs", out JsonElement sourceRefs)
                || sourceRefs.ValueKind != JsonValueKind.Array)
                return false;
            string[] refs = sourceRefs.EnumerateArray()
                .Where(item => item.ValueKind == JsonValueKind.String)
                .Select(item => item.GetString()!)
                .ToArray();
            if (refs.Length == 0
                || refs.Length != sourceRefs.GetArrayLength()
                || refs.Any(reference => !transportedSourceRefs.Contains(reference)))
                return false;
            detailedTexts.Add(question.GetString()!);
        }
        return suggestionTexts.SequenceEqual(detailedTexts, StringComparer.Ordinal);
    }

    private static bool IsSha256Reference(string? value)
        => value is { Length: 71 }
           && value.StartsWith("sha256:", StringComparison.Ordinal)
           && value.AsSpan(7).ToString().All(Uri.IsHexDigit);

    private static bool HasNullableBoolean(JsonElement payload, string name)
        => payload.TryGetProperty(name, out JsonElement property)
           && property.ValueKind is JsonValueKind.True or JsonValueKind.False or JsonValueKind.Null;

    private static bool HasNullableString(JsonElement payload, string name)
        => payload.TryGetProperty(name, out JsonElement property)
           && property.ValueKind is JsonValueKind.String or JsonValueKind.Null;

    private static bool HasNullableUnitScore(JsonElement payload, string name)
    {
        if (!payload.TryGetProperty(name, out JsonElement property)) return false;
        if (property.ValueKind == JsonValueKind.Null) return true;
        return property.ValueKind == JsonValueKind.Number
               && property.TryGetDouble(out double score)
               && !double.IsNaN(score)
               && !double.IsInfinity(score)
               && score is >= 0 and <= 1;
    }

    private void Invalidate(string reason)
    {
        Stage = RagTerminalStage.Invalid;
        FailureReason ??= reason;
    }
}
