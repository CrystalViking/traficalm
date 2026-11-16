import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { planJourney, prepareCity, tripRealtime } from './api'
import { AddressInput } from './components/AddressInput'
import { ItineraryDetails } from './components/ItineraryDetails'
import { RouteMap } from './components/RouteMap'
import { RouteOptions } from './components/RouteOptions'
import { StopAlternatives } from './components/StopAlternatives'
import { SettingsMenu } from './components/SettingsMenu'
import type { AppSettings } from './settings'
import { DEFAULT_CROWD_TOLERANCE, readSettings, SETTINGS_KEY, SettingsContext, useSettings } from './settings'
import { useGeolocation } from './hooks/useGeolocation'
import type { CityStatus, Itinerary, NearbyStop, Place, PlanResponse, StopCall, TripRealtime, Vehicle } from './types'
import type { City } from './cities'
import { CITIES } from './cities'
import { toLocalInputValue } from './utils/format'
import { sortByCrowd } from './utils/sort'
import { realtimeKey } from './utils/realtime'

const REALTIME_REFRESH_MS = 30_000
const TOLERANCE_KEY = 'traficalm-crowd-tolerance'
const LAYA_KEY = 'traficalm-use-laya'

function readLayaPreference(): { value: boolean; error: string | null } {
  try {
    const saved = localStorage.getItem(LAYA_KEY)
    if (saved === null) return { value: false, error: null }
    if (saved === 'true' || saved === 'false') return { value: saved === 'true', error: null }
    return { value: false, error: 'Saved Laya preference is invalid; using the default (off).' }
  } catch (err) {
    return { value: false, error: `Could not read Laya preference: ${err instanceof Error ? err.message : String(err)}` }
  }
}

function readTolerance(): { value: number; error: string | null } {
  try {
    const saved = localStorage.getItem(TOLERANCE_KEY)
    if (saved === null) return { value: DEFAULT_CROWD_TOLERANCE, error: null }
    const value = Number(saved)
    if (saved.trim() && Number.isInteger(value) && value >= 0 && value <= 100) return { value, error: null }
    return { value: DEFAULT_CROWD_TOLERANCE, error: `Saved crowd tolerance is invalid; using ${DEFAULT_CROWD_TOLERANCE}%.` }
  } catch (err) {
    return { value: DEFAULT_CROWD_TOLERANCE, error: `Could not read crowd tolerance: ${err instanceof Error ? err.message : String(err)}` }
  }
}

export default function App() {
  const [settings, setSettings] = useState(readSettings)
  const [cityState, setCityState] = useState<{ city: City | null; data: CityStatus | null; error: string | null }>({
    city: null, data: null, error: null,
  })
  const [cityRetry, setCityRetry] = useState(0)
  useEffect(() => {
    const city = settings.value.city
    const controller = new AbortController()
    prepareCity(city, controller.signal).then(data => {
      if (controller.signal.aborted) return
      if (data.city !== city || !data.ready) throw new Error('The city timetable is not ready')
      setCityState({ city, data, error: null })
    }).catch((err: unknown) => {
      if (!controller.signal.aborted) setCityState({ city, data: null, error: err instanceof Error ? err.message : String(err) })
    })
    return () => controller.abort()
  }, [settings.value.city, cityRetry])
  useEffect(() => {
    document.documentElement.lang = settings.value.language
    document.documentElement.dataset.theme = settings.value.darkMode ? 'dark' : 'light'
  }, [settings.value.language, settings.value.darkMode])
  const saveSettings = (value: AppSettings) => {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(value))
      setSettings({ value, error: null })
    } catch (err) {
      setSettings({ value, error: `Could not save settings: ${err instanceof Error ? err.message : String(err)}` })
    }
  }
  return (
    <SettingsContext.Provider value={settings.value}>
      <JourneyPlanner settings={settings.value} onSettingsChange={saveSettings} appSettingsError={settings.error}
        cityData={cityState.city === settings.value.city ? cityState.data : null}
        cityError={cityState.city === settings.value.city ? cityState.error : null}
        onRetryCity={() => {
          setCityState({ city: null, data: null, error: null })
          setCityRetry(value => value + 1)
        }} />
    </SettingsContext.Provider>
  )
}

function JourneyPlanner({ settings, onSettingsChange, appSettingsError, cityData, cityError, onRetryCity }: {
  settings: AppSettings; onSettingsChange: (settings: AppSettings) => void; appSettingsError: string | null
  cityData: CityStatus | null; cityError: string | null; onRetryCity: () => void
}) {
  const { t, external } = useSettings()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [sheetCollapsed, setSheetCollapsed] = useState(false)
  const [origin, setOrigin] = useState<Place | null>(null)
  const [destination, setDestination] = useState<Place | null>(null)
  const [leaveAt, setLeaveAt] = useState(() => toLocalInputValue(new Date()))
  const [plan, setPlan] = useState<PlanResponse | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const detailsRef = useRef<HTMLDivElement>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [liveSnapshot, setLiveSnapshot] = useState<{ key: string; data: Record<string, TripRealtime>; errors: string[] }>({ key: '', data: {}, errors: [] })
  const [tolerance, setTolerance] = useState(readTolerance)
  const crowdTolerance = tolerance.value
  const settingsError = tolerance.error
  const [avoidingStop, setAvoidingStop] = useState<StopCall | null>(null)
  const [alternativeStops, setAlternativeStops] = useState<NearbyStop[]>([])
  const [hoveredAlternativeStop, setHoveredAlternativeStop] = useState<string | null>(null)
  const [alternativePreview, setAlternativePreview] = useState<Itinerary | null>(null)
  const [focusPoints, setFocusPoints] = useState<[number, number][] | null>(null)
  const [slowPlanning, setSlowPlanning] = useState(false)
  const [selectedStop, setSelectedStop] = useState<StopCall | null>(null)
  const [layaPreference, setLayaPreference] = useState(readLayaPreference)
  const planRequest = useRef<AbortController | null>(null)
  const { position, error: gpsError } = useGeolocation(settings.gps)
  const cityReady = cityData?.ready === true
  const cityLoading = !cityReady && cityError === null

  const options = useMemo(() => sortByCrowd(plan?.itineraries ?? []), [plan])
  const selected = options.find((it) => it.id === selectedId) ?? null
  const previewed = options.find((it) => it.id === hoveredId) ?? selected
  const liveKey = selected ? JSON.stringify([settings.city, selected.id, selected.legs.map(leg => [leg.trip_id, leg.service_date])]) : ''
  const realtime = liveSnapshot.key === liveKey ? liveSnapshot.data : {}

  useEffect(() => {
    if (!loading) return
    const timer = setTimeout(() => setSlowPlanning(true), 1000)
    return () => {
      clearTimeout(timer)
      setSlowPlanning(false)
    }
  }, [loading])

  useEffect(() => {
    return () => {
      planRequest.current?.abort()
    }
  }, [])

  const dismissAlternatives = () => {
    setAvoidingStop(null)
    setAlternativeStops([])
    setHoveredAlternativeStop(null)
  }

  const changeTolerance = (value: number) => {
    dismissAlternatives()
    try {
      localStorage.setItem(TOLERANCE_KEY, String(value))
      setTolerance({ value, error: null })
    } catch (err) {
      setTolerance({ value, error: `Crowd tolerance applies for this session but could not be saved: ${err instanceof Error ? err.message : String(err)}` })
    }
  }

  const changeLaya = (value: boolean) => {
    dismissAlternatives()
    try {
      localStorage.setItem(LAYA_KEY, String(value))
      setLayaPreference({ value, error: null })
    } catch (err) {
      setLayaPreference({ value, error: `Laya preference applies for this session but could not be saved: ${err instanceof Error ? err.message : String(err)}` })
    }
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!cityReady) return
    if (!origin || !destination) {
      setError('Choose both a starting point (A) and a destination (B) from the suggestions or use GPS.')
      return
    }
    planRequest.current?.abort()
    dismissAlternatives()
    const controller = new AbortController()
    planRequest.current = controller
    setLoading(true)
    setError(null)
    try {
      const departure = leaveAt ? new Date(leaveAt).toISOString() : undefined
      const result = await planJourney(origin, destination, departure, controller.signal, crowdTolerance, [], layaPreference.value, settings.city, settings.strictWalking)
      if (controller.signal.aborted) return
      if (result.city !== settings.city) throw new Error('The returned journey belongs to a different city')
      setPlan(result)
      setLiveSnapshot({ key: '', data: {}, errors: [] })
      setSelectedStop(null)
      setSelectedId(sortByCrowd(result.itineraries)[0]?.id ?? null)
    } catch (err) {
      if (controller.signal.aborted) return
      setPlan(null)
      setError(`Could not plan the journey: ${(err as Error).message}`)
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }

  const avoidStop = (stop: StopCall) => {
    setAlternativeStops([])
    setHoveredAlternativeStop(null)
    setAvoidingStop(stop)
    setSelectedStop(stop)
  }

  const applyAlternative = (result: PlanResponse, id: string) => {
    setPlan(result)
    setSelectedId(id)
    setLiveSnapshot({ key: '', data: {}, errors: [] })
    setSelectedStop(null)
    dismissAlternatives()
  }

  const selectStop = (stop: StopCall | null) => {
    if (stop) setSheetCollapsed(false)
    if (stop !== avoidingStop) dismissAlternatives()
    setSelectedStop(stop)
  }

  const swap = () => {
    setOrigin(destination)
    setDestination(origin)
  }

  // Poll GTFS-RT delays and vehicle positions for the selected option.
  useEffect(() => {
    if (!selected) return
    const legs = selected.legs.filter((leg) => leg.mode === 'transit' && leg.trip_id)
    if (legs.length === 0) return
    const controller = new AbortController()
    let refreshing = false
    const refresh = async () => {
      if (refreshing) return
      refreshing = true
      try {
        const results = await Promise.allSettled(legs.map(leg => tripRealtime(leg.trip_id!, leg.service_date, controller.signal, settings.city)))
        if (controller.signal.aborted) return
        const data: Record<string, TripRealtime> = {}
        const errors: string[] = []
        results.forEach((result, i) => {
          if (result.status === 'fulfilled') data[realtimeKey(legs[i].trip_id!, legs[i].service_date)] = result.value
          else errors.push(`Live tracking unavailable: ${result.reason instanceof Error ? result.reason.message : String(result.reason)}`)
        })
        setLiveSnapshot({ key: liveKey, data, errors })
      } finally {
        refreshing = false
      }
    }
    refresh()
    const timer = setInterval(refresh, REALTIME_REFRESH_MS)
    return () => {
      controller.abort()
      clearInterval(timer)
    }
  }, [selected, liveKey, settings.city])

  const cityStatus = (
    <>
      {cityLoading && <p className="hint" role="status">{t('Preparing {city} timetable. The first download may take a few minutes.', { city: CITIES[settings.city].name })}</p>}
      {cityError && <div className="error" role="alert">
        <p>{t('City timetable unavailable: {error}', { error: external(cityError) })}</p>
        <button type="button" onClick={onRetryCity}>{t('Retry timetable download')}</button>
      </div>}
    </>
  )

  const vehicles: Vehicle[] = selected
    ? selected.legs.flatMap((leg) => {
        const v = leg.trip_id ? realtime[realtimeKey(leg.trip_id, leg.service_date)]?.vehicle : null
        return v ? [v] : []
      })
    : []
  const liveVehicles = [...new Map(vehicles.map(vehicle => [vehicle.vehicle_id ?? vehicle.trip_id, vehicle])).values()]
  const trackingErrors = liveSnapshot.key === liveKey ? [
    ...liveSnapshot.errors, ...Object.values(realtime).flatMap(trip => trip.tracking_message ? [trip.tracking_message] : []),
  ] : []

  return (
    <div className="app">
      <header className="app-header">
        <button type="button" className="settings-button" onClick={() => setSettingsOpen(true)} aria-haspopup="dialog"
          aria-label={t('Settings')} title={t('Settings')}><span aria-hidden="true">⚙</span></button>
        <h1>🌿 TrafiCalm</h1>
        <p>{t('Calm public-transport routes in {city} – options sorted from the least crowded.', { city: CITIES[settings.city].name })}</p>
      </header>
      <main className="layout">
        <aside className={`sidebar${sheetCollapsed ? ' collapsed' : ''}`}>
          <button type="button" className="sheet-toggle" aria-expanded={!sheetCollapsed}
            aria-label={t(sheetCollapsed ? 'Show journey planner' : 'Hide journey planner')}
            onClick={() => setSheetCollapsed(value => !value)}>{t('Journey planner')} {sheetCollapsed ? '▴' : '▾'}</button>
          <div className="sidebar-content">
          <form className="plan-form" onSubmit={submit}>
            <AddressInput key={`${settings.city}-origin`} label={t('From (A)')} placeholder={t('Start typing an address…')} value={origin} onChange={setOrigin} />
            <button type="button" className="swap" onClick={swap} title={t('Swap A and B')} aria-label={t('Swap A and B')}>
              ⇅
            </button>
            <AddressInput key={`${settings.city}-destination`} label={t('To (B)')} placeholder={t('Start typing an address…')} value={destination} onChange={setDestination} />
            <label htmlFor="leave-at">{t('Leave at')}</label>
            <input id="leave-at" type="datetime-local" value={leaveAt} onChange={(e) => setLeaveAt(e.target.value)} />
            {loading && layaPreference.value && (
              <p className="hint" role="status">{t('Laya CPU scoring can take tens of seconds while comparing rides.')}</p>
            )}
            <button type="submit" className="primary" disabled={loading || !cityReady}>
              {loading && slowPlanning && <span className="spinner" aria-hidden="true" />}
              {loading ? t('Planning…') : t('Find calm routes')}
            </button>
          </form>
          {cityStatus}
          {error && (
            <p className="error" role="alert">
              {external(error)}
            </p>
          )}
          {gpsError && <p className="hint">GPS: {external(gpsError)}</p>}
          {settingsError && <p className="error" role="alert">{external(settingsError)}</p>}
          {layaPreference.error && <p className="error" role="alert">{external(layaPreference.error)}</p>}
          {appSettingsError && <p className="error" role="alert">{external(appSettingsError)}</p>}
          {plan && (
            <>
              <h2 className="section-title">
                {t(options.length === 1 ? '{count} option · least crowded first' : '{count} options · least crowded first', { count: options.length })}
                {plan.laya_enabled && <span className="ai-tag"> · Laya AI</span>}
              </h2>
              {plan.laya_message && <p className="crowd-warning" role="status">{external(plan.laya_message)}</p>}
              <RouteOptions key={options.map(it => it.id).join('|')} itineraries={options} selectedId={selectedId} limit={3}
                onHover={setHoveredId} onSelect={(id) => {
                dismissAlternatives()
                setSelectedId(id)
                setSelectedStop(null)
                requestAnimationFrame(() => detailsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
              }} />
            </>
          )}
          <div ref={detailsRef}>
          {selected && <ItineraryDetails key={selected.id} itinerary={selected} realtime={realtime} crowdTolerance={crowdTolerance}
            onAvoidStop={avoidStop} editing={loading} selectedStop={selectedStop} onSelectStop={selectStop}
            editingStop={avoidingStop} stopEditor={plan && avoidingStop ? (
              <StopAlternatives key={`${selected.id}-${selected.legs.findIndex(leg => leg.stops.includes(avoidingStop))}-${avoidingStop.stop_sequence}`}
                plan={plan} itinerary={selected} stop={avoidingStop} crowdTolerance={crowdTolerance} useLaya={layaPreference.value}
                onApply={applyAlternative} onClose={dismissAlternatives}
                onStopsChange={setAlternativeStops} onHoverStop={setHoveredAlternativeStop}
                onPreview={setAlternativePreview} onFocus={setFocusPoints} />
            ) : null} />}
          </div>
          </div>
        </aside>
        <section className="map-panel" aria-label={t('Map')}>
          <RouteMap key={settings.city} origin={plan?.origin ?? origin} destination={plan?.destination ?? destination} itinerary={avoidingStop ? (alternativePreview ?? selected) : previewed}
            focusPoints={avoidingStop && !alternativePreview ? focusPoints : null}
            userPosition={position} vehicles={liveVehicles} at={plan?.departure ?? leaveAt} crowdTolerance={crowdTolerance}
            ready={cityReady}
            alternativeStops={avoidingStop ? alternativeStops : []}
            hoveredAlternativeStop={avoidingStop ? hoveredAlternativeStop : null}
            trackingLoading={liveSnapshot.key !== liveKey} trackingErrors={[...new Set(trackingErrors)]}
            onSelectStop={selectStop} />
        </section>
      </main>
      <footer className="app-footer">
        {t('Timetables & real-time data:')} {cityData
          ? <a href={cityData.source_url}>{cityData.agency} ({cityData.license})</a> : CITIES[settings.city].name}
        {cityData?.imported_at && <> · {t('Processed timetable · imported {date}', {
          date: new Date(cityData.imported_at).toLocaleString(settings.language === 'pl' ? 'pl-PL' : 'en-GB'),
        })}</>}
        {cityData?.attributions.map((attribution, index) => <span key={index}>
          {' · '}{attribution.attribution_url && /^https?:\/\//i.test(attribution.attribution_url)
            ? <a href={attribution.attribution_url}>{attribution.organization_name}</a>
            : attribution.organization_name}
        </span>)}
        {' · '}{t('Map:')} © OpenStreetMap {t('contributors')}
      </footer>
      {settingsOpen && <SettingsMenu settings={settings} crowdTolerance={crowdTolerance} useLaya={layaPreference.value}
        onToleranceChange={changeTolerance} onLayaChange={changeLaya} planning={loading}
        errors={[settingsError, layaPreference.error, appSettingsError].filter((error): error is string => error !== null)}
        cityStatus={cityStatus}
        onClose={() => setSettingsOpen(false)} onChange={next => {
        if (next.city !== settings.city) {
          planRequest.current?.abort()
          setOrigin(null)
          setDestination(null)
          setPlan(null)
          setSelectedId(null)
          setSelectedStop(null)
          setLiveSnapshot({ key: '', data: {}, errors: [] })
          setLoading(false)
          setError(null)
          dismissAlternatives()
        }
        if (next.walkRadiusM !== settings.walkRadiusM) dismissAlternatives()
        onSettingsChange(next)
      }} />}
    </div>
  )
}
