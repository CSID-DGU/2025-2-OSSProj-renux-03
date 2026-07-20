import type {
  AdminItemResponse,
  DepartmentKnowledge,
  PendingAnswerReview,
} from '../types/admin'

const parseItemData = (data: string): Record<string, unknown> => {
  try {
    const parsed = JSON.parse(data) as unknown
    return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {}
  } catch {
    return {}
  }
}

const readText = (data: Record<string, unknown>, key: string) => {
  const value = data[key]
  return typeof value === 'string' ? value : ''
}

const getItemDisplay = (item: AdminItemResponse) => {
  const data = parseItemData(item.data)

  if (item.source_type === 'custom_knowledge') {
    return {
      title: readText(data, 'question') || '질문 없음',
      content: readText(data, 'answer'),
      department: readText(data, 'category') || '공통',
      requester: readText(data, 'requester') || '정보 없음',
    }
  }

  if (item.source_type === 'event') {
    return {
      title: `[행사] ${readText(data, 'title')}`,
      content: `일시: ${readText(data, 'start_date')} ~ ${readText(data, 'end_date')}\n장소: ${readText(data, 'location')}\n\n${readText(data, 'description')}`,
      department: readText(data, 'department') || '공통',
      requester: readText(data, 'requester') || '정보 없음',
    }
  }

  if (item.source_type === 'announcement') {
    return {
      title: `[공지] ${readText(data, 'title')}`,
      content: `게시일: ${readText(data, 'date')}\n분류: ${readText(data, 'category')}\n\n${readText(data, 'content')}`,
      department: readText(data, 'department') || '공통',
      requester: readText(data, 'requester') || '정보 없음',
    }
  }

  return {
    title: '제목 없음',
    content: '',
    department: '공통',
    requester: readText(data, 'requester') || '정보 없음',
  }
}

export const toPendingAnswerReview = (item: AdminItemResponse): PendingAnswerReview => {
  const display = getItemDisplay(item)
  return {
    id: item.id.toString(),
    departmentName: display.department,
    submittedAt: item.created_at,
    handler: display.requester,
    question: display.title,
    answer: display.content,
    status: item.status,
  }
}

export const toDepartmentKnowledge = (item: AdminItemResponse): DepartmentKnowledge => {
  const display = getItemDisplay(item)
  const status = item.status === 'approved' || item.status === 'approved_manually'
    ? 'APPROVED'
    : item.status === 'rejected'
      ? 'REJECTED'
      : 'PENDING'

  return {
    id: item.id.toString(),
    title: display.title,
    content: display.content,
    status,
    createdAt: item.created_at,
  }
}
