using System.Text.Json;

namespace RenuxServer.Services;

public static class RagStreamContract
{
    public const int DefaultServiceTimeoutSeconds = 300;

    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    public static TimeSpan ResolveServiceTimeout(IConfiguration configuration)
    {
        int timeoutSeconds =
            configuration.GetValue<int?>("RagServiceTimeoutSeconds")
            ?? configuration.GetValue<int?>("RAG_SERVICE_TIMEOUT_SECONDS")
            ?? DefaultServiceTimeoutSeconds;

        return TimeSpan.FromSeconds(timeoutSeconds > 0 ? timeoutSeconds : DefaultServiceTimeoutSeconds);
    }

    public static IReadOnlyList<string> CreateGracefulFallbackPayloads(
        string requestId,
        string? fallbackReason,
        string fallbackText)
    {
        return
        [
            JsonSerializer.Serialize(
                new
                {
                    type = "metadata",
                    request_id = requestId,
                    sources = Array.Empty<object>(),
                    fallback_triggered = true,
                    fallback_reason = fallbackReason,
                },
                JsonOptions),
            JsonSerializer.Serialize(
                new { type = "text", request_id = requestId, content = fallbackText },
                JsonOptions),
            JsonSerializer.Serialize(
                new
                {
                    type = "completion",
                    request_id = requestId,
                    sources = Array.Empty<object>(),
                    suggested_questions = Array.Empty<string>(),
                    suggested_question_details = Array.Empty<object>(),
                    resolved_intents = Array.Empty<string>(),
                    grounded = (bool?)null,
                    grounding_score = (double?)null,
                    fallback_triggered = true,
                    fallback_reason = fallbackReason,
                },
                JsonOptions),
            JsonSerializer.Serialize(
                new { type = "done", request_id = requestId },
                JsonOptions),
        ];
    }
}
