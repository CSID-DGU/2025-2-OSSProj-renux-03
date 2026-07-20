import assert from 'node:assert/strict'
import test from 'node:test'

import {
  finalizeStoppedAssistant,
  normalizeAssistantRuns,
  parseGuestChatRecords,
  prepareRegeneration,
  readGuestChatRecords,
  resolveGuestChatRoute,
  toChatPath,
  updateGuestChatMessages,
  writeGuestChatRecords,
} from '../src/chat/chatState.ts'

const question = {
  id: 'question-1',
  chatId: 'chat-1',
  isAsk: true,
  content: '장학 공지 알려줘',
  createdTime: '2026-07-19T00:00:00Z',
}

const firstAnswer = {
  id: 'answer-1',
  chatId: 'chat-1',
  isAsk: false,
  content: '이전 답변',
  createdTime: '2026-07-19T00:00:01Z',
}

const latestAnswer = {
  ...firstAnswer,
  id: 'answer-2',
  content: '최신 답변\n\n## 확인된 정보 1',
  createdTime: '2026-07-19T00:00:02Z',
}

test('연속 assistant 응답은 최신값만 남기고 Markdown을 보존한다', () => {
  assert.deepEqual(normalizeAssistantRuns([question, firstAnswer, latestAnswer]), [question, latestAnswer])
})

test('로드된 빈 assistant 행은 영구 타이핑 표시 대신 완료되지 않은 상태로 정규화한다', () => {
  const emptyAnswer = { ...latestAnswer, content: '' }
  const normalized = normalizeAssistantRuns([question, firstAnswer, emptyAnswer])

  assert.equal(normalized.length, 2)
  assert.equal(normalized[1].id, emptyAnswer.id)
  assert.equal(normalized[1].content, '답변 생성이 완료되지 않았습니다.')
})

test('기존 배열형 게스트 저장 포맷을 읽고 known/unknown URL을 구분한다', () => {
  const records = parseGuestChatRecords(JSON.stringify([{ id: 'chat-1', title: '장학 상담' }]))

  assert.equal(resolveGuestChatRoute(undefined, records).kind, 'root')
  assert.equal(resolveGuestChatRoute('chat-1', records).kind, 'known')
  assert.deepEqual(resolveGuestChatRoute('other-device-chat', records), {
    kind: 'unknown',
    chatId: 'other-device-chat',
  })
  assert.equal(toChatPath('chat-1'), '/chat/chat-1')
})

test('storage 읽기가 차단되면 게스트 목록 대신 빈 배열을 반환한다', () => {
  const throwingStorage = {
    getItem() {
      throw new Error('storage disabled')
    },
    setItem() {},
  }

  assert.deepEqual(readGuestChatRecords(throwingStorage), [])
})

test('storage 쓰기는 quota 예외를 false로 바꾸고 성공 시 true를 반환한다', () => {
  const throwingStorage = {
    getItem() {
      return null
    },
    setItem() {
      throw new Error('quota exceeded')
    },
  }
  let stored = ''
  const workingStorage = {
    getItem() {
      return stored
    },
    setItem(_key, value) {
      stored = value
    },
  }

  assert.equal(writeGuestChatRecords(throwingStorage, [{ id: 'chat-1' }]), false)
  assert.equal(writeGuestChatRecords(workingStorage, [{ id: 'chat-1' }]), true)
  assert.equal(stored, JSON.stringify([{ id: 'chat-1' }]))
})

test('게스트 메시지 갱신도 연속 assistant 최신값으로 정규화한다', () => {
  const records = [{ id: 'chat-1', title: '장학 상담' }]
  const updated = updateGuestChatMessages(
    records,
    'chat-1',
    [question, firstAnswer, latestAnswer],
    '2026-07-19T01:00:00Z',
  )

  assert.deepEqual(updated[0].messages, [question, latestAnswer])
  assert.equal(updated[0].updatedAt, '2026-07-19T01:00:00Z')
})

test('재생성은 질문을 추가하지 않고 기존 assistant 슬롯만 비운다', () => {
  const prepared = prepareRegeneration(
    [question, latestAnswer],
    latestAnswer.id,
    '2026-07-19T02:00:00Z',
  )

  assert.ok(prepared)
  assert.equal(prepared.messages.length, 2)
  assert.equal(prepared.question.id, question.id)
  assert.equal(prepared.assistant.id, latestAnswer.id)
  assert.equal(prepared.assistant.content, '')
  assert.equal(prepared.messages.filter((message) => message.isAsk).length, 1)
})

test('스트림 중단은 부분 답변을 유지하고 빈 슬롯만 중단 문구로 바꾼다', () => {
  const partial = { ...latestAnswer, content: '받은 부분' }
  assert.equal(finalizeStoppedAssistant([question, partial], partial.id)[1].content, '받은 부분')

  const empty = { ...latestAnswer, content: '' }
  assert.equal(finalizeStoppedAssistant([question, empty], empty.id)[1].content, '답변 생성을 중단했습니다.')
})
