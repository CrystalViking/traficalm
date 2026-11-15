import { useEffect, useMemo, useRef, useState } from 'react'
import { Circle, CircleMarker, MapContainer, Pane, Polygon, Polyline, TileLayer, Tooltip, ZoomControl, useMap, useMapEvents } from 'react-leaflet'
import { DomEvent } from 'leaflet'
import type { LatLngExpression, LatLngTuple } from 'leaflet'
import type { GeoPosition } from '../hooks/useGeolocation'
import type { CrowdForecastResponse, Itinerary, NearbyStop, Place, StopCall, Vehicle, VehiclePrediction } from '../types'
import { crowdForecast, vehicleForecast } from '../api'
import { crowdColor, formatTime } from '../utils/format'
import { useSettings } from '../settings'
import { CITIES } from '../cities'

interface Props {
  origin: Place | null
  destination: Place | null
  itinerary: Itinerary | null
  userPosition: GeoPosition | null
  vehicles: Vehicle[]
  at: string
  crowdTolerance: number
  onSelectStop: (stop: StopCall) => void
  trackingLoading: boolean
  trackingErrors: string[]
  ready?: boolean
  alternativeStops?: NearbyStop[]
  hoveredAlternativeStop?: string | null
}

function useMapControl() {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (ref.current) {
      DomEvent.disableClickPropagation(ref.current)
      DomEvent.disableScrollPropagation(ref.current)
    }
  }, [])
  return ref
}

function flowArrow(a: Place, b: Place): LatLngTuple[] {
  const scale = Math.cos((a.lat + b.lat) / 2 * Math.PI / 180)
  const x = (b.lon - a.lon) * scale, y = b.lat - a.lat
  const length = Math.hypot(x, y)
  if (!length) return [[a.lat, a.lon], [b.lat, b.lon]]
  const ux = x / length, uy = y / length, size = Math.min(0.0006, length / 4)
  return [
    [a.lat, a.lon], [b.lat, b.lon],
    [b.lat - size * uy + size * ux / 2, b.lon - (size * ux + size * uy / 2) / scale],
    [b.lat, b.lon],
    [b.lat - size * uy - size * ux / 2, b.lon - (size * ux - size * uy / 2) / scale],
  ]
}

function CrowdOverlay({ at, itinerary }: { at: string; itinerary: Itinerary | null }) {
  const { t, external, city } = useSettings()
  const map = useMap()
  const [prediction, setPrediction] = useState<{ at: string; routeKey: string; data: CrowdForecastResponse } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [hovered, setHovered] = useState(false)
  const [focused, setFocused] = useState(false)
  const [pinned, setPinned] = useState(false)
  const control = useMapControl()
  const [revision, setRevision] = useState(0)
  const routeGeometry = useMemo(() => itinerary?.legs.flatMap((leg) => leg.geometry) ?? [], [itinerary])
  const routeKey = JSON.stringify(routeGeometry)
  const validTime = Number.isFinite(new Date(at).getTime())
  useMapEvents({ moveend: () => setRevision((n) => n + 1) })
  useEffect(() => {
    const controller = new AbortController()
    const load = () => {
      const bounds = map.getBounds()
      const date = new Date(at)
      if (!Number.isFinite(date.getTime())) {
        return
      }
      crowdForecast(date.toISOString(), {
        south: bounds.getSouth(), west: bounds.getWest(), north: bounds.getNorth(), east: bounds.getEast(),
      }, routeGeometry, controller.signal, city).then((result) => {
        if (controller.signal.aborted) return
        setPrediction({ at, routeKey, data: result })
        setError(null)
      }).catch((err: unknown) => {
        if (controller.signal.aborted) return
        setPrediction(null)
        setError(`Crowd areas unavailable: ${err instanceof Error ? err.message : String(err)}`)
      })
    }
    load()
    const timer = setInterval(load, 60_000)
    return () => { controller.abort(); clearInterval(timer) }
  }, [map, at, revision, routeGeometry, routeKey, city])
  const visible = prediction?.at === at && prediction.routeKey === routeKey ? prediction.data : null
  const open = hovered || focused || pinned
  return (
    <>
      <Pane name="crowd-areas" style={{ zIndex: 300 }}>
      {visible?.areas.map((area) => (
        <Polygon key={area.id} positions={area.geometry}
          pathOptions={{ color: crowdColor(area.crowd_index), weight: 1, fillOpacity: area.crowd_index >= 50 ? 0.25 : 0.12 }}>
          <Tooltip sticky>
            {t('{area} · estimated crowd {value}%', { area: external(area.name), value: area.crowd_index })}<br />
            {area.reasons.map(external).join(' · ') || t('Typical time-of-day demand')}
          </Tooltip>
        </Polygon>
      ))}
      </Pane>
      <Pane name="crowd-movement" style={{ zIndex: 350 }}>
        {visible?.flows.map((flow, i) => (
          <Polyline key={i} positions={flowArrow(flow.origin, flow.destination)}
            pathOptions={{ color: 'var(--ai)', weight: 2, opacity: 0.8 }}>
            <Tooltip sticky>{t('Estimated demand shift: {from} → {to}', { from: flow.origin.name, to: flow.destination.name })}</Tooltip>
          </Polyline>
        ))}
      </Pane>
      <div ref={control} className="crowd-map-legend" onMouseEnter={() => setHovered(true)} onMouseLeave={() => {
        setHovered(false)
        setFocused(false)
        setPinned(false)
      }}
        onFocus={() => setFocused(true)} onBlur={event => {
          if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setFocused(false)
        }}>
        <button type="button" className="legend-toggle" aria-label={t('Crowd map legend')} title={t('Crowd map legend')}
          aria-expanded={open} aria-controls="crowd-legend-details" onClick={() => setPinned(value => !value)}>
          {open ? t('Estimated area crowd') : <svg aria-hidden="true" width="22" height="22" viewBox="0 0 24 24" fill="none">
            <circle cx="4" cy="5" r="2" fill={crowdColor(10)} />
            <circle cx="4" cy="12" r="2" fill={crowdColor(35)} />
            <circle cx="4" cy="19" r="2" fill={crowdColor(90)} />
            <path d="M9 5h12M9 12h12M9 19h12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>}
        </button>
        {open && <div id="crowd-legend-details" className="legend-details">
        <span><i style={{ background: crowdColor(10) }} /> {t('Low (0–24%)')}</span>
        <span><i style={{ background: crowdColor(35) }} /> {t('Moderate (25–49%)')}</span>
        <span><i style={{ background: crowdColor(65) }} /> {t('High (50–74%)')}</span>
        <span><i style={{ background: crowdColor(90) }} /> {t('Very high (75–100%)')}</span>
        <small>{t('High areas always shown; other areas within 500 m of your route.')}</small>
        {visible && <p>{t('Forecast: {from}–{to}', { from: formatTime(visible.at), to: formatTime(visible.until) })}</p>}
        <small>{t('Purple arrows infer demand shifts over 60 minutes, not measured pedestrian paths or live crowd counts.')}</small>
        {visible?.changes.slice(0, 5).map(change => (
          <small key={`${change.lat}-${change.lon}`}>{change.name}: {change.current_index}% → {change.predicted_index}%</small>
        ))}
        {visible && !visible.flows.length && <small>{t('No matched movement direction is predicted in this window.')}</small>}
        </div>}
        {error && <p className="error" role="alert">{external(error)}</p>}
        {!validTime && <p className="error" role="alert">{t('Choose a valid departure time to show crowd predictions.')}</p>}
      </div>
    </>
  )
}

function VehiclePredictions({ at, itinerary }: { at: string; itinerary: Itinerary }) {
  const { t, external, city } = useSettings()
  const [snapshot, setSnapshot] = useState<{ key: string; positions: VehiclePrediction[]; error: string | null } | null>(null)
  const key = JSON.stringify([at, itinerary.id, itinerary.legs.map(leg => leg.service_date)])
  useEffect(() => {
    const controller = new AbortController()
    const load = async () => {
      if (!Number.isFinite(new Date(at).getTime())) return
      try {
        const result = await vehicleForecast(new Date(at).toISOString(), itinerary, controller.signal, city)
        if (!controller.signal.aborted) setSnapshot({ key, positions: result.predictions, error: null })
      } catch (err) {
        if (!controller.signal.aborted) setSnapshot({
          key, positions: [], error: `Vehicle forecast unavailable: ${err instanceof Error ? err.message : String(err)}`,
        })
      }
    }
    void load()
    const timer = setInterval(load, 60_000)
    return () => { controller.abort(); clearInterval(timer) }
  }, [at, itinerary, key, city])
  const current = snapshot?.key === key ? snapshot : null
  return (
    <>
      <Pane name="predicted-vehicles" style={{ zIndex: 620 }}>
        {current?.positions.map(position => (
          <CircleMarker key={`${position.trip_id}-${position.at}`} center={[position.lat, position.lon]} radius={5}
            pathOptions={{ color: 'var(--ai)', weight: 2, fillOpacity: 0.15, dashArray: '3 3' }}>
            <Tooltip>{t('Predicted {line} at {time} (not live GPS)', { line: position.name, time: formatTime(position.at) })}</Tooltip>
          </CircleMarker>
        ))}
      </Pane>
      {current?.error && <p className="vehicle-forecast-error error" role="alert">{external(current.error)}</p>}
    </>
  )
}

function TimeSlider({ at, value, onChange }: { at: string; value: number; onChange: (value: number) => void }) {
  const { t } = useSettings()
  const control = useMapControl()
  return (
    <div ref={control} className="forecast-time-slider">
      <label htmlFor="map-forecast-time">{t('Forecast time: {time} · next 60 minutes', { time: formatTime(at) })}</label>
      <input id="map-forecast-time" type="range" min="0" max="1440" step="15" value={value} onChange={event => onChange(Number(event.target.value))} />
      <div><span>{t('Departure / now')}</span><span>+24 h</span></div>
    </div>
  )
}

function FitBounds({ points }: { points: LatLngTuple[] }) {
  const map = useMap()
  const key = JSON.stringify(points)
  useEffect(() => {
    const pts = JSON.parse(key) as LatLngTuple[]
    const fit = () => {
      if (pts.length === 1) map.setView(pts[0], 15)
      else if (pts.length > 1) map.fitBounds(pts, {
        paddingTopLeft: [30, 30], paddingBottomRight: [30, map.getSize().x <= 800 ? map.getSize().y * 0.48 + 30 : 30],
      })
    }
    fit()
    map.on('resize', fit)
    return () => { map.off('resize', fit) }
  }, [map, key])
  return null
}

function LiveTrackingLegend({ count, loading, errors }: { count: number; loading: boolean; errors: string[] }) {
  const { t, external } = useSettings()
  const [hovered, setHovered] = useState(false)
  const [focused, setFocused] = useState(false)
  const control = useMapControl()
  const open = hovered || focused
  return (
    <div ref={control} className="live-tracking-control" onMouseEnter={() => setHovered(true)} onMouseLeave={() => {
      setHovered(false)
      setFocused(false)
    }} onFocus={() => setFocused(true)} onBlur={event => {
      if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setFocused(false)
    }} onKeyDown={event => {
      if (event.key === 'Escape') {
        setHovered(false)
        setFocused(false)
      }
    }}>
      <button type="button" className="live-tracking-toggle" aria-label={t('Live bus tracking')}
        aria-expanded={open} aria-controls="live-tracking-details"
        aria-describedby={errors.length ? 'live-tracking-messages' : undefined}>
        <svg aria-hidden="true" width="22" height="22" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <rect x="4" y="3" width="16" height="15" rx="3" />
          <path d="M4 12h16M12 3v9M7 18v2M17 18v2" />
          <circle cx="7.5" cy="15.2" r="0.8" fill="currentColor" stroke="none" />
          <circle cx="16.5" cy="15.2" r="0.8" fill="currentColor" stroke="none" />
        </svg>
        {errors.length > 0 && <span className="live-tracking-indicator" aria-hidden="true">!</span>}
      </button>
      <div id="live-tracking-details" className={open ? 'live-tracking-status' : undefined}>
        {open && <>
          <strong>{t('Live buses: {count}', { count })}</strong>
          <small>{loading ? t('Loading selected-trip GPS positions…') : t('Only selected trips. GPS refreshes every 30 seconds; dotted rings are forecasts, not live buses.')}</small>
        </>}
        <div id="live-tracking-messages" role="status" className={open ? undefined : 'visually-hidden'}>
          {errors.map(error => <small key={error}>{external(error)}</small>)}
        </div>
      </div>
    </div>
  )
}

export function RouteMap({ origin, destination, itinerary, userPosition, vehicles, at, crowdTolerance, onSelectStop, trackingLoading, trackingErrors, ready = true, alternativeStops = [], hoveredAlternativeStop = null }: Props) {
  const { t, language, timeSlider, city } = useSettings()
  const [clock, setClock] = useState(Date.now)
  const [offset, setOffset] = useState({ key: '', minutes: 0 })
  const basis = itinerary ? `${itinerary.id}:${at}` : 'now'
  const minutes = timeSlider && offset.key === basis ? offset.minutes : 0
  const base = itinerary ? new Date(at).getTime() : clock
  const forecastAt = Number.isFinite(base) ? new Date(base + minutes * 60_000).toISOString() : at
  useEffect(() => {
    const timer = setInterval(() => setClock(Date.now()), 60_000)
    return () => clearInterval(timer)
  }, [])
  const boundsPoints: LatLngTuple[] = []
  if (itinerary) {
    for (const leg of itinerary.legs) for (const [lat, lon] of leg.geometry) boundsPoints.push([lat, lon])
  }
  if (origin) boundsPoints.push([origin.lat, origin.lon])
  if (destination) boundsPoints.push([destination.lat, destination.lon])
  for (const stop of alternativeStops) boundsPoints.push([stop.lat, stop.lon])

  return (
    <MapContainer key={city} center={CITIES[city].centre} zoom={13} className="route-map" scrollWheelZoom zoomControl={false}>
      <ZoomControl key={language} zoomInTitle={t('Zoom in')} zoomOutTitle={t('Zoom out')} />
      <TileLayer
        attribution={`&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> ${t('contributors')}`}
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <FitBounds points={boundsPoints} />
      {ready && <CrowdOverlay at={forecastAt} itinerary={itinerary} />}
      {ready && itinerary?.legs.some(leg => leg.mode === 'transit') && <VehiclePredictions at={forecastAt} itinerary={itinerary} />}
      {timeSlider && <TimeSlider at={forecastAt} value={minutes} onChange={value => setOffset({ key: basis, minutes: value })} />}
      {itinerary?.legs.some(leg => leg.mode === 'transit') && (
        <LiveTrackingLegend count={vehicles.length} loading={trackingLoading} errors={trackingErrors} />
      )}

      <Pane name="route-lines" style={{ zIndex: 400 }}>
      {itinerary?.legs.map((leg, i) => {
        const positions: LatLngExpression[] = leg.geometry.map(([lat, lon]) => [lat, lon])
        if (leg.mode === 'walk') {
          return (
            <Polyline key={`leg-${i}`} positions={positions} pathOptions={{ color: '#1565c0', weight: 4, dashArray: '6 8' }}>
              <Tooltip sticky>🚶 {t('Walk to {stop}', { stop: leg.end.name })}</Tooltip>
            </Polyline>
          )
        }
        return (
          <Polyline key={`leg-${i}`} positions={positions} pathOptions={{ color: crowdColor(leg.crowd_index), weight: 6, opacity: 0.85 }}>
            <Tooltip sticky>
              {t('{line} → {headsign} · crowd {value}%', { line: leg.route?.short_name ?? '', headsign: leg.headsign ?? '', value: leg.crowd_index })}
            </Tooltip>
          </Polyline>
        )
      })}
      </Pane>

      <Pane name="route-stops" style={{ zIndex: 600 }}>
      {itinerary?.legs
        .filter((leg) => leg.mode === 'transit')
        .flatMap((leg) =>
          leg.stops.map((stop, j) => (
            <CircleMarker
              key={`${leg.trip_id}-${stop.stop_sequence}`}
              center={[stop.lat, stop.lon]}
              radius={stop.crowd_index > crowdTolerance ? 7 : j === 0 || j === leg.stops.length - 1 ? 6 : 4}
              pathOptions={{ color: stop.crowd_index > crowdTolerance ? '#b71c1c' : '#263238', weight: 2, fillColor: crowdColor(stop.crowd_index), fillOpacity: 1 }}
              eventHandlers={{ click: () => onSelectStop(stop) }}
            >
              <Tooltip>
                {t('{stop} · crowd {value}%', { stop: stop.name, value: stop.crowd_index })}
              </Tooltip>
            </CircleMarker>
          )),
        )}
      </Pane>

      <Pane name="alternative-stops" style={{ zIndex: 610 }}>
        {alternativeStops.map(stop => (
          <CircleMarker key={stop.stop_id} center={[stop.lat, stop.lon]}
            radius={hoveredAlternativeStop === stop.stop_id ? 11 : 6}
            pathOptions={{ color: 'var(--brand)', weight: 3, fillColor: crowdColor(stop.crowd_index), fillOpacity: 1 }}>
            <Tooltip>{t('{stop} · crowd {value}%', { stop: stop.name, value: stop.crowd_index })}</Tooltip>
          </CircleMarker>
        ))}
      </Pane>

      <Pane name="live-vehicles" style={{ zIndex: 630 }}>
      {vehicles.map((v) => (
        <CircleMarker
          key={`veh-${v.vehicle_id ?? v.trip_id}`}
          center={[v.lat, v.lon]}
          radius={7}
          pathOptions={{ color: '#000', weight: 2, fillColor: '#ffeb3b', fillOpacity: 1 }}
        >
          <Tooltip>{t('Vehicle {label} (live)', { label: v.label ?? v.vehicle_id ?? v.trip_id ?? '', })}</Tooltip>
        </CircleMarker>
      ))}
      </Pane>

      {origin && (
        <CircleMarker center={[origin.lat, origin.lon]} radius={9} pathOptions={{ color: '#fff', weight: 3, fillColor: '#2e7d32', fillOpacity: 1 }}>
          <Tooltip permanent direction="top">A</Tooltip>
        </CircleMarker>
      )}
      {destination && (
        <CircleMarker
          center={[destination.lat, destination.lon]}
          radius={9}
          pathOptions={{ color: '#fff', weight: 3, fillColor: '#c62828', fillOpacity: 1 }}
        >
          <Tooltip permanent direction="top">B</Tooltip>
        </CircleMarker>
      )}

      {userPosition && (
        <>
          <Circle
            center={[userPosition.lat, userPosition.lon]}
            radius={userPosition.accuracy}
            pathOptions={{ color: '#1e88e5', weight: 1, fillOpacity: 0.12 }}
          />
          <CircleMarker
            center={[userPosition.lat, userPosition.lon]}
            radius={8}
            pathOptions={{ color: '#fff', weight: 3, fillColor: '#1e88e5', fillOpacity: 1 }}
          >
            <Tooltip>{t('You are here')}</Tooltip>
          </CircleMarker>
        </>
      )}
    </MapContainer>
  )
}
