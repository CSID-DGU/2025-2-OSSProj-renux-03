const pad = (value: number) => value.toString().padStart(2, '0')

const fallbackFilename = (now: Date) => (
  `rag_evaluation_logs_${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}.csv`
)

const safeCsvFilename = (value: string, fallback: string) => {
  const withoutPath = value.split(/[/\\]/).at(-1)?.trim() ?? ''
  const invalidCharacters = '<>:"|?*'
  const sanitized = Array.from(withoutPath, (character) => (
    character.charCodeAt(0) <= 31 || invalidCharacters.includes(character) ? '_' : character
  )).join('')
  if (!sanitized) return fallback
  return sanitized.toLowerCase().endsWith('.csv') ? sanitized : `${sanitized}.csv`
}

export const getCsvFilename = (contentDisposition: string | null, now = new Date()) => {
  const fallback = fallbackFilename(now)
  if (!contentDisposition) return fallback

  const encodedMatch = contentDisposition.match(/filename\*\s*=\s*(?:UTF-8'')?([^;]+)/i)
  if (encodedMatch?.[1]) {
    const encodedValue = encodedMatch[1].trim().replace(/^"|"$/g, '')
    try {
      return safeCsvFilename(decodeURIComponent(encodedValue), fallback)
    } catch {
      return safeCsvFilename(encodedValue, fallback)
    }
  }

  const quotedMatch = contentDisposition.match(/filename\s*=\s*"([^"]+)"/i)
  const plainMatch = contentDisposition.match(/filename\s*=\s*([^;]+)/i)
  const value = quotedMatch?.[1] ?? plainMatch?.[1]?.trim()
  return value ? safeCsvFilename(value, fallback) : fallback
}

export interface BlobDownloadEnvironment {
  createObjectUrl: (blob: Blob) => string
  revokeObjectUrl: (url: string) => void
  createAnchor: () => HTMLAnchorElement
  appendAnchor: (anchor: HTMLAnchorElement) => void
  scheduleCleanup: (cleanup: () => void) => void
}

const browserDownloadEnvironment = (): BlobDownloadEnvironment => ({
  createObjectUrl: (blob) => URL.createObjectURL(blob),
  revokeObjectUrl: (url) => URL.revokeObjectURL(url),
  createAnchor: () => document.createElement('a'),
  appendAnchor: (anchor) => document.body.appendChild(anchor),
  scheduleCleanup: (cleanup) => {
    window.setTimeout(cleanup, 0)
  },
})

export const triggerBlobDownload = (
  blob: Blob,
  filename: string,
  environment = browserDownloadEnvironment(),
) => {
  const objectUrl = environment.createObjectUrl(blob)
  let anchor: HTMLAnchorElement | null = null
  let cleanedUp = false
  const cleanup = () => {
    if (cleanedUp) return
    cleanedUp = true
    anchor?.remove()
    environment.revokeObjectUrl(objectUrl)
  }

  try {
    anchor = environment.createAnchor()
    anchor.href = objectUrl
    anchor.download = filename
    anchor.style.display = 'none'
    environment.appendAnchor(anchor)
    anchor.click()
    // 일부 브라우저는 click 처리 뒤에 Blob URL을 소비하므로 다음 task까지 유지한다.
    environment.scheduleCleanup(cleanup)
  } catch (error) {
    cleanup()
    throw error
  }
}
