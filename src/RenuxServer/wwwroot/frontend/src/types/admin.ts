export type AdminOrgStatus = '활성' | '검토 중' | '일시중지'

export interface CouncilOrganization {
  id: string
  name: string
  manager: string
  updatedAt: string
  status: AdminOrgStatus
  pendingRequests: number
}

export type AdminItemStatus = 'pending' | 'approved' | 'approved_manually' | 'rejected'

export interface AdminItemResponse {
  id: number | string
  source_type: string
  data: string
  status: AdminItemStatus
  created_at: string
}

export interface PendingAnswerReview {
  id: string
  departmentName: string
  submittedAt: string
  handler: string
  question: string
  answer: string
  status: AdminItemStatus
}

export type KnowledgeStatus = 'PENDING' | 'APPROVED' | 'REJECTED'

export interface DepartmentKnowledge {
  id: string
  title: string
  content: string
  status: KnowledgeStatus
  createdAt: string
  rejectionReason?: string
}

export interface RagChatLog {
  id: number
  question: string
  answer: string
  fallback_triggered: boolean
  fallback_reason: string | null
  session_id?: string | null
  username?: string | null
  created_at: string
  route: string
  source_count: number
}

export interface RagFeedbackItem {
  id: number
  rating: number
  reason: string | null
  comment: string | null
  major: string | null
  createdAt: string | null
  question: string | null
  answer: string | null
}

export interface ApiRagFeedbackItem {
  id: number
  rating: number
  reason: string | null
  comment: string | null
  major: string | null
  created_at: string | null
  question: string | null
  answer: string | null
}

export interface CouncilSignupRequest {
  id: string
  userId: string
  username: string
  majorId: string
  majorName?: string | null
  status: string
  createdTime: string
  reviewedTime?: string | null
  reviewNote?: string | null
}

export interface RagAdminFeedbackSummary {
  total: number
  up: number
  down: number
  satisfaction: number | null
  downReasons?: Record<string, number>
  down_reasons?: Record<string, number>
}

export interface RagDatasetStatus {
  key: string
  collection: string
  chroma_count: number | null
  cached_chunk_count: number
  chunk_artifact_exists: boolean
  chunk_artifact_mtime: string | null
  latest_document_published_at?: string | null
  vectorizer_exists: boolean
  vectorizer_mtime: string | null
  last_successful_indexed_at?: string | null
  vectorizer_sklearn_version?: string | null
  status: 'ok' | 'degraded' | 'error'
  error?: string | null
}

export interface RagAdminStatus {
  status: 'ok' | 'degraded' | 'error'
  generated_at: string
  datasets: RagDatasetStatus[]
  pending_items: {
    pending: number
    approved: number
    rejected: number
  }
  rag_logs: {
    total_queries: number
    fallback_count: number
    latest_query_at: string | null
    fallback_reasons?: Record<string, number>
  }
  visitor_stats?: {
    today: number | null
    total: number | null
  }
  feedback?: RagAdminFeedbackSummary
  notices_ingestion?: {
    last_collection_at: string | null
    last_successful_ingestion_at: string | null
    ingestion_summary: {
      status: string | null
      documents_seen: number
      documents_new: number
      documents_updated: number
      documents_deleted: number
      documents_failed: number
    }
    stage_summary: {
      raw_documents: number
      normalized_documents: number
      indexed_documents: number
    }
    quality_summary: {
      parse_failed: number
      severities: Record<string, number>
      recent_checks: Array<{
        document_key: string
        check_type: string
        severity: string
        message: string
        created_at: string
      }>
    }
  }
  error?: string
}
