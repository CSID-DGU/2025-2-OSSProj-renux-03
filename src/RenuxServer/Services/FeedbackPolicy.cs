namespace RenuxServer.Services;

public enum FeedbackDecision
{
    Accept,
    Duplicate,
    Conflict,
}

public static class FeedbackPolicy
{
    // First accepted rating wins. Replaying the same rating is idempotent;
    // attempting to flip it is rejected and never forwarded upstream.
    public static FeedbackDecision Decide(int? existingRating, int requestedRating)
    {
        if (requestedRating is not (1 or -1)) throw new ArgumentOutOfRangeException(nameof(requestedRating));
        if (existingRating is null) return FeedbackDecision.Accept;
        return existingRating == requestedRating ? FeedbackDecision.Duplicate : FeedbackDecision.Conflict;
    }
}
