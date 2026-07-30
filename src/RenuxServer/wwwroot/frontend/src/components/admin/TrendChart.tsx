export interface TrendPoint {
  label: string
  value: number
  /** 두 번째 계열(예: 만족도). 없으면 단일 계열로 그린다. */
  secondary?: number | null
}

interface TrendChartProps {
  points: TrendPoint[]
  height?: number
  /** 값 축 라벨 형식 — 개수/퍼센트 등 */
  formatValue?: (value: number) => string
  ariaLabel: string
}

const WIDTH = 640
const PADDING = { top: 12, right: 12, bottom: 22, left: 34 }

/**
 * 의존성 없이 SVG로 그리는 추이 차트.
 * 차트 라이브러리를 추가하면 번들이 커지고 챗봇 초기 로딩까지 느려지므로,
 * 관리자 화면에 필요한 최소 표현(면적+선+끝점 강조)만 직접 그린다.
 */
const TrendChart = ({ points, height = 160, formatValue, ariaLabel }: TrendChartProps) => {
  if (points.length === 0) {
    return <p className="ac-empty">표시할 데이터가 없습니다.</p>
  }

  const innerWidth = WIDTH - PADDING.left - PADDING.right
  const innerHeight = height - PADDING.top - PADDING.bottom

  const values = points.map((point) => point.value)
  const maxValue = Math.max(...values, 1)
  const stepX = points.length > 1 ? innerWidth / (points.length - 1) : 0

  const toX = (index: number) => PADDING.left + (points.length > 1 ? index * stepX : innerWidth / 2)
  const toY = (value: number) => PADDING.top + innerHeight - (value / maxValue) * innerHeight

  const linePath = points
    .map((point, index) => `${index === 0 ? 'M' : 'L'}${toX(index).toFixed(1)},${toY(point.value).toFixed(1)}`)
    .join(' ')

  const areaPath = `${linePath} L${toX(points.length - 1).toFixed(1)},${(PADDING.top + innerHeight).toFixed(1)} L${toX(0).toFixed(1)},${(PADDING.top + innerHeight).toFixed(1)} Z`

  const hasSecondary = points.some((point) => point.secondary != null)
  const secondaryPath = hasSecondary
    ? points
        .map((point, index) => {
          // 두 번째 계열은 0~100 비율 스케일로 고정해 첫 계열과 축을 공유하지 않는다.
          const ratio = (point.secondary ?? 0) / 100
          const y = PADDING.top + innerHeight - ratio * innerHeight
          return `${index === 0 ? 'M' : 'L'}${toX(index).toFixed(1)},${y.toFixed(1)}`
        })
        .join(' ')
    : null

  const lastIndex = points.length - 1
  // 라벨이 겹치지 않도록 최대 6개만 표시한다.
  const labelStride = Math.max(1, Math.ceil(points.length / 6))
  const format = formatValue ?? ((value: number) => value.toLocaleString('ko-KR'))

  return (
    <svg
      className="ac-chart"
      viewBox={`0 0 ${WIDTH} ${height}`}
      role="img"
      aria-label={ariaLabel}
      preserveAspectRatio="none"
    >
      {[0, 0.5, 1].map((ratio) => {
        const y = PADDING.top + innerHeight * ratio
        return (
          <g key={ratio}>
            <line className="ac-chart__grid" x1={PADDING.left} y1={y} x2={WIDTH - PADDING.right} y2={y} />
            <text className="ac-chart__label" x={4} y={y + 3.5}>
              {format(Math.round(maxValue * (1 - ratio)))}
            </text>
          </g>
        )
      })}

      <path className="ac-chart__area" d={areaPath} />
      <path className="ac-chart__line" d={linePath} />
      {secondaryPath && <path className="ac-chart__line ac-chart__line--alt" d={secondaryPath} />}
      <circle className="ac-chart__dot" cx={toX(lastIndex)} cy={toY(points[lastIndex].value)} r={3.5} />

      {points.map((point, index) => (
        index % labelStride === 0 || index === lastIndex ? (
          <text
            key={point.label}
            className="ac-chart__label"
            x={toX(index)}
            y={height - 6}
            textAnchor={index === 0 ? 'start' : index === lastIndex ? 'end' : 'middle'}
          >
            {point.label}
          </text>
        ) : null
      ))}
    </svg>
  )
}

export default TrendChart
