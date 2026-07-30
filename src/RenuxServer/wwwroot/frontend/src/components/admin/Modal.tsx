import { type ReactNode, useEffect, useId, useRef } from 'react'

interface ModalProps {
  open: boolean
  title: string
  description?: ReactNode
  children?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  /** 파괴적 작업이면 확인 버튼을 위험 색으로 표시한다. */
  tone?: 'default' | 'danger'
  busy?: boolean
  confirmDisabled?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/**
 * confirm()/prompt()를 대체하는 모달.
 * 브라우저 기본 다이얼로그와 달리 설명·입력·위험도 표시를 담을 수 있고,
 * 모바일에서도 조작 가능하다.
 */
const Modal = ({
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
}: ModalProps) => {
  const titleId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  const previouslyFocused = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!open) return
    previouslyFocused.current = document.activeElement as HTMLElement | null

    // 첫 입력 요소로 포커스를 옮겨 키보드만으로 바로 조작할 수 있게 한다.
    const focusTarget = dialogRef.current?.querySelector<HTMLElement>(
      'input, textarea, select, button[data-autofocus]',
    ) ?? dialogRef.current?.querySelector<HTMLElement>('button')
    focusTarget?.focus()

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onCancel()
        return
      }
      if (event.key !== 'Tab') return

      // 포커스가 모달 밖(뒤쪽 페이지)으로 새지 않도록 순환시킨다.
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
    <div className="ac-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onCancel() }}>
      <div className="ac-modal" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={dialogRef}>
        <h2 className="ac-modal__title" id={titleId}>{title}</h2>
        {description && <div className="ac-modal__description">{description}</div>}
        {children && <div className="ac-modal__body">{children}</div>}
        <div className="ac-modal__actions">
          <button type="button" className="ac-btn" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className={`ac-btn ${tone === 'danger' ? 'ac-btn--danger' : 'ac-btn--primary'}`}
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

export default Modal
