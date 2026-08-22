import { createContext, useContext } from 'react'
import type { ToastMessage, ToastTone } from './Toast'
import type { RagAdminStatus } from '../../types/admin'
import type { UserRole } from '../../types/auth'

export interface AdminConsoleValue {
  /** 서버에서 확인한 현재 역할 — 메뉴 노출과 기능 제한의 기준 */
  role: UserRole
  userName: string
  majorName: string
  isUniversityLevel: boolean

  /** 사이드바 배지에 쓰는 대기 건수 */
  pendingReviewCount: number
  pendingSignupCount: number

  /** 헤더 상태 점과 전역 경고 배너의 근거 */
  ragStatus: RagAdminStatus | null
  ragStatusError: string | null

  lastRefreshedAt: Date | null
  refreshing: boolean
  /** 요약 지표를 다시 읽는다. 각 페이지의 상세 목록은 페이지가 스스로 갱신한다. */
  refreshSummary: () => Promise<void>
  /** 페이지가 승인/반려 후 배지를 즉시 맞추고 싶을 때 호출 */
  notifyDataChanged: () => void
  /** 데이터 변경 신호. useEffect 의존성에 넣으면 다른 페이지의 변경을 따라간다. */
  dataVersion: number

  showToast: (text: string, tone?: ToastTone, options?: Partial<Omit<ToastMessage, 'id' | 'text' | 'tone'>>) => string
  dismissToast: (id: string) => void
}

export const AdminConsoleContext = createContext<AdminConsoleValue | null>(null)

export const useAdminConsole = (): AdminConsoleValue => {
  const value = useContext(AdminConsoleContext)
  if (!value) {
    throw new Error('useAdminConsole은 AdminLayout 내부에서만 사용할 수 있습니다.')
  }
  return value
}
