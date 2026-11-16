import type { AddressSuggestion, CityStatus, CrowdAreasResponse, CrowdForecastResponse, Itinerary, MapBounds, NearbyStop, Place, PlanResponse, StopCall, TripRealtime, VehicleForecastResponse } from './types'
import type { City } from './cities'
import { DEFAULT_CROWD_TOLERANCE } from './settings'
import type { WalkingLogic } from './settings'

const BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') ?? ''

async function getJson<T>(path: string, params: Record<string, string | number | string[] | undefined>, signal?: AbortSignal): Promise<T> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) {
      for (const item of value) query.append(key, item)
    } else if (value !== undefined && value !== '') query.set(key, String(value))
  }
  return requestJson(`${BASE}${path}?${query.toString()}`, { signal })
}

async function requestJson<T>(url: string, init: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      /* not JSON */
    }
    throw new Error(`${response.status} ${detail}`)
  }
  return (await response.json()) as T
}

export function prepareCity(city: City, signal?: AbortSignal): Promise<CityStatus> {
  return requestJson(`${BASE}/api/cities/${city}/prepare`, { method: 'POST', signal })
}

export function searchAddress(q: string, signal?: AbortSignal, city: City = 'gdansk'): Promise<AddressSuggestion[]> {
  return getJson('/api/geocode/search', { q, city }, signal)
}

export function reverseGeocode(lat: number, lon: number, signal?: AbortSignal, city: City = 'gdansk'): Promise<AddressSuggestion | null> {
  return getJson('/api/geocode/reverse', { lat, lon, city }, signal)
}

export function planJourney(
  origin: Place, destination: Place, departure?: string, signal?: AbortSignal,
  crowdTolerance = DEFAULT_CROWD_TOLERANCE, avoidStopIds: string[] = [], useLaya = false, city: City = 'gdansk', walkingLogic: WalkingLogic = 'flexible',
): Promise<PlanResponse> {
  return getJson(
    '/api/plan',
    {
      city,
      from_lat: origin.lat,
      from_lon: origin.lon,
      to_lat: destination.lat,
      to_lon: destination.lon,
      from_name: origin.name,
      to_name: destination.name,
      departure,
      crowd_tolerance: crowdTolerance,
      avoid_stop_ids: avoidStopIds,
      use_laya: String(useLaya),
      strict_walking: String(walkingLogic === 'strict'),
      fastest: String(walkingLogic === 'fastest'),
    },
    signal,
  )
}

export function crowdAreas(at: string, bounds: MapBounds, signal?: AbortSignal, routeGeometry: [number, number][] = [], city: City = 'gdansk'): Promise<CrowdAreasResponse> {
  return requestJson(`${BASE}/api/crowd/areas?city=${city}`, {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ at, ...bounds, route_geometry: routeGeometry }),
  })
}

export function crowdForecast(at: string, bounds: MapBounds, routeGeometry: [number, number][], signal?: AbortSignal, city: City = 'gdansk'): Promise<CrowdForecastResponse> {
  return requestJson(`${BASE}/api/crowd/forecast?city=${city}`, {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ at, ...bounds, route_geometry: routeGeometry }),
  })
}

export function vehicleForecast(at: string, itinerary: Itinerary, signal?: AbortSignal, city: City = 'gdansk'): Promise<VehicleForecastResponse> {
  const rides = itinerary.legs.filter(leg => leg.mode === 'transit').map(leg => {
    if (!leg.trip_id || !leg.service_date || !leg.stops.length) throw new Error('The selected route is missing timetable information')
    return {
      trip_id: leg.trip_id, service_date: leg.service_date,
      board_sequence: leg.stops[0].stop_sequence,
      alight_sequence: leg.stops[leg.stops.length - 1].stop_sequence,
    }
  })
  return requestJson(`${BASE}/api/vehicles/forecast?city=${city}`, {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ at, rides }),
  })
}

export function nearbyStops(stop: StopCall, excludedStopIds: string[], signal?: AbortSignal, radiusM = 500, city: City = 'gdansk'): Promise<NearbyStop[]> {
  return getJson('/api/stops/nearby', {
    city, stop_id: stop.stop_id, at: stop.realtime_arrival ?? stop.scheduled_arrival,
    excluded_stop_ids: excludedStopIds,
    radius_m: radiusM,
  }, signal)
}

export function continueJourney(
  plan: PlanResponse, itinerary: Itinerary, exit: StopCall, boarding: NearbyStop,
  crowdTolerance: number, avoidStopIds: string[], useLaya: boolean, signal?: AbortSignal,
  radiusM = 500, city: City = 'gdansk', walkingLogic: WalkingLogic = 'flexible',
): Promise<PlanResponse> {
  const legIndex = itinerary.legs.findIndex(leg => leg.stops.includes(exit))
  if (legIndex < 0) throw new Error('The exit stop is not part of the selected route')
  const prefixRides = itinerary.legs.slice(0, legIndex + 1).filter(leg => leg.mode === 'transit').map(leg => {
    if (!leg.trip_id || !leg.service_date || !leg.stops.length) throw new Error('The selected route is missing timetable information')
    return {
      trip_id: leg.trip_id, service_date: leg.service_date,
      board_sequence: leg.stops[0].stop_sequence,
      alight_sequence: leg.stops.includes(exit) ? exit.stop_sequence : leg.stops[leg.stops.length - 1].stop_sequence,
    }
  })
  return requestJson(`${BASE}/api/plan/continue?city=${city}`, {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      city: plan.city, origin: plan.origin, destination: plan.destination, departure: plan.departure,
      prefix_rides: prefixRides, exit_stop_id: exit.stop_id, boarding_stop_id: boarding.stop_id,
      avoid_stop_ids: avoidStopIds, crowd_tolerance: crowdTolerance, use_laya: useLaya,
      walk_radius_m: radiusM, strict_walking: walkingLogic === 'strict', fastest: walkingLogic === 'fastest',
    }),
  })
}

export function tripRealtime(tripId: string, serviceDate: string | null, signal?: AbortSignal, city: City = 'gdansk'): Promise<TripRealtime> {
  return getJson(`/api/trips/${encodeURIComponent(tripId)}/realtime`, { city, service_date: serviceDate ?? undefined }, signal)
}
