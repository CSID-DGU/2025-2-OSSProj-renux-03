/**
 * 답변 본문의 인용 표기를 링크로 바꾸는 규칙.
 * 컴포넌트에서 분리해 두어야 fast-refresh가 깨지지 않고 단위 테스트도 가능하다.
 */

/** 내부 전용 스킴. 실제 이동이 아니라 '출처 카드로 스크롤' 동작을 나타낸다. */
export const CITATION_LINK_PREFIX = 'dongttok-citation:'

/**
 * `[문서3]` 또는 `[3]` 표기를 클릭 가능한 링크 문법으로 바꾼다.
 * 이미 링크인 `[3](...)`는 건드리지 않고, 0 이하나 숫자가 아닌 값은 원문을 유지한다.
 */
export const toCitationMarkdown = (content: string) =>
  content.replace(/\[(?:문서)?(\d{1,2})\](?!\()/g, (_, citationNumber: string) => {
    const normalized = Number(citationNumber)
    if (!Number.isInteger(normalized) || normalized < 1) return `[${citationNumber}]`
    return `[문서${normalized}](${CITATION_LINK_PREFIX}${normalized})`
  })

/** 링크 href에서 인용 번호를 읽는다. 인용 링크가 아니면 null. */
export const parseCitationNumber = (href?: string | null): number | null => {
  if (!href?.startsWith(CITATION_LINK_PREFIX)) return null
  const parsed = Number(href.slice(CITATION_LINK_PREFIX.length))
  return Number.isInteger(parsed) && parsed >= 1 ? parsed : null
}
