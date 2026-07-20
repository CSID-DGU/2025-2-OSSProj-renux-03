import { useEffect } from 'react'
import { trackSuggestionEvent } from '../../chat/productTelemetry'

type SuggestedQuestionsProps = {
  questions: string[]
  requestId?: string
  disabled?: boolean
  onSelect: (q: string) => void
}

const SuggestedQuestions = ({ questions, requestId, disabled = false, onSelect }: SuggestedQuestionsProps) => {
  useEffect(() => {
    if (disabled || !requestId || questions.length === 0) return
    trackSuggestionEvent('suggestion_shown', requestId, questions.length)
  }, [disabled, questions.length, requestId])

  if (questions.length === 0) return null

  return (
    <div className="suggested-questions" aria-label="추천 질문">
      <div className="suggested-questions__heading">추천 질문</div>
      <div className="suggested-questions__list">
        {questions.map((question, index) => (
          <button
            key={question}
            type="button"
            className="suggested-questions__chip"
            onClick={() => {
              if (requestId) trackSuggestionEvent('suggestion_clicked', requestId, index)
              onSelect(question)
            }}
            disabled={disabled}
          >
            {question}
          </button>
        ))}
      </div>
    </div>
  )
}

export default SuggestedQuestions
