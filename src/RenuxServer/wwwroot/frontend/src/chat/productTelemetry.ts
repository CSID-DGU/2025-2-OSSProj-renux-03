import { apiFetch } from '../api/client'
import {
  buildSuggestionTelemetryPayload,
  type SuggestionTelemetryPayload,
} from './productTelemetryPayload'

const emittedEvents = new Set<string>()

export const trackSuggestionEvent = (
  eventType: SuggestionTelemetryPayload['eventType'],
  requestId: string,
  value: number,
) => {
  if (!requestId) return

  const dedupeKey = `${eventType}:${requestId}:${eventType === 'suggestion_clicked' ? value : 'all'}`
  if (emittedEvents.has(dedupeKey)) return
  emittedEvents.add(dedupeKey)

  // Telemetry is best effort. A failed analytics request must never delay or
  // cancel the user's next chat action.
  void apiFetch('/telemetry/events', {
    method: 'POST',
    json: buildSuggestionTelemetryPayload(eventType, requestId, value),
  }).catch((error) => {
    console.debug('Suggestion telemetry was not recorded', error)
  })
}
