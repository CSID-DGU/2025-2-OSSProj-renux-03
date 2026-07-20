import assert from 'node:assert/strict'
import test from 'node:test'

import { toDepartmentKnowledge, toPendingAnswerReview } from '../src/admin/adminData.ts'
import { getCsvFilename, triggerBlobDownload } from '../src/admin/csvDownload.ts'

const pendingItem = {
  id: 17,
  source_type: 'custom_knowledge',
  data: JSON.stringify({
    question: '졸업논문 제출 기한은?',
    answer: '학과 공지를 확인하세요.',
    category: '컴퓨터공학과',
    requester: '학생회 담당자',
  }),
  status: 'pending',
  created_at: '2026-07-19T10:00:00+09:00',
}

test('승인 대기 API 항목을 관리자 검수 화면 계약으로 변환한다', () => {
  assert.deepEqual(toPendingAnswerReview(pendingItem), {
    id: '17',
    departmentName: '컴퓨터공학과',
    submittedAt: '2026-07-19T10:00:00+09:00',
    handler: '학생회 담당자',
    question: '졸업논문 제출 기한은?',
    answer: '학과 공지를 확인하세요.',
    status: 'pending',
  })
})

test('승인 상태와 손상된 data도 학과 관리자 목록에서 안전하게 처리한다', () => {
  const mapped = toDepartmentKnowledge({
    ...pendingItem,
    id: 18,
    data: '{broken-json',
    status: 'approved_manually',
  })

  assert.equal(mapped.id, '18')
  assert.equal(mapped.title, '질문 없음')
  assert.equal(mapped.content, '')
  assert.equal(mapped.status, 'APPROVED')
})

test('CSV 응답의 일반 파일명과 UTF-8 확장 파일명을 해석한다', () => {
  const now = new Date(2026, 6, 19, 14, 5, 6)
  assert.equal(
    getCsvFilename('attachment; filename="rag_evaluation_logs_20260719.csv"', now),
    'rag_evaluation_logs_20260719.csv',
  )
  assert.equal(
    getCsvFilename("attachment; filename*=UTF-8''%EB%8F%99%EB%98%91%EC%9D%B4_%EB%A1%9C%EA%B7%B8.csv", now),
    '동똑이_로그.csv',
  )
  assert.equal(getCsvFilename(null, now), 'rag_evaluation_logs_20260719_140506.csv')
})

test('Blob 다운로드 성공은 다음 task까지 URL을 유지하고 예약된 정리에서 해제한다', () => {
  const events = []
  let scheduledCleanup
  const anchor = {
    href: '',
    download: '',
    style: {},
    click() { events.push('click') },
    remove() { events.push('remove') },
  }
  const environment = {
    createObjectUrl() { events.push('create'); return 'blob:test' },
    revokeObjectUrl(url) { events.push(`revoke:${url}`) },
    createAnchor() { return anchor },
    appendAnchor() { events.push('append') },
    scheduleCleanup(cleanup) { events.push('schedule'); scheduledCleanup = cleanup },
  }

  triggerBlobDownload(new Blob(['csv']), 'logs.csv', environment)
  assert.deepEqual(events, ['create', 'append', 'click', 'schedule'])
  assert.equal(anchor.download, 'logs.csv')
  assert.equal(typeof scheduledCleanup, 'function')

  scheduledCleanup()
  assert.deepEqual(events, ['create', 'append', 'click', 'schedule', 'remove', 'revoke:blob:test'])

  // cleanup이 중복 실행되어도 이미 해제한 URL을 다시 건드리지 않는다.
  scheduledCleanup()
  assert.deepEqual(events, ['create', 'append', 'click', 'schedule', 'remove', 'revoke:blob:test'])
})

test('Blob 다운로드의 append/click 실패는 URL과 임시 링크를 즉시 정리한다', () => {
  const failureEvents = []
  const failingAnchor = {
    href: '',
    download: '',
    style: {},
    click() { throw new Error('download blocked') },
    remove() { failureEvents.push('remove') },
  }
  assert.throws(() => triggerBlobDownload(new Blob(['csv']), 'logs.csv', {
    createObjectUrl() { return 'blob:failure' },
    revokeObjectUrl(url) { failureEvents.push(`revoke:${url}`) },
    createAnchor() { return failingAnchor },
    appendAnchor() {},
    scheduleCleanup() { failureEvents.push('schedule') },
  }), /download blocked/)
  assert.deepEqual(failureEvents, ['remove', 'revoke:blob:failure'])

  const appendFailureEvents = []
  const appendFailureAnchor = {
    href: '',
    download: '',
    style: {},
    click() { appendFailureEvents.push('click') },
    remove() { appendFailureEvents.push('remove') },
  }
  assert.throws(() => triggerBlobDownload(new Blob(['csv']), 'logs.csv', {
    createObjectUrl() { return 'blob:append-failure' },
    revokeObjectUrl(url) { appendFailureEvents.push(`revoke:${url}`) },
    createAnchor() { return appendFailureAnchor },
    appendAnchor() { throw new Error('append blocked') },
    scheduleCleanup() { appendFailureEvents.push('schedule') },
  }), /append blocked/)
  assert.deepEqual(appendFailureEvents, ['remove', 'revoke:blob:append-failure'])
})
