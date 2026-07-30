import { useEffect, useState } from 'react'
import { submitItem } from '../../admin/adminApi'
import { getApiErrorMessage } from '../../admin/format'
import Modal from './Modal'
import { useAdminConsole } from './adminConsoleContext'

export interface QuickFaqSeed {
  question: string
  answer: string
  /** 로그·피드백에서 온 경우 학과가 비어 있을 수 있어 기본값을 둔다. */
  category?: string | null
}

interface QuickFaqModalProps {
  seed: QuickFaqSeed | null
  onClose: () => void
}

/**
 * 로그·피드백 화면에서 발견한 질문을 그 자리에서 FAQ로 등록한다.
 * 지금까지는 로그를 보고 학과 화면으로 이동해 처음부터 다시 입력해야 했다.
 */
const QuickFaqModal = ({ seed, onClose }: QuickFaqModalProps) => {
  const { showToast, notifyDataChanged, userName, majorName } = useAdminConsole()
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState('')
  const [category, setCategory] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!seed) return
    setQuestion(seed.question ?? '')
    setAnswer(seed.answer ?? '')
    setCategory(seed.category ?? majorName ?? '공통')
  }, [seed, majorName])

  const submit = async () => {
    setBusy(true)
    try {
      await submitItem('custom_knowledge', {
        question: question.trim(),
        answer: answer.trim(),
        category: category.trim() || '공통',
        requester: userName || '관리자',
      })
      showToast('FAQ로 등록했습니다. 검수함에서 승인하면 챗봇에 반영됩니다.', 'success')
      notifyDataChanged()
      onClose()
    } catch (error) {
      showToast(getApiErrorMessage(error, 'FAQ 등록에 실패했습니다.'), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={seed !== null}
      title="FAQ로 등록"
      description="질문과 답변을 확인하고 등록하세요. 등록 후 검수함에서 승인해야 챗봇에 반영됩니다."
      confirmLabel="등록"
      busy={busy}
      confirmDisabled={question.trim().length === 0 || answer.trim().length === 0}
      onCancel={onClose}
      onConfirm={submit}
    >
      <div className="ac-field">
        <label className="ac-label" htmlFor="quick-faq-question">질문</label>
        <input
          id="quick-faq-question"
          type="text"
          className="ac-input"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
        />
      </div>
      <div className="ac-field">
        <label className="ac-label" htmlFor="quick-faq-answer">답변</label>
        <textarea
          id="quick-faq-answer"
          className="ac-textarea"
          rows={7}
          value={answer}
          onChange={(event) => setAnswer(event.target.value)}
          placeholder="챗봇이 이 질문에 답할 내용을 정확하게 작성하세요."
        />
      </div>
      <div className="ac-field">
        <label className="ac-label" htmlFor="quick-faq-category">학과·분류</label>
        <input
          id="quick-faq-category"
          type="text"
          className="ac-input"
          value={category}
          onChange={(event) => setCategory(event.target.value)}
          placeholder="예: 통계학과 · 공통"
        />
      </div>
    </Modal>
  )
}

export default QuickFaqModal
