import { Fragment, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { Itinerary, StopCall, TripRealtime } from '../types'
import { formatDelay, formatDistance, formatDuration, formatTime } from '../utils/format'
import { CrowdBadge } from './CrowdBadge'
import { HelpTip } from './HelpTip'
import { SlideAction } from './SlideAction'
import { DEFAULT_CROWD_TOLERANCE, useSettings } from '../settings'
import { realtimeKey } from '../utils/realtime'

interface Props {
  itinerary: Itinerary
  realtime: Record<string, TripRealtime | undefined>
  crowdTolerance?: number
  onAvoidStop?: (stop: StopCall) => void
  editing?: boolean
  selectedStop?: StopCall | null
  onSelectStop?: (stop: StopCall | null) => void
  editingStop?: StopCall | null
  stopEditor?: ReactNode
}

export function ItineraryDetails({ itinerary, realtime, crowdTolerance = DEFAULT_CROWD_TOLERANCE, onAvoidStop, editing = false, selectedStop = null, onSelectStop, editingStop, stopEditor }: Props) {
  const { t, external, timeFormat, language } = useSettings()
  const [confirmedStop, setConfirmedStop] = useState<StopCall | null>(null)
  const [dismissedStops, setDismissedStops] = useState<Set<StopCall>>(() => new Set())
  const selectedRow = useRef<HTMLTableRowElement>(null)
  useEffect(() => {
    selectedRow.current?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' })
  }, [selectedStop])
  const crowdedStops = itinerary.legs.flatMap((leg) => leg.stops).filter((stop) => stop.crowd_index > crowdTolerance)
  return (
    <section className="itinerary-details" aria-label={t('Selected route details')}>
      <header>
        <h2>
          {formatTime(itinerary.departure, timeFormat, language)} – {formatTime(itinerary.arrival, timeFormat, language)} · {formatDuration(itinerary.duration_seconds)}
        </h2>
        <CrowdBadge value={itinerary.crowd_index} />
      </header>
      {crowdedStops.length > 0 && (
        <p className="crowd-warning" role="status">
          {t(crowdedStops.length === 1 ? '{count} stop visit exceeds your {tolerance}% crowd tolerance.' : '{count} stop visits exceed your {tolerance}% crowd tolerance.', { count: crowdedStops.length, tolerance: crowdTolerance })}{' '}
          {t('Select a stop to find a different line or a walking alternative.')}
        </p>
      )}
      {itinerary.notes.map((note) => (
        <p key={note} className="note">
          ℹ️ {external(note)}
        </p>
      ))}
      <ol className="legs">
        {itinerary.legs.map((leg, i) => {
          if (leg.mode === 'walk') {
            return (
              <li key={i} className="leg leg-walk">
                <div className="leg-title">
                  🚶 {t('Walk {distance} ({duration}) to {stop}', { distance: formatDistance(leg.distance_m), duration: formatDuration(leg.duration_seconds), stop: leg.end.name })}
                  <CrowdBadge value={leg.crowd_index} compact />
                </div>
                {leg.crowd_reasons.length > 0 && <div className="reasons">{leg.crowd_reasons.map(external).join(' · ')}</div>}
              </li>
            )
          }
          const rt = leg.trip_id ? realtime[realtimeKey(leg.trip_id, leg.service_date)] : undefined
          const live = new Map(rt?.stops.map((s) => [s.stop_sequence, s]) ?? [])
          return (
            <li key={i} className="leg leg-transit">
              <div className="leg-title">
                <span className="line" style={leg.route?.color ? { backgroundColor: `#${leg.route.color}` } : undefined}>
                  {leg.route?.mode === 'tram' ? '🚋' : '🚌'} {leg.route?.short_name}
                </span>
                <span>→ {leg.headsign}</span>
                <CrowdBadge value={leg.crowd_index} compact />
                {rt?.has_realtime && <span className="live" title={t('Real-time data from ZTM GTFS-RT')}>● {t('live')}</span>}
              </div>
              {leg.crowd_reasons.length > 0 && <div className="reasons">{leg.crowd_reasons.map(external).join(' · ')}</div>}
              {leg.crowd_laya !== null && (
                <div className="reasons">
                  {t('Rule-based {rule}% · Laya AI {laya}%', { rule: leg.crowd_rule_based ?? '–', laya: leg.crowd_laya })}
                </div>
              )}
              <table className="stops">
                <thead>
                  <tr>
                    <th scope="col">{t('Stop')}</th>
                    <th scope="col">{t('Planned')}</th>
                    <th scope="col">{t('Real-time')}</th>
                    <th scope="col">{t('Crowd')}</th>
                  </tr>
                </thead>
                <tbody>
                  {leg.stops.map((stop, j) => {
                    const isFirst = j === 0
                    const update = live.get(stop.stop_sequence)
                    const delay = update ? update.delay_seconds : stop.delay_seconds
                    const planned = isFirst ? stop.scheduled_departure : stop.scheduled_arrival
                    const actual = update
                      ? isFirst
                        ? update.realtime_departure
                        : update.realtime_arrival
                      : isFirst
                        ? stop.realtime_departure
                        : stop.realtime_arrival
                    const aboveTolerance = stop.crowd_index > crowdTolerance
                    const expanded = selectedStop === stop && (!aboveTolerance || confirmedStop === stop)
                    return (
                      <Fragment key={`${stop.stop_id}-${stop.stop_sequence}`}>
                      <tr ref={selectedStop === stop ? selectedRow : undefined} className={aboveTolerance ? 'above-tolerance' : undefined}>
                        <td>{onSelectStop ? (
                          <button type="button" className="stop-name" aria-expanded={selectedStop === stop}
                            onClick={() => onSelectStop(selectedStop === stop ? null : stop)}>
                            {stop.name}
                          </button>
                        ) : stop.name}</td>
                        <td className="slide-host">{formatTime(planned, timeFormat, language)}
                          {onAvoidStop && (
                            <SlideAction open={expanded && !(editingStop === stop && stopEditor)}>
                              <button type="button" className="avoid-stop" disabled={editing} onClick={() => onAvoidStop(stop)}
                                aria-label={t('Find alternatives avoiding {stop}', { stop: stop.name })}>
                                {t('Avoid stop')}
                              </button>
                              <HelpTip text={t('Find another line or walk to a nearby stop to continue your journey.')} />
                            </SlideAction>
                          )}
                        </td>
                        <td className={delay && delay > 60 ? 'late' : undefined}>
                          {actual ? `${formatTime(actual, timeFormat, language)} (${external(formatDelay(delay))})` : '–'}
                        </td>
                        <td>
                          <CrowdBadge value={stop.crowd_index} compact />
                        </td>
                      </tr>
                      {onAvoidStop && (editingStop === stop && stopEditor ? true : !expanded && aboveTolerance && (!dismissedStops.has(stop) || selectedStop === stop)) && (
                        <tr className="stop-action-row">
                          <td colSpan={4}>
                            <div className="stop-action" key={expanded ? 'action' : 'prompt'}>
                              {editingStop === stop && stopEditor ? stopEditor : (
                                <>
                                  <p>{t('{stop} is above your {tolerance}% tolerance ({crowd}%). Would you like to avoid this stop?', { stop: stop.name, tolerance: crowdTolerance, crowd: stop.crowd_index })}</p>
                                  <button type="button" disabled={editing} onClick={() => {
                                    setConfirmedStop(stop)
                                    onSelectStop?.(stop)
                                    onAvoidStop(stop)
                                  }} aria-label={t('Yes, avoid {stop}', { stop: stop.name })}>{t('Yes, show avoidance options')}</button>
                                  <button type="button" onClick={() => {
                                    setDismissedStops((prev) => new Set([...prev, stop]))
                                    if (selectedStop === stop) onSelectStop?.(null)
                                  }} aria-label={t('Keep {stop}', { stop: stop.name })}>{t('Keep this stop')}</button>
                                </>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                      </Fragment>
                    )
                  })}
                </tbody>
              </table>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
