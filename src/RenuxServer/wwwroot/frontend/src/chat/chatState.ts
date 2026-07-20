import type { ChatSource } from '../components/chat/SourceCards'
import type { ActiveChat } from '../types/chat'

export const GUEST_CHAT_STORAGE_KEY = 'renux-guest-chats'

export interface ChatViewMessage {
  id: string
  chatId: string
  isAsk: boolean
  content: string
  createdTime: string | number
  sources?: ChatSource[] | null
  requestId?: string
  isFallback?: boolean
  fallbackReason?: string | null
  suggestedQuestions?: string[]
  grounded?: boolean
  groundingScore?: number
}

export interface GuestChatRecord extends ActiveChat {
  messages?: ChatViewMessage[]
  updatedAt?: string
}

export interface GuestChatStorage {
  getItem: (key: string) => string | null
  setItem: (key: string, value: string) => void
}

export type GuestChatRoute =
  | { kind: 'root' }
  | { kind: 'known'; chat: GuestChatRecord }
  | { kind: 'unknown'; chatId: string }

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null

const isChatMessage = (value: unknown): value is ChatViewMessage =>
  isObject(value)
  && typeof value.id === 'string'
  && typeof value.chatId === 'string'
  && typeof value.isAsk === 'boolean'
  && typeof value.content === 'string'
  && (typeof value.createdTime === 'string' || typeof value.createdTime === 'number')

/**
 * A regeneration currently appends another assistant row on the server.
 * Treat a consecutive run of assistant rows as one UI slot and keep its latest value.
 */
export const normalizeAssistantRuns = (messages: ChatViewMessage[]): ChatViewMessage[] => {
  const normalized: ChatViewMessage[] = []

  for (const originalMessage of messages) {
    const message = !originalMessage.isAsk && originalMessage.content.trim().length === 0
      ? { ...originalMessage, content: '답변 생성이 완료되지 않았습니다.' }
      : originalMessage
    const previous = normalized.at(-1)
    if (!message.isAsk && previous && !previous.isAsk) {
      normalized[normalized.length - 1] = message
    } else {
      normalized.push(message)
    }
  }

  return normalized
}

const normalizeGuestRecord = (value: unknown): GuestChatRecord | null => {
  if (!isObject(value) || typeof value.id !== 'string') return null

  const rawMessages = Array.isArray(value.messages) ? value.messages : []
  const messages = normalizeAssistantRuns(rawMessages.filter(isChatMessage))

  return {
    id: value.id,
    title: typeof value.title === 'string' || value.title === null ? value.title : null,
    organization: isObject(value.organization) || value.organization === null
      ? value.organization as ActiveChat['organization']
      : null,
    ...(messages.length > 0 ? { messages } : {}),
    ...(typeof value.updatedAt === 'string' ? { updatedAt: value.updatedAt } : {}),
  }
}

/** Supports both the legacy top-level array and a future `{ chats: [] }` envelope. */
export const parseGuestChatRecords = (raw: string | null): GuestChatRecord[] => {
  if (!raw) return []

  try {
    const parsed: unknown = JSON.parse(raw)
    const candidates = Array.isArray(parsed)
      ? parsed
      : isObject(parsed) && Array.isArray(parsed.chats)
        ? parsed.chats
        : []

    const seen = new Set<string>()
    return candidates.flatMap((candidate) => {
      const record = normalizeGuestRecord(candidate)
      if (!record || seen.has(record.id)) return []
      seen.add(record.id)
      return [record]
    })
  } catch {
    return []
  }
}

export const readGuestChatRecords = (storage: GuestChatStorage): GuestChatRecord[] => {
  try {
    return parseGuestChatRecords(storage.getItem(GUEST_CHAT_STORAGE_KEY))
  } catch {
    return []
  }
}

export const writeGuestChatRecords = (storage: GuestChatStorage, records: GuestChatRecord[]) => {
  try {
    // Keep the legacy array envelope so older builds can still read the chat list.
    storage.setItem(GUEST_CHAT_STORAGE_KEY, JSON.stringify(records))
    return true
  } catch {
    return false
  }
}

export const upsertGuestChat = (
  records: GuestChatRecord[],
  chat: ActiveChat,
  updatedAt = new Date().toISOString(),
): GuestChatRecord[] => {
  const existing = records.find((record) => record.id === chat.id)
  const next: GuestChatRecord = {
    ...existing,
    ...chat,
    messages: existing?.messages ?? [],
    updatedAt,
  }
  return [next, ...records.filter((record) => record.id !== chat.id)]
}

export const updateGuestChatMessages = (
  records: GuestChatRecord[],
  chatId: string,
  messages: ChatViewMessage[],
  updatedAt = new Date().toISOString(),
): GuestChatRecord[] => {
  const existing = records.find((record) => record.id === chatId)
  if (!existing) return records

  const next: GuestChatRecord = {
    ...existing,
    messages: normalizeAssistantRuns(messages),
    updatedAt,
  }
  return [next, ...records.filter((record) => record.id !== chatId)]
}

export const resolveGuestChatRoute = (
  chatId: string | undefined,
  records: GuestChatRecord[],
): GuestChatRoute => {
  if (!chatId) return { kind: 'root' }
  const chat = records.find((record) => record.id === chatId)
  return chat ? { kind: 'known', chat } : { kind: 'unknown', chatId }
}

export const toChatPath = (chatId: string) => `/chat/${encodeURIComponent(chatId)}`

export interface RegenerationState {
  messages: ChatViewMessage[]
  question: ChatViewMessage
  assistant: ChatViewMessage
}

/** Clears an existing assistant slot without appending another user turn. */
export const prepareRegeneration = (
  messages: ChatViewMessage[],
  assistantId: string,
  createdTime = new Date().toISOString(),
): RegenerationState | null => {
  const assistantIndex = messages.findIndex((message) => message.id === assistantId && !message.isAsk)
  if (assistantIndex < 0) return null

  let question: ChatViewMessage | undefined
  for (let index = assistantIndex - 1; index >= 0; index -= 1) {
    if (messages[index].isAsk) {
      question = messages[index]
      break
    }
  }
  if (!question) return null

  const assistant: ChatViewMessage = {
    ...messages[assistantIndex],
    content: '',
    createdTime,
    sources: [],
    requestId: undefined,
    isFallback: false,
    fallbackReason: null,
    suggestedQuestions: [],
    grounded: undefined,
    groundingScore: undefined,
  }

  return {
    messages: messages.map((message, index) => (index === assistantIndex ? assistant : message)),
    question,
    assistant,
  }
}

/** A stopped stream keeps received Markdown; an empty slot gets a neutral status message. */
export const finalizeStoppedAssistant = (
  messages: ChatViewMessage[],
  assistantId: string,
): ChatViewMessage[] => messages.map((message) => {
  if (message.id !== assistantId || message.isAsk || message.content.trim().length > 0) return message
  return { ...message, content: '답변 생성을 중단했습니다.' }
})

export const isAbortError = (error: unknown) =>
  isObject(error) && error.name === 'AbortError'
