import { useNavigate } from 'react-router-dom'
import type { HomeBriefing as HomeBriefingData } from '../../types/briefing'
import type { DeadlineItem } from '../../types/notification'

interface HomeBriefingProps {
  greeting: string
  subtitle: string
  briefing: HomeBriefingData | null
  briefingLoading: boolean
  deadlines: DeadlineItem[]
  isAuthenticated: boolean
  starterQuestions: string[]
  busy: boolean
  /** 위젯·칩을 누르면 해당 질문으로 바로 대화를 시작한다. */
  onAsk: (question: string) => void
  showGuide: boolean
  onDismissGuide: () => void
  /** 브라우저가 설치 가능하다고 알린 경우에만 true. */
  canInstall: boolean
  onInstall: () => void
  onDismissInstall: () => void
}

const formatDday = (dDay: number) => {
  if (dDay === 0) return 'D-day'
  if (dDay > 0) return `D-${dDay}`
  return `D+${Math.abs(dDay)}`
}

const ddayTone = (dDay: number) => {
  if (dDay <= 0) return 'danger'
  if (dDay <= 3) return 'danger'
  if (dDay <= 7) return 'warn'
  return 'info'
}

/**
 * 홈의 '오늘' 브리핑.
 * 정보 제공 챗봇인데도 첫 화면에서 아무 정보도 보이지 않던 문제를 해결하는 화면으로,
 * 모든 카드는 곧바로 질문으로 이어져 브리핑과 대화가 끊기지 않게 한다.
 */
const HomeBriefing = ({
  greeting,
  subtitle,
  briefing,
  briefingLoading,
  deadlines,
  isAuthenticated,
  starterQuestions,
  busy,
  onAsk,
  showGuide,
  onDismissGuide,
  canInstall,
  onInstall,
  onDismissInstall,
}: HomeBriefingProps) => {
  const navigate = useNavigate()
  const nearestDeadline = deadlines[0]
  const meals = briefing?.meals ?? []
  const schedules = briefing?.schedules ?? []
  const notices = briefing?.notices ?? []

  return (
    <div className="ch-home">
      <div>
        <h2 className="ch-home__greeting">{greeting}</h2>
        <p className="ch-home__sub">{subtitle}</p>
      </div>

      <div className="ch-widgets">
        <button
          type="button"
          className={`ch-widget ${meals.length === 0 ? 'ch-widget--empty' : ''}`}
          onClick={() => onAsk('오늘 학식 뭐 나와?')}
          disabled={busy}
        >
          <span className="ch-widget__label"><span aria-hidden="true">🍚</span> 오늘 학식</span>
          {briefingLoading ? (
            <span className="ch-widget__lines"><span>불러오는 중…</span></span>
          ) : meals.length === 0 ? (
            <span className="ch-widget__lines"><span>등록된 식단이 없어요</span></span>
          ) : (
            <>
              <span className="ch-widget__value">{meals[0].corner}</span>
              <span className="ch-widget__lines">
                {meals.slice(0, 2).map((meal, index) => <span key={`${meal.corner}-${index}`}>{meal.menu}</span>)}
              </span>
            </>
          )}
          <span className="ch-widget__cta">자세히 물어보기 →</span>
        </button>

        <button
          type="button"
          className={`ch-widget ${schedules.length === 0 ? 'ch-widget--empty' : ''}`}
          onClick={() => onAsk('이번 주 학사일정 알려줘')}
          disabled={busy}
        >
          <span className="ch-widget__label"><span aria-hidden="true">📅</span> 학사일정</span>
          {briefingLoading ? (
            <span className="ch-widget__lines"><span>불러오는 중…</span></span>
          ) : schedules.length === 0 ? (
            <span className="ch-widget__lines"><span>이번 주 일정이 없어요</span></span>
          ) : (
            <>
              <span className="ch-widget__value">{schedules[0].title}</span>
              <span className="ch-widget__lines">
                <span>{schedules[0].period}</span>
                {schedules[1] && <span>{schedules[1].title}</span>}
              </span>
            </>
          )}
          <span className="ch-widget__cta">이번 주 일정 보기 →</span>
        </button>

        {isAuthenticated ? (
          <button
            type="button"
            className={`ch-widget ${!nearestDeadline ? 'ch-widget--empty' : ''}`}
            onClick={() => navigate('/settings')}
          >
            <span className="ch-widget__label"><span aria-hidden="true">⏰</span> 내 마감</span>
            {!nearestDeadline ? (
              <span className="ch-widget__lines"><span>다가오는 마감이 없어요</span></span>
            ) : (
              <>
                <span className="ch-widget__value">
                  <span className={`ch-badge ch-badge--${ddayTone(nearestDeadline.dDay)}`}>
                    {formatDday(nearestDeadline.dDay)}
                  </span>{' '}
                  {nearestDeadline.title}
                </span>
                <span className="ch-widget__lines">
                  {deadlines[1] && <span>{formatDday(deadlines[1].dDay)} · {deadlines[1].title}</span>}
                </span>
              </>
            )}
            <span className="ch-widget__cta">알림 설정에서 보기 →</span>
          </button>
        ) : (
          <button type="button" className="ch-widget ch-widget--empty" onClick={() => navigate('/auth/in')}>
            <span className="ch-widget__label"><span aria-hidden="true">⏰</span> 내 마감</span>
            <span className="ch-widget__lines"><span>로그인하면 관심 주제의<br />마감을 D-day로 챙겨드려요</span></span>
            <span className="ch-widget__cta">로그인 →</span>
          </button>
        )}

        <button
          type="button"
          className={`ch-widget ${notices.length === 0 ? 'ch-widget--empty' : ''}`}
          onClick={() => onAsk('오늘 올라온 공지 요약해줘')}
          disabled={busy}
        >
          <span className="ch-widget__label"><span aria-hidden="true">📢</span> 최신 공지</span>
          {briefingLoading ? (
            <span className="ch-widget__lines"><span>불러오는 중…</span></span>
          ) : notices.length === 0 ? (
            <span className="ch-widget__lines"><span>새 공지가 없어요</span></span>
          ) : (
            <>
              <span className="ch-widget__value">{notices[0].title}</span>
              <span className="ch-widget__lines">
                {notices[1] && <span>{notices[1].title}</span>}
              </span>
            </>
          )}
          <span className="ch-widget__cta">공지 요약 받기 →</span>
        </button>
      </div>

      {canInstall && (
        <div className="ch-install">
          <span className="ch-install__text">
            동똑이를 홈 화면에 추가하면 앱처럼 바로 열 수 있어요.
          </span>
          <div className="ch-install__actions">
            <button type="button" className="ch-btn ch-btn--sm ch-btn--ghost" onClick={onDismissInstall}>
              나중에
            </button>
            <button type="button" className="ch-btn ch-btn--sm ch-btn--primary" onClick={onInstall}>
              홈 화면에 추가
            </button>
          </div>
        </div>
      )}

      {starterQuestions.length > 0 && (
        <div className="ch-starters">
          <span className="ch-starters__heading">
            {isAuthenticated ? '나에게 맞는 질문' : '바로 물어볼 질문'}
          </span>
          <div className="ch-chips">
            {starterQuestions.map((question) => (
              <button
                key={question}
                type="button"
                className="ch-chip"
                onClick={() => onAsk(question)}
                disabled={busy}
              >
                {question}
              </button>
            ))}
          </div>
        </div>
      )}

      {showGuide && (
        <details className="ch-guide" open onToggle={(event) => { if (!event.currentTarget.open) onDismissGuide() }}>
          <summary>동똑이 처음이신가요?</summary>
          <ol className="ch-guide__body">
            <li>
              <strong>바로 물어보세요</strong>
              <p>아래 입력창에 질문을 쓰면 대화가 자동으로 시작됩니다. 따로 방을 만들 필요가 없어요.</p>
            </li>
            <li>
              <strong>출처를 확인하세요</strong>
              <p>답변마다 학교 공지·학칙 원문을 함께 보여드립니다. 답변 아래 “출처”를 펼쳐 확인할 수 있어요.</p>
            </li>
            <li>
              <strong>로그인하면 더 정확해집니다</strong>
              <p>대화 내역이 저장되고, 소속 학과에 맞는 답변과 마감 D-day 알림을 받을 수 있어요.</p>
            </li>
          </ol>
        </details>
      )}
    </div>
  )
}

export default HomeBriefing
