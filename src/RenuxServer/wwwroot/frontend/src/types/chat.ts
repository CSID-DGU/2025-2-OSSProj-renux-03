import type { DepartmentMajor } from './organization'

export interface ActiveChatOrganization {
  major?: DepartmentMajor | null
}

export interface ActiveChat {
  id: string
  title?: string | null
  organization?: ActiveChatOrganization | null
}

export interface ChatSource {
  source: string
  metadata?: Record<string, unknown>
  snippet: string
}

export interface ChatMessage {
  id: string
  chatId: string
  isAsk: boolean
  content: string
  citations?: string | null
  route?: string[] | null
  sources?: ChatSource[] | null
  createdTime: string | number
}
