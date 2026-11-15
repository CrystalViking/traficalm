import { crowdColor, crowdLabel, crowdLevel } from '../utils/format'
import { useSettings } from '../settings'

interface Props {
  value: number
  compact?: boolean
}

export function CrowdBadge({ value, compact = false }: Props) {
  const { t, external } = useSettings()
  const clamped = Math.max(0, Math.min(100, Math.round(value)))
  return (
    <span
      className={`crowd-badge crowd-${crowdLevel(clamped)}`}
      style={{ backgroundColor: crowdColor(clamped) }}
      title={t('{level} – crowd index {value}%', { level: external(crowdLabel(clamped)), value: clamped })}
      aria-label={t('Crowd index {value}% ({level})', { value: clamped, level: external(crowdLabel(clamped)).toLowerCase() })}
    >
      {compact ? `${clamped}%` : `👥 ${clamped}%`}
    </span>
  )
}
