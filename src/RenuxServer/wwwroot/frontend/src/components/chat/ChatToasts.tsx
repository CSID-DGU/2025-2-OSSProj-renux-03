import { useEffect, useRef } from 'react'

export type ChatToastTone = 'success' | 'error'

export interface ChatToast {
  id: string
  tone: ChatToastTone
  text: string
}

interface ChatToastItemProps {
  toast: ChatToast
  onDismiss: (id: string) => void
}

const ChatToastItem = ({ toast, onDismiss }: ChatToastItemProps) => {
  const onDismissRef = useRef(onDismiss)
  onDismissRef.current = onDismiss

  useEffect(() => {
    // 오류는 읽을 시간을 더 준다.
    const duration = toast.tone === 'error' ? 6000 : 3500
    const timer = window.setTimeout(() => onDismissRef.current(toast.id), duration)
    return () => window.clearTimeout(timer)
  }, [toast.id, toast.tone])

  return (
    <div
      className={`ch-toast ${toast.tone === 'error' ? 'ch-toast--error' : ''}`}
      role={toast.tone === 'error' ? 'alert' : 'status'}
    >
      <span className="ch-toast__text">{toast.text}</span>
      <button type="button" className="ch-toast__close" aria-label="알림 닫기" onClick={() => onDismiss(toast.id)}>
        ×
      </button>
    </div>
  )
}

interface ChatToastsProps {
  toasts: ChatToast[]
  onDismiss: (id: string) => void
}

const ChatToasts = ({ toasts, onDismiss }: ChatToastsProps) => {
  if (toasts.length === 0) return null
  return (
    <div className="ch-toasts" aria-live="polite">
      {toasts.map((toast) => (
        <ChatToastItem key={toast.id} toast={toast} onDismiss={onDismiss} />
      ))}
    </div>
  )
}

export default ChatToasts
