import { useCallback, useEffect, useState } from 'react'

/** 브라우저가 설치 가능 시점에 발생시키는 이벤트. 표준 타입에 아직 없어 최소 형태만 정의한다. */
interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

const DISMISSED_KEY = 'renux-install-dismissed'

const readDismissed = () => {
  try {
    return window.localStorage.getItem(DISMISSED_KEY) === '1'
  } catch {
    return false
  }
}

/**
 * PWA 설치 유도.
 *
 * 브라우저가 설치 가능하다고 알려줄 때만(beforeinstallprompt) 노출하므로,
 * 이미 설치했거나 지원하지 않는 환경에서는 아무것도 보이지 않는다.
 * 한 번 닫으면 다시 묻지 않는다 — 매 방문마다 권유하면 방해가 된다.
 */
export const useInstallPrompt = () => {
  const [promptEvent, setPromptEvent] = useState<BeforeInstallPromptEvent | null>(null)
  const [dismissed, setDismissed] = useState(readDismissed)

  useEffect(() => {
    const handler = (event: Event) => {
      // 기본 미니 배너를 막고, 우리 UI에서 적절한 시점에 띄운다.
      event.preventDefault()
      setPromptEvent(event as BeforeInstallPromptEvent)
    }
    window.addEventListener('beforeinstallprompt', handler)

    // 설치가 끝나면 더 권유할 이유가 없다.
    const installedHandler = () => setPromptEvent(null)
    window.addEventListener('appinstalled', installedHandler)

    return () => {
      window.removeEventListener('beforeinstallprompt', handler)
      window.removeEventListener('appinstalled', installedHandler)
    }
  }, [])

  const install = useCallback(async () => {
    if (!promptEvent) return
    try {
      await promptEvent.prompt()
      await promptEvent.userChoice
    } catch {
      // 사용자가 취소했거나 브라우저가 거부한 경우 — 조용히 넘긴다.
    } finally {
      setPromptEvent(null)
    }
  }, [promptEvent])

  const dismiss = useCallback(() => {
    setDismissed(true)
    setPromptEvent(null)
    try {
      window.localStorage.setItem(DISMISSED_KEY, '1')
    } catch {
      // 저장 실패 시 다음 방문에 다시 보일 뿐이라 무시한다.
    }
  }, [])

  return { canInstall: promptEvent !== null && !dismissed, install, dismiss }
}
