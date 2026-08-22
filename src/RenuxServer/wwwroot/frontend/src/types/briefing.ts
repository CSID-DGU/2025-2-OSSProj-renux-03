/** 홈 브리핑 위젯이 쓰는 '오늘' 요약. 서버가 RAG 데이터셋에서 모아 내려준다. */

export interface BriefingMeal {
  corner: string
  menu: string
}

export interface BriefingSchedule {
  title: string
  period: string
}

export interface BriefingNotice {
  title: string
  url?: string | null
  publishedAt?: string | null
}

export interface HomeBriefing {
  generatedAt?: string | null
  meals: BriefingMeal[]
  schedules: BriefingSchedule[]
  notices: BriefingNotice[]
}
