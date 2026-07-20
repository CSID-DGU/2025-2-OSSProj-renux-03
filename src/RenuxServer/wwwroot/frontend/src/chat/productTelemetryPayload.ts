export type SuggestionTelemetryPayload = {
  eventType: 'suggestion_shown' | 'suggestion_clicked'
  requestId: string
  suggestionIndex?: number
  suggestionCount?: number
}

export const buildSuggestionTelemetryPayload = (
  eventType: SuggestionTelemetryPayload['eventType'],
  requestId: string,
  value: number,
): SuggestionTelemetryPayload => eventType === 'suggestion_shown'
  ? { eventType, requestId, suggestionCount: value }
  : { eventType, requestId, suggestionIndex: value }
