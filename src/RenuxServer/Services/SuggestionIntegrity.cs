namespace RenuxServer.Services;

public static class SuggestionIntegrity
{
    public static bool Matches(
        string eventType,
        int actualSuggestionCount,
        int? claimedSuggestionCount,
        int? claimedSuggestionIndex)
        => actualSuggestionCount is >= 0 and <= 10
           && eventType switch
           {
               ProductEventTypes.SuggestionShown => claimedSuggestionCount == actualSuggestionCount
                                                      && claimedSuggestionIndex is null,
               ProductEventTypes.SuggestionClicked => claimedSuggestionCount is null
                                                        && claimedSuggestionIndex is >= 0
                                                        && claimedSuggestionIndex < actualSuggestionCount,
               _ => false,
           };
}
