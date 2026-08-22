import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildChatTitle,
  filterChatEntries,
  formatChatTime,
  groupChatEntries,
  resolveDateBucket,
  toChatListEntry,
  toGuestChatListEntry,
} from '../src/chat/chatList.ts'

// 기준 시각을 고정해 '오늘/어제' 판정이 실행 시점에 흔들리지 않게 한다.
const NOW = new Date(2026, 6, 30, 14, 0, 0) // 2026-07-30 14:00 로컬
const at = (year, month, day, hour = 12) => new Date(year, month - 1, day, hour).toISOString()

test('날짜 그룹은 24시간이 아니라 자정 기준으로 끊는다', () => {
  // 오늘 새벽 1시는 13시간 전이지만 '오늘'이어야 한다.
  assert.equal(resolveDateBucket(at(2026, 7, 30, 1), NOW), 'today')
  // 어제 23시는 15시간 전이지만 '어제'여야 한다.
  assert.equal(resolveDateBucket(at(2026, 7, 29, 23), NOW), 'yesterday')
  assert.equal(resolveDateBucket(at(2026, 7, 25), NOW), 'last7')
  assert.equal(resolveDateBucket(at(2026, 7, 1), NOW), 'older')
})

test('미래 시각과 잘못된 값도 그룹 분류에서 안전하게 처리한다', () => {
  // 시계가 앞선 기기에서 만들어진 기록은 미래로 보일 수 있다 — '오늘'로 취급한다.
  assert.equal(resolveDateBucket(at(2026, 8, 5), NOW), 'today')
  assert.equal(resolveDateBucket('', NOW), 'unknown')
  assert.equal(resolveDateBucket('어제', NOW), 'unknown')
})

test('목록 시각은 오늘이면 시각, 어제면 어제, 그 이전이면 날짜로 표시한다', () => {
  assert.match(formatChatTime(at(2026, 7, 30, 9), NOW), /9/)
  assert.equal(formatChatTime(at(2026, 7, 29), NOW), '어제')
  assert.equal(formatChatTime(at(2026, 7, 20), NOW), '7. 20.')
  assert.equal(formatChatTime('', NOW), '')
})

test('서버 대화를 목록 표시 계약으로 변환한다', () => {
  assert.deepEqual(
    toChatListEntry({
      id: 'c1',
      title: '  졸업요건 알려줘  ',
      lastMessage: '졸업요건은 다음과 같이…',
      updatedTime: at(2026, 7, 30, 10),
    }),
    {
      id: 'c1',
      title: '졸업요건 알려줘',
      preview: '졸업요건은 다음과 같이…',
      updatedAt: at(2026, 7, 30, 10),
    },
  )
})

test('제목이 없는 대화는 목록에서 대체 문구를 쓴다', () => {
  const entry = toChatListEntry({ id: 'c2', title: '   ', lastMessage: null, updatedTime: null })
  assert.equal(entry.title, '제목 없음')
  assert.equal(entry.preview, '')
  assert.equal(entry.updatedAt, '')
})

test('게스트 대화는 저장된 마지막 메시지에서 미리보기를 만든다', () => {
  const entry = toGuestChatListEntry({
    id: 'g1',
    title: '학식 질문',
    guestToken: 'tok',
    updatedAt: at(2026, 7, 30, 11),
    messages: [
      { id: 'm1', chatId: 'g1', isAsk: true, content: '오늘 학식 뭐야?', createdTime: at(2026, 7, 30, 10) },
      { id: 'm2', chatId: 'g1', isAsk: false, content: '## 오늘의  학식\n제육덮밥', createdTime: at(2026, 7, 30, 11) },
    ],
  })

  // 마크다운 기호와 연속 공백은 한 줄 미리보기에서 눌러진다.
  assert.equal(entry.preview, '오늘의 학식 제육덮밥')
  assert.equal(entry.guestToken, 'tok')
})

test('updatedAt이 없는 게스트 대화는 마지막 메시지 시각으로 대체한다', () => {
  const entry = toGuestChatListEntry({
    id: 'g2',
    title: '이전 빌드 기록',
    messages: [
      { id: 'm1', chatId: 'g2', isAsk: true, content: '질문', createdTime: at(2026, 7, 28) },
    ],
  })
  assert.equal(entry.updatedAt, at(2026, 7, 28))
})

test('검색은 제목과 미리보기 모두를 대상으로 한다', () => {
  const entries = [
    { id: 'a', title: '졸업요건', preview: '학점 기준 안내', updatedAt: '' },
    { id: 'b', title: '학식 문의', preview: '제육덮밥이 나옵니다', updatedAt: '' },
  ]

  assert.deepEqual(filterChatEntries(entries, '졸업').map((e) => e.id), ['a'])
  // 제목을 기억하지 못해도 내용으로 찾을 수 있어야 한다.
  assert.deepEqual(filterChatEntries(entries, '제육').map((e) => e.id), ['b'])
  assert.deepEqual(filterChatEntries(entries, '   ').map((e) => e.id), ['a', 'b'])
  assert.deepEqual(filterChatEntries(entries, '없는말'), [])
})

test('그룹은 최근 순으로 정렬되고 빈 그룹은 만들지 않는다', () => {
  const groups = groupChatEntries([
    { id: 'old', title: '오래된 대화', preview: '', updatedAt: at(2026, 7, 1) },
    { id: 'today', title: '오늘 대화', preview: '', updatedAt: at(2026, 7, 30, 9) },
    { id: 'yesterday', title: '어제 대화', preview: '', updatedAt: at(2026, 7, 29) },
    { id: 'todayLater', title: '오늘 나중 대화', preview: '', updatedAt: at(2026, 7, 30, 13) },
  ], NOW)

  assert.deepEqual(groups.map((group) => group.label), ['오늘', '어제', '이전'])
  // '지난 7일'에 해당하는 항목이 없으므로 헤더만 남는 그룹이 생기지 않는다.
  assert.equal(groups.some((group) => group.bucket === 'last7'), false)
  // 같은 그룹 안에서도 최근 활동이 위로 온다.
  assert.deepEqual(groups[0].entries.map((entry) => entry.id), ['todayLater', 'today'])
})

test('자동 제목은 단어 경계에서 끊어 읽을 수 있게 만든다', () => {
  assert.equal(buildChatTitle('졸업요건 알려줘'), '졸업요건 알려줘')
  // 여러 공백은 하나로 정리한다.
  assert.equal(buildChatTitle('  졸업요건   알려줘 '), '졸업요건 알려줘')
  assert.equal(buildChatTitle(''), '새 대화')

  const long = '동국대학교 통계학과 3학년 2학기 수강 가능한 전공선택 과목을 알려줘'
  const title = buildChatTitle(long)
  const body = title.slice(0, -1) // '…' 제외

  assert.ok(title.endsWith('…'))
  assert.ok(title.length <= 29, `제목이 너무 깁니다: ${title}`)
  // 원문의 앞부분을 그대로 보존한다.
  assert.ok(long.startsWith(body), `원문 접두사가 아닙니다: ${body}`)
  // 단어 중간이 아니라 공백 경계에서 끊긴다 = 끊긴 지점 다음 문자가 공백이다.
  assert.equal(long[body.length], ' ', `단어 중간에서 끊겼습니다: ${body}`)
})

test('공백 없이 긴 한 단어는 그대로 잘라 제목 길이를 지킨다', () => {
  const title = buildChatTitle('가'.repeat(60))
  assert.equal(title, `${'가'.repeat(28)}…`)
})
