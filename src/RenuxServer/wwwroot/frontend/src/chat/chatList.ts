import type { GuestChatRecord } from './chatState'
import type { ActiveChat } from '../types/chat'

/**
 * 사이드바 대화 목록의 정렬·검색·날짜 그룹.
 * 서버 대화와 게스트 대화가 서로 다른 필드를 갖고 있어(서버=lastMessage, 게스트=messages)
 * 여기서 한 가지 표시 계약으로 좁힌 뒤 렌더링한다.
 */

export interface ChatListEntry {
  id: string
  title: string
  preview: string
  /** ISO 문자열. 알 수 없으면 빈 문자열. */
  updatedAt: string
  guestToken?: string
}

export type ChatDateBucket = 'today' | 'yesterday' | 'last7' | 'older' | 'unknown'

export interface ChatListGroup {
  bucket: ChatDateBucket
  label: string
  entries: ChatListEntry[]
}

const BUCKET_LABELS: Record<ChatDateBucket, string> = {
  today: '오늘',
  yesterday: '어제',
  last7: '지난 7일',
  older: '이전',
  unknown: '날짜 미상',
}

/** 그룹 표시 순서. 최근 활동이 위로 온다. */
const BUCKET_ORDER: ChatDateBucket[] = ['today', 'yesterday', 'last7', 'older', 'unknown']

const startOfLocalDay = (date: Date) =>
  new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()

/**
 * 활동 시각을 날짜 그룹으로 분류한다.
 * 24시간 단위가 아니라 '자정' 기준으로 끊어야 사용자가 말하는 "어제"와 일치한다.
 */
export const resolveDateBucket = (isoValue: string, now = new Date()): ChatDateBucket => {
  if (!isoValue) return 'unknown'
  const date = new Date(isoValue)
  if (Number.isNaN(date.getTime())) return 'unknown'

  const todayStart = startOfLocalDay(now)
  const targetStart = startOfLocalDay(date)
  const dayDiff = Math.round((todayStart - targetStart) / 86_400_000)

  if (dayDiff <= 0) return 'today'
  if (dayDiff === 1) return 'yesterday'
  if (dayDiff <= 7) return 'last7'
  return 'older'
}

/** 목록 행의 상대 시각. 오늘은 시각, 어제는 '어제', 그 이전은 날짜. */
export const formatChatTime = (isoValue: string, now = new Date()): string => {
  if (!isoValue) return ''
  const date = new Date(isoValue)
  if (Number.isNaN(date.getTime())) return ''

  const bucket = resolveDateBucket(isoValue, now)
  if (bucket === 'today') {
    return new Intl.DateTimeFormat('ko-KR', { hour: 'numeric', minute: '2-digit' }).format(date)
  }
  if (bucket === 'yesterday') return '어제'
  return new Intl.DateTimeFormat('ko-KR', { month: 'numeric', day: 'numeric' }).format(date)
}

const FALLBACK_TITLE = '제목 없음'

/** 목록 한 줄에 들어갈 길이로 미리보기를 줄인다(게스트 대화는 서버 요약이 없다). */
const PREVIEW_LENGTH = 70

const flattenPreview = (content: string) => {
  const flattened = content.split(/[\s#*_`>[\]-]+/u).filter(Boolean).join(' ')
  if (flattened.length <= PREVIEW_LENGTH) return flattened
  return `${flattened.slice(0, PREVIEW_LENGTH)}…`
}

/** 서버 대화를 표시 계약으로 변환한다. */
export const toChatListEntry = (chat: ActiveChat): ChatListEntry => ({
  id: chat.id,
  title: chat.title?.trim() || FALLBACK_TITLE,
  preview: chat.lastMessage?.trim() ?? '',
  updatedAt: chat.updatedTime ?? '',
  ...(chat.guestToken ? { guestToken: chat.guestToken } : {}),
})

/**
 * 게스트 대화를 표시 계약으로 변환한다.
 * 미리보기는 저장된 메시지 마지막 항목에서 직접 만든다.
 */
export const toGuestChatListEntry = (record: GuestChatRecord): ChatListEntry => {
  const messages = record.messages ?? []
  const last = messages.at(-1)
  const lastTime = last?.createdTime
  const resolvedUpdatedAt = record.updatedAt
    ?? (typeof lastTime === 'string'
      ? lastTime
      : typeof lastTime === 'number'
        ? new Date(lastTime).toISOString()
        : '')

  return {
    id: record.id,
    title: record.title?.trim() || FALLBACK_TITLE,
    preview: last?.content ? flattenPreview(last.content) : '',
    updatedAt: resolvedUpdatedAt,
    ...(record.guestToken ? { guestToken: record.guestToken } : {}),
  }
}

/** 제목과 미리보기 모두를 검색한다 — 제목을 기억하지 못해도 내용으로 찾을 수 있게. */
export const filterChatEntries = (entries: ChatListEntry[], query: string): ChatListEntry[] => {
  const term = query.trim().toLowerCase()
  if (!term) return entries
  return entries.filter((entry) =>
    entry.title.toLowerCase().includes(term) || entry.preview.toLowerCase().includes(term),
  )
}

/**
 * 활동 시각 내림차순으로 정렬한 뒤 날짜 그룹으로 묶는다.
 * 빈 그룹은 만들지 않으므로 헤더만 남는 일이 없다.
 */
export const groupChatEntries = (entries: ChatListEntry[], now = new Date()): ChatListGroup[] => {
  const sorted = [...entries].sort((a, b) => {
    const aTime = a.updatedAt ? new Date(a.updatedAt).getTime() : 0
    const bTime = b.updatedAt ? new Date(b.updatedAt).getTime() : 0
    if (Number.isNaN(aTime) && Number.isNaN(bTime)) return 0
    return (Number.isNaN(bTime) ? 0 : bTime) - (Number.isNaN(aTime) ? 0 : aTime)
  })

  const byBucket = new Map<ChatDateBucket, ChatListEntry[]>()
  sorted.forEach((entry) => {
    const bucket = resolveDateBucket(entry.updatedAt, now)
    const list = byBucket.get(bucket)
    if (list) list.push(entry)
    else byBucket.set(bucket, [entry])
  })

  return BUCKET_ORDER.flatMap((bucket) => {
    const list = byBucket.get(bucket)
    if (!list || list.length === 0) return []
    return [{ bucket, label: BUCKET_LABELS[bucket], entries: list }]
  })
}

/**
 * 첫 질문으로 대화 제목을 만든다.
 * 앞 20자를 그대로 자르면 단어가 끊겨 읽기 어려우므로 공백 경계에서 끊는다.
 */
export const buildChatTitle = (question: string, maxLength = 28): string => {
  const normalized = question.trim().replace(/\s+/gu, ' ')
  if (!normalized) return '새 대화'
  if (normalized.length <= maxLength) return normalized

  const sliced = normalized.slice(0, maxLength)
  const lastSpace = sliced.lastIndexOf(' ')
  // 공백이 너무 앞에 있으면(한 단어가 긴 경우) 그냥 자른다.
  const cut = lastSpace >= Math.floor(maxLength * 0.6) ? sliced.slice(0, lastSpace) : sliced
  return `${cut.trimEnd()}…`
}
