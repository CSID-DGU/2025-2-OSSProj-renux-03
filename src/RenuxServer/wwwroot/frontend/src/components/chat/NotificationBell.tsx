import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { UserNotification } from '../../types/notification'

interface NotificationBellProps {
  notifications: UserNotification[]
  unreadCount: number
  onMarkRead: (notificationId: string) => void
}

const formatWhen = (value: string) => {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('ko-KR', { month: 'numeric', day: 'numeric' }).format(date)
}

const formatDday = (targetDate: string) => {
  const target = new Date(targetDate)
  if (Number.isNaN(target.getTime())) return null
  const startOfDay = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
  const diff = Math.round((startOfDay(target) - startOfDay(new Date())) / 86_400_000)
  if (diff === 0) return { label: 'D-day', tone: 'danger' as const }
  if (diff > 0) return { label: `D-${diff}`, tone: diff <= 3 ? ('danger' as const) : ('warn' as const) }
  return { label: `D+${Math.abs(diff)}`, tone: 'info' as const }
}

/**
 * 헤더의 알림함. 마감 알림이 설정 페이지 안에만 있어 존재를 알기 어려웠던 문제를 해결한다.
 * 읽지 않은 수를 항상 노출하고, 자세한 설정은 기존 알림 설정 페이지로 넘긴다.
 */
const NotificationBell = ({ notifications, unreadCount, onMarkRead }: NotificationBellProps) => {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const anchorRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handlePointerDown = (event: MouseEvent) => {
      if (!anchorRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [open])

  const visible = notifications.slice(0, 6)

  return (
    <div className="ch-menu-anchor" ref={anchorRef}>
      <button
        type="button"
        className="ch-bell"
        aria-label={unreadCount > 0 ? `알림 ${unreadCount}건 읽지 않음` : '알림'}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span aria-hidden="true">🔔</span>
        {unreadCount > 0 && <span className="ch-bell__count">{unreadCount > 99 ? '99+' : unreadCount}</span>}
      </button>

      {open && (
        <div className="ch-menu ch-menu--right ch-menu--notifications" role="menu">
          <div className="ch-menu__header">
            <span className="ch-menu__name">알림</span>
            <span className="ch-menu__sub">
              {unreadCount > 0 ? `읽지 않은 알림 ${unreadCount}건` : '읽지 않은 알림이 없습니다'}
            </span>
          </div>

          {visible.length === 0 ? (
            <p className="ch-notif__empty">
              아직 도착한 알림이 없습니다.<br />관심 주제를 켜두면 마감 전에 알려드려요.
            </p>
          ) : (
            visible.map((notification) => {
              const dday = formatDday(notification.targetDate)
              return (
                <div
                  key={notification.id}
                  className={`ch-notif ${notification.isRead ? '' : 'ch-notif--unread'}`}
                >
                  <div className="ch-notif__row">
                    <div>
                      <div className="ch-notif__meta">
                        {dday && <span className={`ch-badge ch-badge--${dday.tone}`}>{dday.label}</span>}
                        <span>{notification.topicLabel}</span>
                        <span>{formatWhen(notification.createdTime)}</span>
                      </div>
                      <p className="ch-notif__title">{notification.title}</p>
                    </div>
                    <div className="ch-notif__actions">
                      {notification.url && (
                        <a
                          className="ch-btn ch-btn--sm ch-btn--ghost"
                          href={notification.url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          출처
                        </a>
                      )}
                      {!notification.isRead && (
                        <button
                          type="button"
                          className="ch-btn ch-btn--sm ch-btn--ghost"
                          onClick={() => onMarkRead(notification.id)}
                        >
                          읽음
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              )
            })
          )}

          <div className="ch-menu__divider" />
          <button
            type="button"
            className="ch-menu__item"
            role="menuitem"
            onClick={() => { setOpen(false); navigate('/settings') }}
          >
            알림 설정에서 전체 보기 <span aria-hidden="true">→</span>
          </button>
        </div>
      )}
    </div>
  )
}

export default NotificationBell
