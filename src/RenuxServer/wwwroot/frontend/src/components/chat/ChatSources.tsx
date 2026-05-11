import type { ChatSource } from '../../types/chat'

interface ChatSourcesProps {
  sources?: ChatSource[] | null
  citations?: string | null
}

const sourceLabelMap: Record<string, string> = {
  notices: '공지',
  rules: '규정',
  schedule: '학사일정',
  courses: '교과목',
  staff: '교직원',
  dongguk_official: '동국대 공식',
}

const getDisplayLabel = (source?: string) => {
  if (!source) return '출처'
  return sourceLabelMap[source] ?? source
}

interface SourceCard {
  key: string
  title: string
  publishedAt?: string
  typeLabel: string
  url?: string
  department?: string
  snippet?: string
}

const stringValue = (value: unknown) => {
  if (typeof value !== 'string') return undefined
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : undefined
}

const firstString = (...values: unknown[]): string | undefined => {
  for (const value of values) {
    if (Array.isArray(value)) {
      const nested: string | undefined = firstString(...value)
      if (nested) return nested
      continue
    }

    if (typeof value === 'string') {
      try {
        const parsed = value.trim().startsWith('[') ? JSON.parse(value) : null
        if (Array.isArray(parsed)) {
          const nested: string | undefined = firstString(...parsed)
          if (nested) return nested
        }
      } catch {
        // Not a serialized array; ignore.
      }
    }

    const normalized = stringValue(value)
    if (normalized) return normalized
  }
  return undefined
}

const publicUrl = (value?: string) => {
  if (!value) return undefined
  return value.startsWith('http://') || value.startsWith('https://') ? value : undefined
}

const parseCitationLine = (line: string, index: number): SourceCard | null => {
  const cleaned = line.replace(/^[-*]\s*/, '').trim()
  if (!cleaned) return null

  const [titlePart, urlPart] = cleaned.split(/\s+[—-]\s+(https?:\/\/\S+)/)
  const url = firstString(urlPart)
  const dateMatch = titlePart.match(/\((\d{4}[-.]\d{1,2}[-.]\d{1,2})\)\s*$/)
  const title = titlePart.replace(/\((\d{4}[-.]\d{1,2}[-.]\d{1,2})\)\s*$/, '').trim()

  return {
    key: `citation-${index}-${url ?? title}`,
    title: title || cleaned,
    publishedAt: dateMatch?.[1],
    typeLabel: '출처',
    url,
  }
}

const normalizeSource = (item: ChatSource, index: number): SourceCard => {
  const metadata = item.metadata ?? {}
  const title =
    firstString(metadata.title, metadata.topics, metadata.doc_id, metadata.file_name, metadata.filename) ?? '제목 없음'
  const publishedAt = firstString(metadata.published_at, metadata.date, metadata.updated_at)
  const url = publicUrl(firstString(metadata.source_url, metadata.url, metadata.link, metadata.attachment_url, metadata.attachment_urls))
  const category = firstString(metadata.category, metadata.document_type)
  const department = firstString(metadata.department, metadata.dept, metadata.campus)
  const snippet = firstString(item.snippet)?.replace(/\s+/g, ' ').slice(0, 260)

  return {
    key: `${url ?? title}-${index}`,
    title,
    publishedAt,
    typeLabel: category ? `${getDisplayLabel(item.source)} · ${category}` : getDisplayLabel(item.source),
    url,
    department,
    snippet,
  }
}

const buildCards = (sources?: ChatSource[] | null, citations?: string | null) => {
  const cards =
    sources
      ?.map(normalizeSource)
      .filter((card, index, list) => {
        const duplicateKey = card.url ?? `${card.title}-${card.publishedAt ?? ''}`
        return list.findIndex((item) => (item.url ?? `${item.title}-${item.publishedAt ?? ''}`) === duplicateKey) === index
      })
      .slice(0, 5) ?? []

  if (cards.length > 0) return cards

  return (
    citations
      ?.split('\n')
      .map(parseCitationLine)
      .filter((card): card is SourceCard => Boolean(card))
      .slice(0, 5) ?? []
  )
}

const ChatSources = ({ sources, citations }: ChatSourcesProps) => {
  const cards = buildCards(sources, citations)

  if (cards.length === 0) {
    return null
  }

  return (
    <details className="chat-sources">
      <summary className="chat-sources__summary">
        <span className="chat-sources__heading">참고한 출처</span>
        <span className="chat-sources__count">{cards.length}개</span>
      </summary>
      <ul className="chat-sources__list">
        {cards.map((card) => {
          return (
            <li key={card.key} className="chat-sources__card">
              <div className="chat-sources__meta">
                <span className="chat-sources__badge">{card.typeLabel}</span>
                {card.publishedAt && <span className="chat-sources__date">{card.publishedAt}</span>}
              </div>
              <strong className="chat-sources__title">{card.title}</strong>
              {card.department && <span className="chat-sources__department">{card.department}</span>}
              {card.snippet && <p className="chat-sources__snippet">{card.snippet}</p>}
              {card.url ? (
                <a className="chat-sources__link" href={card.url} target="_blank" rel="noopener noreferrer">
                  원문 보기
                </a>
              ) : (
                <span className="chat-sources__link chat-sources__link--muted">원문 링크 없음</span>
              )}
            </li>
          )
        })}
      </ul>
    </details>
  )
}

export default ChatSources
