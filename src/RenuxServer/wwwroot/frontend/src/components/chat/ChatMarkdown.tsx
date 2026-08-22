import { useMemo } from 'react'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import rehypeExternalLinks from 'rehype-external-links'
import remarkGfm from 'remark-gfm'
import { CITATION_LINK_PREFIX, parseCitationNumber, toCitationMarkdown } from '../../chat/citations'

type ChatMarkdownProps = {
  content: string
  onCitationClick?: (citationNumber: number) => void
}

/**
 * react-markdown v9의 기본 urlTransform은 허용 목록에 없는 스킴을 빈 문자열로 만든다.
 * 내부 인용 스킴(dongttok-citation:)도 여기서 걸러지면서 href가 비고, 인용 표기가
 * 클릭되지 않는 일반 링크로 렌더링됐다. 이 스킴만 통과시키고 나머지는 기본 규칙을 따른다.
 */
const citationAwareUrlTransform = (url: string) =>
  url.startsWith(CITATION_LINK_PREFIX) ? url : defaultUrlTransform(url)

const ChatMarkdown = ({ content, onCitationClick }: ChatMarkdownProps) => {
  const markdownContent = useMemo(() => toCitationMarkdown(content), [content])

  return (
    <ReactMarkdown
      className="chat-bubble__text"
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[[rehypeExternalLinks, { target: '_blank', rel: ['noopener', 'noreferrer'] }]]}
      urlTransform={citationAwareUrlTransform}
      components={{
        table: ({ node: _node, children, ...props }) => (
          <div className="chat-markdown__table-scroll" role="region" aria-label="답변 표" tabIndex={0}>
            <table {...props}>{children}</table>
          </div>
        ),
        a: ({ node: _node, href, children, ...props }) => {
          const citationNumber = parseCitationNumber(href)
          if (citationNumber !== null) {
            return (
              <button
                type="button"
                className="chat-citation-link"
                aria-label={`출처 ${citationNumber}번 보기`}
                onClick={(event) => {
                  event.stopPropagation()
                  onCitationClick?.(citationNumber)
                }}
              >
                {children}
              </button>
            )
          }

          return (
            <a
              {...props}
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(event) => event.stopPropagation()}
            >
              {children}
            </a>
          )
        },
      }}
    >
      {markdownContent}
    </ReactMarkdown>
  )
}

export default ChatMarkdown
