import type { ApiError } from '../api/client'

interface ValidationProblemDetails {
  errors?: Record<string, string[] | string>
  message?: string
  title?: string
  detail?: string
}

const fieldLabelMap: Record<string, string> = {
  UserId: '아이디',
  Password: '비밀번호',
  Username: '이름',
  MajorId: '전공',
  RoleId: '역할',
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null

export const getApiErrorMessage = (
  error: unknown,
  fallback = '요청 처리 중 오류가 발생했습니다.',
) => {
  if (!isRecord(error) || !('status' in error)) {
    return error instanceof Error ? error.message : fallback
  }

  const apiError = error as unknown as ApiError
  const details = apiError.details as ValidationProblemDetails | undefined

  if (details?.errors && isRecord(details.errors)) {
    const messages = Object.entries(details.errors).flatMap(([field, value]) => {
      const label = fieldLabelMap[field] ?? field
      const fieldMessages = Array.isArray(value) ? value : [value]
      return fieldMessages.filter(Boolean).map((message) => `${label}: ${message}`)
    })

    if (messages.length > 0) {
      return messages.join('\n')
    }
  }

  if (details?.message) return details.message
  if (details?.detail) return details.detail
  if (details?.title && apiError.status !== 400) return details.title

  return apiError.message ?? fallback
}
