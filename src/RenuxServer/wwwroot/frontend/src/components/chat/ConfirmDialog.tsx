import { type ReactNode, useEffect, useId, useRef } from 'react'

interface ConfirmDialogProps {
  open: boolean
  title: string
  description?: ReactNode
  children?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  tone?: 'default' | 'danger'
  busy?: boolean
  confirmDisabled?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/**
 * 대화 삭제·이름 변경처럼 되돌리기 어려운 동작을 확인받는 다이얼로그.
 * 브라우저 confirm()과 달리 무엇이 사라지는지 문장으로 설명할 수 있고 모바일에서도 조작된다.
 */
const ConfirmDialog = ({
  open,
  title,
  description,
  children,
  confirmLabel = '확인',
  cancelLabel = '취소',
  tone = 'default',
  busy = false,
  confirmDisabled = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) => {
  const titleId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  const previouslyFocused = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!open) return
    previouslyFocused.current = document.activeElement as HTMLElement | null

    const focusTarget = dialogRef.current?.querySelector<HTMLElement>('input, textarea')
      ?? dialogRef.current?.querySelector<HTMLElement>('button[data-autofocus]')
      ?? dialogRef.current?.querySelector<HTMLElement>('button')
    focusTarget?.focus()

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onCancel()
        return
      }
      if (event.key !== 'Tab') return

      // 포커스가 다이얼로그 밖(뒤쪽 채팅 화면)으로 새지 않도록 순환시킨다.
      const focusables = dialogRef.current?.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
      )
      if (!focusables || focusables.length === 0) return
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown, true)
    return () => {
      document.removeEventListener('keydown', handleKeyDown, true)
      previouslyFocused.current?.focus?.()
    }
  }, [open, onCancel])

  if (!open) return null

  return (
    <div
      className="ch-dialog-backdrop"
      onMouseDown={(event) => { if (event.target === event.currentTarget) onCancel() }}
    >
      <div className="ch-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={dialogRef}>
        <h2 className="ch-dialog__title" id={titleId}>{title}</h2>
        {description && <p className="ch-dialog__desc">{description}</p>}
        {children}
        <div className="ch-dialog__actions">
          <button type="button" className="ch-btn" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className={`ch-btn ${tone === 'danger' ? 'ch-btn--danger' : 'ch-btn--primary'}`}
            onClick={onConfirm}
            disabled={busy || confirmDisabled}
            data-autofocus={children ? undefined : true}
          >
            {busy ? '처리 중...' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

export default ConfirmDialog
