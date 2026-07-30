import type { DepartmentMajor } from './organization'

export interface ActiveChatOrganization {
  major?: DepartmentMajor | null
}

export interface ActiveChat {
  id: string
  title?: string | null
  organization?: ActiveChatOrganization | null
  guestToken?: string
  /** 마지막 활동 시각. 사이드바 정렬과 날짜 그룹의 기준. */
  updatedTime?: string | null
  /** 목록에서 대화를 구분하기 위한 마지막 메시지 한 줄 미리보기. */
  lastMessage?: string | null
}
