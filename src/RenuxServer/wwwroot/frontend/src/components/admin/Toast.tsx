import { useEffect, useRef } from 'react'

export type ToastTone = 'success' | 'error' | 'info'

export interface ToastMessage {
  id: string
  tone: ToastTone
  text: string
  /** 실행 취소가 가능한 작업이면 라벨과 콜백을 넘긴다. */
  undoLabel?: string
  onUndo?: () => void
  /** ms. 0이면 자동 소거하지 않는다. */
  duration?: number
}

interface ToastItemProps {
  toast: ToastMessage
  onDismiss: (id: string) => void
}

const TONE_ICON: Record<ToastTone, string> = {
  success: '✓',
  error: '!',
  info: 'ℹ',
}

const ToastItem = ({ toast, onDismiss }: ToastItemProps) => {
  const onDismissRef = useRef(onDismiss)
  onDismissRef.current = onDismiss

  useEffect(() => {
    const duration = toast.duration ?? (toast.tone === 'error' ? 6000 : 4000)
    if (duration <= 0) return
    const timer = setTimeout(() => onDismissRef.current(toast.id), duration)
    return () => clearTimeout(timer)
  }, [toast.id, toast.duration, toast.tone])

  return (
    <div className={`ac-toast ac-toast--${toast.tone}`} role={toast.tone === 'error' ? 'alert' : 'status'}>
      <span className="ac-toast__icon" aria-hidden="true">{TONE_ICON[toast.tone]}</span>
      <span className="ac-toast__text">{toast.text}</span>
      {toast.onUndo && (
        <button
          type="button"
          className="ac-toast__undo"
          onClick={() => {
            toast.onUndo?.()
            onDismiss(toast.id)
          }}
        >
          {toast.undoLabel ?? '실행 취소'}
        </button>
      )}
      <button type="button" className="ac-toast__close" aria-label="알림 닫기" onClick={() => onDismiss(toast.id)}>
        ×
      </button>
    </div>
  )
}

interface ToastStackProps {
  toasts: ToastMessage[]
  onDismiss: (id: string) => void
}

const ToastStack = ({ toasts, onDismiss }: ToastStackProps) => {
  if (toasts.length === 0) return null
  return (
    <div className="ac-toast-stack" aria-live="polite" aria-atomic="false">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} onDismiss={onDismiss} />
      ))}
    </div>
  )
}

export default ToastStack
