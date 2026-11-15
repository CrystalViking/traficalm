import { useEffect, useRef, useState } from 'react'
import { continueJourney, nearbyStops, planJourney } from '../api'
import type { Itinerary, NearbyStop, PlanResponse, StopCall } from '../types'
import { formatDistance } from '../utils/format'
import { CrowdBadge } from './CrowdBadge'
import { RouteOptions } from './RouteOptions'
import { useSettings } from '../settings'

interface Props {
  plan: PlanResponse
  itinerary: Itinerary
  stop: StopCall
  crowdTolerance: number
  useLaya: boolean
  onApply: (plan: PlanResponse, id: string) => void
  onClose: () => void
  onStopsChange: (stops: NearbyStop[]) => void
  onHoverStop: (stopId: string | null) => void
}

export function StopAlternatives({ plan, itinerary, stop, crowdTolerance, useLaya, onApply, onClose, onStopsChange, onHoverStop }: Props) {
  const { t, external, walkRadiusM, city } = useSettings()
  const [exit, setExit] = useState<StopCall | null>(null)
  const [stops, setStops] = useState<NearbyStop[] | null>(null)
  const [boarding, setBoarding] = useState<NearbyStop | null>(null)
  const [result, setResult] = useState<PlanResponse | null>(null)
  const [candidateId, setCandidateId] = useState<string | null>(null)
  const [busy, setBusy] = useState<'stops' | 'routes' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [unavailableStop, setUnavailableStop] = useState<string | null>(null)
  const [wholeJourney, setWholeJourney] = useState(false)
  const request = useRef<AbortController | null>(null)
  const panel = useRef<HTMLDivElement>(null)
  const selectedRoutes = useRef<HTMLElement>(null)
  const selectedLegIndex = itinerary.legs.findIndex(leg => leg.stops.includes(stop))
  const exits = itinerary.legs.slice(0, selectedLegIndex + 1).flatMap((leg, legIndex) =>
    leg.stops.slice(0, legIndex === selectedLegIndex ? leg.stops.indexOf(stop) + 1 : leg.stops.length)
      .map(choice => ({ stop: choice, key: `${legIndex}:${choice.stop_sequence}` })),
  ).slice(-4)
  const excluded = [...new Set([...plan.avoided_stop_ids, stop.stop_id])]

  useEffect(() => {
    panel.current?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' })
    return () => request.current?.abort()
  }, [])

  useEffect(() => {
    selectedRoutes.current?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' })
  }, [boarding, result, error])

  function startRequest() {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setResult(null)
    setCandidateId(null)
    setError(null)
    setUnavailableStop(null)
    return controller
  }

  async function chooseExit(key: string) {
    const controller = startRequest()
    setBoarding(null)
    setStops(null)
    setWholeJourney(false)
    onStopsChange([])
    onHoverStop(null)
    const chosen = exits.find(choice => choice.key === key)?.stop ?? null
    setExit(chosen)
    if (!chosen) {
      setBusy(null)
      return
    }
    setBusy('stops')
    try {
      const nearby = await nearbyStops(chosen, excluded, controller.signal, walkRadiusM, city)
      if (!controller.signal.aborted) {
        setStops(nearby)
        onStopsChange(nearby)
      }
    } catch (err) {
      if (!controller.signal.aborted) setError(`Could not load nearby stops: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      if (!controller.signal.aborted) setBusy(null)
    }
  }

  async function chooseBoarding(chosen: NearbyStop) {
    if (!exit) return
    const controller = startRequest()
    setBoarding(chosen)
    setBusy('routes')
    try {
      const alternatives = await continueJourney(plan, itinerary, exit, chosen, crowdTolerance, excluded, useLaya, controller.signal, walkRadiusM, city)
      if (!controller.signal.aborted) {
        if (alternatives.itineraries.length) {
          setResult(alternatives)
        } else {
          const remaining = (stops ?? []).filter(stop => stop.stop_id !== chosen.stop_id)
          setStops(remaining)
          onStopsChange(remaining)
          onHoverStop(null)
          setBoarding(null)
          setUnavailableStop(chosen.name)
        }
      }
    } catch (err) {
      if (!controller.signal.aborted) setError(`Could not replan from this stop: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      if (!controller.signal.aborted) setBusy(null)
    }
  }

  async function removeAndReplan() {
    const controller = startRequest()
    setExit(null)
    setStops(null)
    setBoarding(null)
    onStopsChange([])
    onHoverStop(null)
    setWholeJourney(true)
    setBusy('routes')
    try {
      const alternatives = await planJourney(
        plan.origin, plan.destination, plan.departure, controller.signal, crowdTolerance, excluded, useLaya, city,
      )
      if (!controller.signal.aborted) setResult(alternatives)
    } catch (err) {
      if (!controller.signal.aborted) setError(`Could not remove this stop and replan: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      if (!controller.signal.aborted) setBusy(null)
    }
  }

  const routes = (
    <>
      {busy === 'routes' && <p role="status">{t('Finding onward routes')}{useLaya ? t(' with Laya (CPU scoring may take a minute)') : ''}...</p>}
      {error && <p className="error" role="alert">{external(error)}</p>}
      {result && (
        <>
          {result.itineraries.length ? (
            <>
              <RouteOptions itineraries={result.itineraries} selectedId={candidateId} onSelect={setCandidateId} showWalkTransfers />
              <button type="button" className="primary" disabled={!candidateId} onClick={() => {
                if (candidateId) onApply(result, candidateId)
              }}>{t('Confirm new route')}</button>
            </>
          ) : <p role="status">{t('No onward route was found. Choose another stop or exit point; your current route is unchanged.')}</p>}
          {result.laya_message && <p className="crowd-warning" role="status">{external(result.laya_message)}</p>}
        </>
      )}
    </>
  )

  return (
    <div ref={panel} className="stop-editor" role="region" aria-label={t('Change route at {stop}', { stop: stop.name })}>
      <h3>{t('Change route at {stop}', { stop: stop.name })}</h3>
      <p>{t('Your selected route stays unchanged until confirmation. Hover over nearby stops to preview them on the map.')}</p>
      <button type="button" onClick={removeAndReplan}>{t('Remove stop and replan whole journey')}</button>
      <p>{t('Or keep the journey up to an exit stop, then walk to another stop and continue.')}</p>
      <label>
        {t('Where to get off')}
        <select aria-label={t('Where to get off')} value={exits.find(choice => choice.stop === exit)?.key ?? ''} onChange={event => void chooseExit(event.target.value)}>
          <option value="">{t('Choose an exit stop')}</option>
          {exits.map((choice, index) => (
            <option key={choice.key} value={choice.key}>
              {choice.stop === stop ? t('{stop} (selected stop)', { stop: choice.stop.name }) : t('{stop} (before {next})', { stop: choice.stop.name, next: exits[index + 1].stop.name })}
            </option>
          ))}
        </select>
      </label>
      {exit === stop && <p>{t('You will visit this stop to get off; the onward transport will not use it again.')}</p>}
      {busy === 'stops' && <p role="status">{t('Finding stops within {distance}...', { distance: formatDistance(walkRadiusM) })}</p>}
      {error && !boarding && !wholeJourney && <p className="error" role="alert">{external(error)}</p>}
      {unavailableStop && <p role="status">{t('No onward route from {stop}. This stop was removed; choose another stop.', { stop: unavailableStop })}</p>}
      {stops && (
        <>
          <p>{t('Stops within {distance} of {stop}, nearest first. Distances are straight-line; walking can be longer.', { distance: formatDistance(walkRadiusM), stop: exit?.name ?? '' })}</p>
          {stops.length === 0 && <p role="status">{unavailableStop
            ? t('No usable nearby stops remain. Choose another exit stop or increase the walking distance cap.')
            : t('No other stops are within {distance}. Choose another exit stop.', { distance: formatDistance(walkRadiusM) })}</p>}
          <ol className="nearby-stops">
            {stops.map(nearby => (
              <li key={nearby.stop_id}>
                <button type="button" className="nearby-stop" aria-pressed={boarding?.stop_id === nearby.stop_id}
                  onMouseEnter={() => onHoverStop(nearby.stop_id)} onMouseLeave={() => onHoverStop(null)}
                  onFocus={() => onHoverStop(nearby.stop_id)} onBlur={() => onHoverStop(null)}
                  onClick={() => void chooseBoarding(nearby)}>
                  {nearby.name} · {formatDistance(nearby.distance_m)} <CrowdBadge value={nearby.crowd_index} compact />
                </button>
                {boarding?.stop_id === nearby.stop_id && (
                  <section ref={selectedRoutes} className="onward-routes" aria-label={t('Routes from {stop}', { stop: nearby.name })}>
                    <h4>{t('Suggested routes from {stop}', { stop: nearby.name })}</h4>
                    <p>{t('Options include the retained journey and walking connection. Compare walking distance and crowd levels.')}</p>
                    {routes}
                  </section>
                )}
              </li>
            ))}
          </ol>
        </>
      )}
      {wholeJourney && <section aria-label={t('Routes without this stop')}>{routes}</section>}
      <button type="button" onClick={onClose}>{t('Keep current route')}</button>
    </div>
  )
}
