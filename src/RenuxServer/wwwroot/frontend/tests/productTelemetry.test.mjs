import assert from 'node:assert/strict'
import test from 'node:test'

import { buildSuggestionTelemetryPayload } from '../src/chat/productTelemetryPayload.ts'

test('추천질문 계측 payload는 allowlist 필드만 포함하고 질문 본문을 싣지 않는다', () => {
  const shown = buildSuggestionTelemetryPayload('suggestion_shown', 'request-1', 3)
  const clicked = buildSuggestionTelemetryPayload('suggestion_clicked', 'request-1', 2)

  assert.deepEqual(shown, {
    eventType: 'suggestion_shown',
    requestId: 'request-1',
    suggestionCount: 3,
  })
  assert.deepEqual(clicked, {
    eventType: 'suggestion_clicked',
    requestId: 'request-1',
    suggestionIndex: 2,
  })

  const forbiddenKeys = ['question', 'answer', 'name', 'studentNumber', 'email', 'ip', 'sessionId']
  for (const payload of [shown, clicked]) {
    for (const key of forbiddenKeys) assert.equal(key in payload, false)
  }
})
