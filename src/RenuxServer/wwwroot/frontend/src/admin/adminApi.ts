import { apiFetch } from '../api/client'
import type {
  AdminItemResponse,
  AdminRoleOption,
  AdminUserAccount,
  ApiRagFeedbackItem,
  CouncilOrganization,
  CouncilSignupRequest,
  MajorOption,
  ProductKpiReport,
  RagAdminStatus,
  RagChatLog,
  RagFeedbackItem,
  ReindexResult,
} from '../types/admin'

/**
 * 관리자 콘솔의 모든 서버 호출을 모은 계층.
 * 페이지 컴포넌트가 URL·쿼리스트링 조립을 직접 하지 않도록 하고,
 * 캐시 무효화(t 파라미터)처럼 반복되는 규칙을 한곳에서 처리한다.
 */

/** GET 응답이 프록시/브라우저 캐시에 걸리지 않도록 타임스탬프를 붙인다. */
const bust = (path: string) => {
  const separator = path.includes('?') ? '&' : '?'
  return `${path}${separator}t=${Date.now()}`
}

const buildQuery = (params: Record<string, string | number | boolean | null | undefined>) => {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value === null || value === undefined || value === '') return
    search.set(key, String(value))
  })
  const query = search.toString()
  return query ? `?${query}` : ''
}

interface ApiOrganization {
  id: string
  major: { id: string; majorname?: string; Majorname?: string }
  managerName?: string
  ManagerName?: string
  updatedTime?: string | null
  UpdatedTime?: string | null
  pendingRequests?: number
  PendingRequests?: number
  memberCount?: number
  MemberCount?: number
}

export interface ApiMessageResponse {
  message?: string
}

// ---------------------------------------------------------------- 검수 항목

export const fetchPendingItems = () => apiFetch<AdminItemResponse[]>(bust('/admin/pending'))

export const fetchAllItems = () => apiFetch<AdminItemResponse[]>(bust('/admin/items'))

export const approveItem = (id: string, note?: string) =>
  apiFetch<ApiMessageResponse>(`/admin/approve/${id}`, {
    method: 'POST',
    json: { note: note?.trim() || null },
  })

export const rejectItem = (id: string, note?: string) =>
  apiFetch<ApiMessageResponse>(`/admin/reject/${id}`, {
    method: 'POST',
    json: { note: note?.trim() || null },
  })

export const cancelItem = (id: string) =>
  apiFetch<ApiMessageResponse>(`/admin/cancel/${id}`, { method: 'POST' })

/** 승인 전 수정 — payload를 통째로 교체한다. */
export const updateItem = (id: string, data: Record<string, unknown>) =>
  apiFetch<ApiMessageResponse>(`/admin/items/${id}`, {
    method: 'PATCH',
    json: { data: JSON.stringify(data) },
  })

/** 승인된 지식을 챗봇 노출에서 내리거나 다시 올린다. */
export const setItemDisabled = (id: string, disabled: boolean) =>
  apiFetch<ApiMessageResponse>(`/admin/items/${id}/disabled`, {
    method: 'POST',
    json: { disabled },
  })

export const submitItem = (sourceType: string, data: Record<string, unknown>) =>
  apiFetch<{ status: string; id: number }>('/admin/submit', {
    method: 'POST',
    json: { source_type: sourceType, data: JSON.stringify(data) },
  })

// ---------------------------------------------------------------- 사용자·조직

export const fetchCouncilSignupRequests = () =>
  apiFetch<CouncilSignupRequest[]>(bust('/auth/council-signup-requests'))

export const reviewCouncilSignup = (requestId: string, action: 'approve' | 'reject', note?: string) =>
  apiFetch<ApiMessageResponse>(`/auth/council-signup-requests/${requestId}/${action}`, {
    method: 'POST',
    json: { note: note?.trim() || null },
  })

export const fetchAdminUsers = () => apiFetch<AdminUserAccount[]>(bust('/auth/admin/users'))

export const fetchAdminRoles = () => apiFetch<AdminRoleOption[]>(bust('/auth/admin/users/roles'))

export const fetchMajors = () => apiFetch<MajorOption[]>('/req/major')

export const updateAdminUser = (targetUserId: string, update: { majorId?: string; roleId?: string }) =>
  apiFetch<ApiMessageResponse>(`/auth/admin/users/${targetUserId}`, { method: 'PATCH', json: update })

export const resetAdminUserPassword = (targetUserId: string, password: string) =>
  apiFetch<ApiMessageResponse>(`/auth/admin/users/${targetUserId}/reset-password`, {
    method: 'POST',
    json: { password },
  })

export const deleteAdminUser = (targetUserId: string) =>
  apiFetch<ApiMessageResponse>(`/auth/admin/users/${targetUserId}`, { method: 'DELETE' })

export const fetchOrganizations = async (): Promise<CouncilOrganization[]> => {
  const data = await apiFetch<ApiOrganization[]>('/req/orgs')
  if (!Array.isArray(data)) return []
  return data.map((org) => ({
    id: org.id,
    name: `${org.major?.majorname || org.major?.Majorname || '알 수 없음'} 학생회`,
    manager: org.managerName || org.ManagerName || '-',
    // 서버가 갱신 시각을 주기 전에는 '-'로 두어, 오늘 날짜를 실데이터처럼 보이게 하지 않는다.
    updatedAt: org.updatedTime ?? org.UpdatedTime ?? '',
    status: '활성',
    pendingRequests: org.pendingRequests ?? org.PendingRequests ?? 0,
  }))
}

// ---------------------------------------------------------------- RAG 운영

export const fetchRagStatus = () => apiFetch<RagAdminStatus>(bust('/admin/rag/status'))

export interface ChatLogQuery {
  limit?: number
  offset?: number
  from?: string
  to?: string
  route?: string
  fallbackOnly?: boolean
  search?: string
}

export const fetchChatLogs = (query: ChatLogQuery = {}) => {
  const path = `/admin/rag-logs-list${buildQuery({
    limit: query.limit ?? 50,
    offset: query.offset,
    from: query.from,
    to: query.to,
    route: query.route,
    fallback_only: query.fallbackOnly ? 1 : undefined,
    search: query.search,
  })}`
  return apiFetch<RagChatLog[]>(bust(path))
}

export interface FeedbackQuery {
  rating?: 1 | -1 | null
  limit?: number
  from?: string
  to?: string
}

export const fetchFeedback = async (query: FeedbackQuery = {}): Promise<RagFeedbackItem[]> => {
  const path = `/admin/rag-feedback${buildQuery({
    rating: query.rating ?? undefined,
    limit: query.limit ?? 100,
    from: query.from,
    to: query.to,
  })}`
  const data = await apiFetch<ApiRagFeedbackItem[]>(bust(path))
  if (!Array.isArray(data)) return []
  return data.map((item) => ({
    id: item.id,
    rating: item.rating,
    reason: item.reason,
    comment: item.comment,
    major: item.major,
    createdAt: item.created_at,
    question: item.question,
    answer: item.answer,
  }))
}

export const reindexDataset = (target: string) =>
  apiFetch<ReindexResult>(`/admin/reindex/${target}`, { method: 'POST' })

export const fetchProductKpis = (from?: string, to?: string) =>
  apiFetch<ProductKpiReport>(`/admin/product-kpis${buildQuery({ from, to })}`)

// ---------------------------------------------------------------- 현재 사용자

export interface AdminIdentity {
  name?: string
  Name?: string
  roleName?: string
  role?: string
  majorName?: string
  MajorName?: string
}

export const fetchIdentity = () => apiFetch<AdminIdentity>('/auth/name')

export const readIdentityName = (identity?: AdminIdentity | null) =>
  identity?.name || identity?.Name || ''

export const readIdentityMajor = (identity?: AdminIdentity | null) =>
  identity?.majorName || identity?.MajorName || ''
