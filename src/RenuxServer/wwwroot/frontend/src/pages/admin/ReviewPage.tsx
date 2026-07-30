import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  approveItem,
  fetchAllItems,
  fetchPendingItems,
  rejectItem,
  updateItem,
} from '../../admin/adminApi'
import { buildChatPreview, getEditableFields, toPendingAnswerReview } from '../../admin/adminData'
import {
  formatDateTime,
  formatFullDateTime,
  getApiErrorMessage,
  getRequestStatusLabel,
  getRequestStatusTone,
  getSourceTypeLabel,
} from '../../admin/format'
import Modal from '../../components/admin/Modal'
import { useAdminConsole } from '../../components/admin/adminConsoleContext'
import {
  EmptyState,
  ErrorNote,
  FilterBar,
  FilterChip,
  LoadingNote,
  PageHeader,
  Panel,
  StatusPill,
} from '../../components/admin/ui'
import type { PendingAnswerReview } from '../../types/admin'

type StatusFilter = 'pending' | 'approved' | 'rejected' | 'all'

const STATUS_FILTERS: Array<{ key: StatusFilter; label: string }> = [
  { key: 'pending', label: '대기' },
  { key: 'approved', label: '승인' },
  { key: 'rejected', label: '반려' },
  { key: 'all', label: '전체' },
]

const SOURCE_FILTERS = [
  { key: 'all', label: '전체 유형' },
  { key: 'custom_knowledge', label: 'FAQ' },
  { key: 'event', label: '행사' },
  { key: 'announcement', label: '공지' },
]

const matchesStatus = (review: PendingAnswerReview, filter: StatusFilter) => {
  if (filter === 'all') return true
  if (filter === 'pending') return review.status === 'pending'
  if (filter === 'approved') return review.status === 'approved' || review.status === 'approved_manually'
  return review.status === 'rejected'
}

const ReviewPage = () => {
  const { showToast, notifyDataChanged, dataVersion } = useAdminConsole()
  const [searchParams, setSearchParams] = useSearchParams()

  const [reviews, setReviews] = useState<PendingAnswerReview[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [statusFilter, setStatusFilter] = useState<StatusFilter>('pending')
  const [sourceFilter, setSourceFilter] = useState('all')
  const [departmentFilter, setDepartmentFilter] = useState('all')
  const [search, setSearch] = useState('')

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set())
  const [showPreview, setShowPreview] = useState(false)

  const [editing, setEditing] = useState(false)
  const [editDraft, setEditDraft] = useState<Record<string, string>>({})
  const [savingEdit, setSavingEdit] = useState(false)

  const [rejectTarget, setRejectTarget] = useState<{ ids: string[]; label: string } | null>(null)
  const [rejectNote, setRejectNote] = useState('')
  const [bulkApproveTarget, setBulkApproveTarget] = useState<string[] | null>(null)
  const [processing, setProcessing] = useState(false)

  const loadReviews = useCallback(async () => {
    setLoading(true)
    const [pendingResult, historyResult] = await Promise.allSettled([
      fetchPendingItems(),
      fetchAllItems(),
    ])

    const merged: PendingAnswerReview[] = []
    const errors: string[] = []

    if (pendingResult.status === 'fulfilled' && Array.isArray(pendingResult.value)) {
      merged.push(...pendingResult.value.map(toPendingAnswerReview))
    } else {
      errors.push('검수 대기 목록을 불러오지 못했습니다.')
    }

    if (historyResult.status === 'fulfilled' && Array.isArray(historyResult.value)) {
      // /admin/pending과 /admin/items는 대기 항목이 겹치므로 id로 중복을 제거한다.
      const seen = new Set(merged.map((review) => review.id))
      historyResult.value
        .map(toPendingAnswerReview)
        .filter((review) => !seen.has(review.id))
        .forEach((review) => merged.push(review))
    } else {
      errors.push('처리 이력을 불러오지 못했습니다.')
    }

    merged.sort((a, b) => {
      const aPending = a.status === 'pending'
      const bPending = b.status === 'pending'
      if (aPending !== bPending) return aPending ? -1 : 1
      return new Date(b.submittedAt).getTime() - new Date(a.submittedAt).getTime()
    })

    setReviews(merged)
    setLoadError(errors.length > 0 ? errors.join(' ') : null)
    setLoading(false)
  }, [])

  useEffect(() => {
    void loadReviews()
  }, [loadReviews, dataVersion])

  // 로그·피드백 화면에서 "FAQ로 등록"으로 넘어온 경우 해당 항목을 바로 연다.
  useEffect(() => {
    const focusId = searchParams.get('item')
    if (focusId && reviews.some((review) => review.id === focusId)) {
      setSelectedId(focusId)
      setStatusFilter('all')
      searchParams.delete('item')
      setSearchParams(searchParams, { replace: true })
    }
  }, [reviews, searchParams, setSearchParams])

  const departments = useMemo(
    () => Array.from(new Set(reviews.map((review) => review.departmentName))).sort(),
    [reviews],
  )

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    return reviews.filter((review) => {
      if (!matchesStatus(review, statusFilter)) return false
      if (sourceFilter !== 'all' && review.sourceType !== sourceFilter) return false
      if (departmentFilter !== 'all' && review.departmentName !== departmentFilter) return false
      if (term && !review.question.toLowerCase().includes(term) && !review.answer.toLowerCase().includes(term)) {
        return false
      }
      return true
    })
  }, [reviews, statusFilter, sourceFilter, departmentFilter, search])

  // 필터 결과가 바뀌면 화면에 없는 항목이 선택된 채로 남지 않도록 맞춘다.
  useEffect(() => {
    setSelectedId((previous) => (
      previous && filtered.some((review) => review.id === previous) ? previous : filtered[0]?.id ?? null
    ))
    setCheckedIds((previous) => {
      const visible = new Set(filtered.map((review) => review.id))
      const next = new Set(Array.from(previous).filter((id) => visible.has(id)))
      return next.size === previous.size ? previous : next
    })
  }, [filtered])

  const selected = useMemo(
    () => filtered.find((review) => review.id === selectedId) ?? null,
    [filtered, selectedId],
  )

  useEffect(() => {
    setEditing(false)
    setShowPreview(false)
  }, [selectedId])

  const pendingCount = reviews.filter((review) => review.status === 'pending').length
  const checkedPendingIds = useMemo(
    () => Array.from(checkedIds).filter((id) => reviews.find((review) => review.id === id)?.status === 'pending'),
    [checkedIds, reviews],
  )

  const toggleChecked = (id: string) => {
    setCheckedIds((previous) => {
      const next = new Set(previous)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const applyLocalStatus = (ids: string[], status: PendingAnswerReview['status'], note?: string) => {
    setReviews((previous) => previous.map((review) => (
      ids.includes(review.id) ? { ...review, status, reviewNote: note ?? review.reviewNote } : review
    )))
  }

  const runApprove = async (ids: string[]) => {
    setProcessing(true)
    const results = await Promise.allSettled(ids.map((id) => approveItem(id)))
    const succeeded = ids.filter((_, index) => results[index].status === 'fulfilled')
    const failed = results.filter((result) => result.status === 'rejected')

    if (succeeded.length > 0) {
      applyLocalStatus(succeeded, 'approved')
      showToast(
        succeeded.length === 1 ? '승인했습니다. 챗봇에 곧 반영됩니다.' : `${succeeded.length}건을 승인했습니다.`,
        'success',
      )
      notifyDataChanged()
    }
    if (failed.length > 0) {
      const reason = getApiErrorMessage((failed[0] as PromiseRejectedResult).reason, '승인 처리에 실패했습니다.')
      showToast(`${failed.length}건 승인 실패 — ${reason}`, 'error')
    }
    setCheckedIds(new Set())
    setProcessing(false)
  }

  const runReject = async (ids: string[], note: string) => {
    setProcessing(true)
    const results = await Promise.allSettled(ids.map((id) => rejectItem(id, note)))
    const succeeded = ids.filter((_, index) => results[index].status === 'fulfilled')
    const failed = results.filter((result) => result.status === 'rejected')

    if (succeeded.length > 0) {
      applyLocalStatus(succeeded, 'rejected', note)
      showToast(
        succeeded.length === 1 ? '반려했습니다. 사유가 제출자에게 전달됩니다.' : `${succeeded.length}건을 반려했습니다.`,
        'success',
      )
      notifyDataChanged()
    }
    if (failed.length > 0) {
      const reason = getApiErrorMessage((failed[0] as PromiseRejectedResult).reason, '반려 처리에 실패했습니다.')
      showToast(`${failed.length}건 반려 실패 — ${reason}`, 'error')
    }
    setCheckedIds(new Set())
    setProcessing(false)
  }

  const startEditing = () => {
    if (!selected) return
    const fields = getEditableFields(selected.sourceType)
    const draft: Record<string, string> = {}
    fields.forEach((field) => {
      const value = selected.raw[field.key]
      draft[field.key] = typeof value === 'string' ? value : ''
    })
    setEditDraft(draft)
    setEditing(true)
  }

  const saveEdit = async () => {
    if (!selected) return
    setSavingEdit(true)
    try {
      // 편집 가능한 필드만 덮어쓰고, requester/department 등 나머지 payload는 보존한다.
      const nextData = { ...selected.raw, ...editDraft }
      await updateItem(selected.id, nextData)
      setReviews((previous) => previous.map((review) => (
        review.id === selected.id ? toPendingAnswerReviewFromDraft(review, nextData) : review
      )))
      setEditing(false)
      showToast('수정 내용을 저장했습니다.', 'success')
    } catch (error) {
      showToast(getApiErrorMessage(error, '수정 저장에 실패했습니다.'), 'error')
    } finally {
      setSavingEdit(false)
    }
  }

  const previewText = selected ? buildChatPreview(selected.sourceType, editing ? { ...selected.raw, ...editDraft } : selected.raw) : ''

  return (
    <>
      <PageHeader
        title="검수함"
        description={`제출된 지식을 확인하고 승인하거나 반려합니다. 현재 대기 ${pendingCount}건.`}
      />

      <Panel padded={false}>
        <FilterBar>
          {STATUS_FILTERS.map((filter) => (
            <FilterChip
              key={filter.key}
              active={statusFilter === filter.key}
              onClick={() => setStatusFilter(filter.key)}
            >
              {filter.label}
              {filter.key === 'pending' && pendingCount > 0 ? ` ${pendingCount}` : ''}
            </FilterChip>
          ))}
          <select
            className="ac-select"
            value={sourceFilter}
            onChange={(event) => setSourceFilter(event.target.value)}
            aria-label="등록 유형 필터"
          >
            {SOURCE_FILTERS.map((option) => (
              <option key={option.key} value={option.key}>{option.label}</option>
            ))}
          </select>
          <select
            className="ac-select"
            value={departmentFilter}
            onChange={(event) => setDepartmentFilter(event.target.value)}
            aria-label="학과 필터"
          >
            <option value="all">전체 학과</option>
            {departments.map((department) => (
              <option key={department} value={department}>{department}</option>
            ))}
          </select>
          <input
            type="search"
            className="ac-input ac-input--grow"
            placeholder="질문·답변 내용 검색"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="검수 항목 검색"
          />
        </FilterBar>

        {loadError && <ErrorNote>{loadError}</ErrorNote>}

        {loading ? (
          <LoadingNote />
        ) : filtered.length === 0 ? (
          <EmptyState>조건에 맞는 검수 항목이 없습니다.</EmptyState>
        ) : (
          <div className="ac-split">
            <div className="ac-list">
              {checkedPendingIds.length > 0 && (
                <div className="ac-bulkbar">
                  <span>{checkedPendingIds.length}개 선택됨</span>
                  <button
                    type="button"
                    className="ac-btn ac-btn--sm ac-btn--primary"
                    onClick={() => setBulkApproveTarget(checkedPendingIds)}
                    disabled={processing}
                  >
                    일괄 승인
                  </button>
                  <button
                    type="button"
                    className="ac-btn ac-btn--sm"
                    onClick={() => {
                      setRejectNote('')
                      setRejectTarget({ ids: checkedPendingIds, label: `${checkedPendingIds.length}건` })
                    }}
                    disabled={processing}
                  >
                    일괄 반려
                  </button>
                  <button type="button" className="ac-btn ac-btn--sm ac-btn--ghost" onClick={() => setCheckedIds(new Set())}>
                    선택 해제
                  </button>
                </div>
              )}

              {filtered.map((review) => (
                <div
                  key={review.id}
                  className={`ac-list-item ${selectedId === review.id ? 'ac-list-item--active' : ''}`}
                >
                  {review.status === 'pending' && (
                    <input
                      type="checkbox"
                      className="ac-checkbox"
                      checked={checkedIds.has(review.id)}
                      onChange={() => toggleChecked(review.id)}
                      aria-label={`${review.question} 선택`}
                    />
                  )}
                  <button
                    type="button"
                    className="ac-list-item__body"
                    style={{ border: 0, background: 'none', padding: 0, textAlign: 'left', font: 'inherit', cursor: 'pointer' }}
                    onClick={() => setSelectedId(review.id)}
                    aria-pressed={selectedId === review.id}
                  >
                    <span className="ac-list-item__title">{review.question}</span>
                    <span className="ac-list-item__meta">
                      {review.departmentName} · {getSourceTypeLabel(review.sourceType)} · {review.handler} · {formatDateTime(review.submittedAt)}
                    </span>
                  </button>
                  <StatusPill tone={getRequestStatusTone(review.status)}>
                    {getRequestStatusLabel(review.status)}
                  </StatusPill>
                </div>
              ))}
            </div>

            <div className="ac-detail">
              {!selected ? (
                <EmptyState>목록에서 항목을 선택하세요.</EmptyState>
              ) : (
                <>
                  <p className="ac-detail__eyebrow">
                    {selected.departmentName} · {getSourceTypeLabel(selected.sourceType)}
                  </p>
                  <h2 className="ac-detail__title">{selected.question}</h2>

                  <dl className="ac-detail__meta">
                    <div>
                      <dt>제출자</dt>
                      <dd>{selected.handler}</dd>
                    </div>
                    <div>
                      <dt>제출 시각</dt>
                      <dd>{formatFullDateTime(selected.submittedAt)}</dd>
                    </div>
                    <div>
                      <dt>상태</dt>
                      <dd>
                        <StatusPill tone={getRequestStatusTone(selected.status)}>
                          {getRequestStatusLabel(selected.status)}
                        </StatusPill>
                      </dd>
                    </div>
                    {selected.reviewedBy && (
                      <div>
                        <dt>처리자</dt>
                        <dd>{selected.reviewedBy}</dd>
                      </div>
                    )}
                    {selected.reviewedAt && (
                      <div>
                        <dt>처리 시각</dt>
                        <dd>{formatFullDateTime(selected.reviewedAt)}</dd>
                      </div>
                    )}
                  </dl>

                  {selected.reviewNote && (
                    <p className="ac-hint" style={{ marginBottom: '10px' }}>
                      <b>처리 메모:</b> {selected.reviewNote}
                    </p>
                  )}

                  {editing ? (
                    <div>
                      {getEditableFields(selected.sourceType).map((field) => (
                        <div className="ac-field" key={field.key}>
                          <label className="ac-label" htmlFor={`edit-${field.key}`}>{field.label}</label>
                          {field.type === 'textarea' ? (
                            <textarea
                              id={`edit-${field.key}`}
                              className="ac-textarea"
                              rows={6}
                              value={editDraft[field.key] ?? ''}
                              onChange={(event) => setEditDraft((draft) => ({ ...draft, [field.key]: event.target.value }))}
                            />
                          ) : (
                            <input
                              id={`edit-${field.key}`}
                              type={field.type === 'date' ? 'date' : 'text'}
                              className="ac-input"
                              value={editDraft[field.key] ?? ''}
                              onChange={(event) => setEditDraft((draft) => ({ ...draft, [field.key]: event.target.value }))}
                            />
                          )}
                        </div>
                      ))}
                      <div className="ac-detail__actions">
                        <button type="button" className="ac-btn" onClick={() => setEditing(false)} disabled={savingEdit}>
                          취소
                        </button>
                        <button type="button" className="ac-btn ac-btn--primary" onClick={saveEdit} disabled={savingEdit}>
                          {savingEdit ? '저장 중...' : '수정 저장'}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="ac-detail__content">{selected.answer || '내용이 없습니다.'}</div>
                  )}

                  <div style={{ display: 'flex', gap: '8px', marginTop: '12px', flexWrap: 'wrap' }}>
                    <button
                      type="button"
                      className="ac-btn ac-btn--sm"
                      onClick={() => setShowPreview((open) => !open)}
                      aria-expanded={showPreview}
                    >
                      {showPreview ? '미리보기 닫기' : '챗봇 미리보기'}
                    </button>
                    {selected.status === 'pending' && !editing && (
                      <button type="button" className="ac-btn ac-btn--sm" onClick={startEditing}>
                        수정
                      </button>
                    )}
                  </div>

                  {showPreview && (
                    <div className="ac-preview">
                      <span className="ac-preview__label">챗봇에 이렇게 반영됩니다</span>
                      <div className="ac-preview__bubble">{previewText || '표시할 내용이 없습니다.'}</div>
                    </div>
                  )}

                  {selected.status === 'pending' && !editing && (
                    <div className="ac-detail__actions">
                      <button
                        type="button"
                        className="ac-btn ac-btn--danger-ghost"
                        onClick={() => {
                          setRejectNote('')
                          setRejectTarget({ ids: [selected.id], label: selected.question })
                        }}
                        disabled={processing}
                      >
                        반려…
                      </button>
                      <button
                        type="button"
                        className="ac-btn ac-btn--primary"
                        onClick={() => { void runApprove([selected.id]) }}
                        disabled={processing}
                      >
                        {processing ? '처리 중...' : '승인'}
                      </button>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        )}
      </Panel>

      <Modal
        open={rejectTarget !== null}
        title="반려 사유 입력"
        description={
          <>
            <b>{rejectTarget?.label}</b> 항목을 반려합니다. 입력한 사유는 제출한 학과 관리자에게 그대로 표시됩니다.
          </>
        }
        confirmLabel="반려"
        tone="danger"
        busy={processing}
        confirmDisabled={rejectNote.trim().length === 0}
        onCancel={() => setRejectTarget(null)}
        onConfirm={async () => {
          if (!rejectTarget) return
          await runReject(rejectTarget.ids, rejectNote)
          setRejectTarget(null)
        }}
      >
        <div className="ac-field">
          <label className="ac-label" htmlFor="reject-note">반려 사유 (필수)</label>
          <textarea
            id="reject-note"
            className="ac-textarea"
            value={rejectNote}
            onChange={(event) => setRejectNote(event.target.value)}
            placeholder="예: 행사 일정이 학사일정과 충돌합니다. 날짜를 확인해주세요."
          />
        </div>
      </Modal>

      <Modal
        open={bulkApproveTarget !== null}
        title="선택 항목 일괄 승인"
        description={`${bulkApproveTarget?.length ?? 0}건을 승인합니다. 승인된 지식은 챗봇 답변에 사용됩니다.`}
        confirmLabel="일괄 승인"
        busy={processing}
        onCancel={() => setBulkApproveTarget(null)}
        onConfirm={async () => {
          if (!bulkApproveTarget) return
          await runApprove(bulkApproveTarget)
          setBulkApproveTarget(null)
        }}
      />
    </>
  )
}

/** 편집 저장 후 목록 항목의 표시 텍스트를 payload 기준으로 다시 만든다. */
const toPendingAnswerReviewFromDraft = (
  review: PendingAnswerReview,
  data: Record<string, unknown>,
): PendingAnswerReview => {
  const text = (key: string) => (typeof data[key] === 'string' ? (data[key] as string) : '')
  if (review.sourceType === 'custom_knowledge') {
    return { ...review, raw: data, question: text('question') || '질문 없음', answer: text('answer') }
  }
  if (review.sourceType === 'event') {
    return {
      ...review,
      raw: data,
      question: `[행사] ${text('title')}`,
      answer: `일시: ${text('start_date')} ~ ${text('end_date')}\n장소: ${text('location')}\n\n${text('description')}`,
    }
  }
  if (review.sourceType === 'announcement') {
    return {
      ...review,
      raw: data,
      question: `[공지] ${text('title')}`,
      answer: `게시일: ${text('date')}\n분류: ${text('category')}\n\n${text('content')}`,
    }
  }
  return { ...review, raw: data }
}

export default ReviewPage
