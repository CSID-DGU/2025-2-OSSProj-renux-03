export type NotificationTopic =
  | 'scholarship'
  | 'course_registration'
  | 'tuition_payment'
  | 'document_submission'
  | 'academic_schedule'

export interface NotificationPreference {
  topic: NotificationTopic
  enabled: boolean
  remindDaysBefore: number[]
  channel: string
}

export interface NotificationPreferenceResponse {
  preferences: NotificationPreference[]
}

export interface DeadlineItem {
  id: string
  source: string
  sourceLabel: string
  sourceId: string
  title: string
  topic: NotificationTopic
  topicLabel: string
  category?: string | null
  targetDate: string
  dDay: number
  url?: string | null
  snippet?: string | null
  dateSource?: string | null
}

export interface UserNotification {
  id: string
  topic: NotificationTopic
  topicLabel: string
  source: string
  sourceId: string
  title: string
  body: string
  targetDate: string
  reminderDate: string
  reminderDaysBefore: number
  url?: string | null
  isRead: boolean
  createdTime: string
  readTime?: string | null
}

export interface NotificationSyncResult {
  created: number
  upcomingMatched: number
}
