const dotnetEpochTicks = 621355968000000000
const dotnetTickThreshold = 100000000000000

const ticksToDate = (ticks: number) => new Date((ticks - dotnetEpochTicks) / 10000)

export const toDate = (value?: string | number | null) => {
  if (value === null || value === undefined || value === '') return null

  if (typeof value === 'number') {
    return value > dotnetTickThreshold ? ticksToDate(value) : new Date(value)
  }

  const numericValue = Number(value)
  if (Number.isFinite(numericValue) && value.trim() !== '') {
    return numericValue > dotnetTickThreshold ? ticksToDate(numericValue) : new Date(numericValue)
  }

  return new Date(value)
}

export const formatChatTime = (value?: string | number | null) => {
  const date = toDate(value)
  if (!date || Number.isNaN(date.getTime())) return ''

  return new Intl.DateTimeFormat('ko-KR', {
    hour: 'numeric',
    minute: '2-digit',
  }).format(date)
}
