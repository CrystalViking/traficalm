import { useState } from 'react'
import type { Itinerary } from '../types'
import { sortByCrowd } from '../utils/sort'
import { formatDistance, formatDuration, formatTime } from '../utils/format'
import { CrowdBadge } from './CrowdBadge'
import { useSettings } from '../settings'

interface Props {
  itineraries: Itinerary[]
  selectedId: string | null
  onSelect: (id: string) => void
  onHover?: (id: string | null) => void
  limit?: number
  showWalkTransfers?: boolean
}

function summary(it: Itinerary): string {
  if (it.type === 'walk') return 'Walk'
  return it.legs
    .filter((leg) => leg.mode === 'transit' && leg.route)
    .map((leg) => `${leg.route!.mode === 'tram' ? '🚋' : '🚌'} ${leg.route!.short_name}`)
    .join(' → ')
}

export function RouteOptions({ itineraries, selectedId, onSelect, onHover, limit, showWalkTransfers = false }: Props) {
  const { t } = useSettings()
  const [expanded, setExpanded] = useState(false)
  if (itineraries.length === 0) {
    return <p className="hint">{t('No connections found for this time. Try another departure time.')}</p>
  }
  const sorted = sortByCrowd(itineraries)
  const collapsed = limit !== undefined && !expanded && sorted.length > limit
  const visible = collapsed ? sorted.slice(0, limit) : sorted
  return (
    <>
    <ol className="route-options" aria-label={t('Transport options sorted by crowd index (lowest first)')} onMouseLeave={() => onHover?.(null)}>
      {visible.map((it) => (
        <li key={it.id}>
          <button
            type="button"
            className={`route-option${it.id === selectedId ? ' selected' : ''}`}
            aria-pressed={it.id === selectedId}
            onClick={() => onSelect(it.id)}
            onMouseEnter={() => onHover?.(it.id)}
            onFocus={() => onHover?.(it.id)}
            onBlur={() => onHover?.(null)}
          >
            <div className="option-header">
              <CrowdBadge value={it.crowd_index} />
              <span className="option-lines">{it.type === 'walk' ? t('Walk') : summary(it)}</span>
            </div>
            <div className="option-meta">
              <span>
                {formatTime(it.departure)} – {formatTime(it.arrival)}
              </span>
              <span>{formatDuration(it.duration_seconds)}</span>
              <span>{it.type === 'walk' ? t('walking only') : it.transfers === 0 ? t('direct') : t(it.transfers === 1 ? '{count} change' : '{count} changes', { count: it.transfers })}</span>
              <span>🚶 {formatDistance(it.walk_distance_m)}</span>
            </div>
            {it.crowd_avoiding && <div className="option-tag">🌿 {t('Avoids crowded stops')}</div>}
            {showWalkTransfers && it.legs.map((leg, i) => {
              const next = it.legs[i + 1]
              return leg.mode === 'walk' && next?.mode === 'transit' ? (
                <div key={i} className="option-tag">
                  {t('Walk {distance} to {stop} · take {line}', { distance: formatDistance(leg.distance_m), stop: leg.end.name, line: next.route?.short_name ?? '' })}
                </div>
              ) : null
            })}
          </button>
        </li>
      ))}
    </ol>
    {limit !== undefined && sorted.length > limit && (
      <button type="button" className="show-more" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        {expanded ? t('Show fewer options') : t('Show all {count} options', { count: sorted.length })}
      </button>
    )}
    </>
  )
}
