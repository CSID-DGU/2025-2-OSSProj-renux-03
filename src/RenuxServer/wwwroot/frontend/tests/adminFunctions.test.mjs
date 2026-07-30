import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildChatPreview,
  getEditableFields,
  toDepartmentKnowledge,
  toPendingAnswerReview,
} from '../src/admin/adminData.ts'
import { getCsvFilename, triggerBlobDownload } from '../src/admin/csvDownload.ts'
import {
  getApiErrorMessage,
  getRequestStatusLabel,
  getRequestStatusTone,
  getSystemStatusTone,
  formatRatio,
} from '../src/admin/format.ts'

const knowledgePayload = {
  question: '졸업논문 제출 기한은?',
  answer: '학과 공지를 확인하세요.',
  category: '컴퓨터공학과',
  requester: '학생회 담당자',
}

const pendingItem = {
  id: 17,
  source_type: 'custom_knowledge',
  data: JSON.stringify(knowledgePayload),
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
    sourceType: 'custom_knowledge',
    reviewNote: null,
    reviewedBy: null,
    reviewedAt: null,
    disabled: false,
    raw: knowledgePayload,
  })
})

test('처리 메모와 노출 중단 상태를 검수 화면 계약에 그대로 싣는다', () => {
  const mapped = toPendingAnswerReview({
    ...pendingItem,
    status: 'rejected',
    review_note: '일정이 학사일정과 충돌합니다.',
    reviewed_by: '총학생회 담당자',
    reviewed_at: '2026-07-20T09:30:00+09:00',
    disabled: true,
  })

  assert.equal(mapped.reviewNote, '일정이 학사일정과 충돌합니다.')
  assert.equal(mapped.reviewedBy, '총학생회 담당자')
  assert.equal(mapped.reviewedAt, '2026-07-20T09:30:00+09:00')
  assert.equal(mapped.disabled, true)
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
  assert.deepEqual(mapped.raw, {})
})

test('반려 항목은 서버가 준 처리 메모를 반려 사유로 노출한다', () => {
  const mapped = toDepartmentKnowledge({
    ...pendingItem,
    status: 'rejected',
    review_note: '날짜를 확인해주세요.',
  })

  assert.equal(mapped.status, 'REJECTED')
  assert.equal(mapped.rejectionReason, '날짜를 확인해주세요.')
})

test('등록 유형마다 편집 가능한 필드가 다르다', () => {
  assert.deepEqual(
    getEditableFields('custom_knowledge').map((field) => field.key),
    ['question', 'answer'],
  )
  assert.deepEqual(
    getEditableFields('event').map((field) => field.key),
    ['title', 'start_date', 'end_date', 'location', 'description'],
  )
  assert.deepEqual(
    getEditableFields('announcement').map((field) => field.key),
    ['title', 'date', 'category', 'content'],
  )
  // 알 수 없는 유형은 편집 폼을 렌더링하지 않는다(빈 배열).
  assert.deepEqual(getEditableFields('unknown_type'), [])
})

test('챗봇 미리보기는 유형별로 문장을 재구성하고 빈 값은 건너뛴다', () => {
  assert.equal(
    buildChatPreview('custom_knowledge', knowledgePayload),
    'Q. 졸업논문 제출 기한은?\n\nA. 학과 공지를 확인하세요.',
  )

  assert.equal(
    buildChatPreview('event', {
      title: '홈커밍데이',
      start_date: '2026-09-01',
      end_date: '2026-09-01',
      location: '',
      department: '통계학과',
      description: '졸업생 초청 행사',
    }),
    // 시작일과 종료일이 같으면 기간을 한 번만, 빈 장소는 줄 자체를 생략한다.
    '홈커밍데이 행사 안내\n일시: 2026-09-01\n주관: 통계학과\n졸업생 초청 행사',
  )

  assert.equal(
    buildChatPreview('announcement', {
      title: '장학금 신청 안내',
      date: '2026-08-01',
      category: '장학',
      department: '경영학과',
      content: '8월 15일까지 신청하세요.',
    }),
    '장학금 신청 안내\n게시일: 2026-08-01\n분류: 장학\n학과: 경영학과\n8월 15일까지 신청하세요.',
  )
})

test('상태 문자열을 라벨과 색 토큰으로 일관되게 변환한다', () => {
  assert.equal(getRequestStatusLabel('approved_manually'), '승인됨')
  assert.equal(getRequestStatusLabel('rejected'), '반려됨')
  assert.equal(getRequestStatusLabel('pending'), '대기 중')
  assert.equal(getRequestStatusTone('approved'), 'success')
  assert.equal(getRequestStatusTone('rejected'), 'danger')
  assert.equal(getSystemStatusTone('degraded'), 'warning')
  assert.equal(getSystemStatusTone(undefined), 'pending')
})

test('분모가 0인 비율은 0%가 아니라 표본 없음으로 표시한다', () => {
  assert.equal(formatRatio(3, 4), '75%')
  assert.equal(formatRatio(0, 4), '0%')
  assert.equal(formatRatio(0, 0), '-')
  assert.equal(formatRatio(null, 10), '-')
})

test('API 오류에서 서버 메시지를 우선 사용하고 없으면 대체 문구를 쓴다', () => {
  const withMessage = Object.assign(new Error('요청이 실패했습니다.'), {
    details: { message: '본인 학과의 요청만 취소할 수 있습니다.' },
  })
  assert.equal(getApiErrorMessage(withMessage, '실패'), '본인 학과의 요청만 취소할 수 있습니다.')

  const withDetail = Object.assign(new Error('요청이 실패했습니다.'), {
    details: { detail: 'RAG 서비스 연결에 실패했습니다.' },
  })
  assert.equal(getApiErrorMessage(withDetail, '실패'), 'RAG 서비스 연결에 실패했습니다.')

  assert.equal(getApiErrorMessage({}, '기본 문구'), '기본 문구')
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
