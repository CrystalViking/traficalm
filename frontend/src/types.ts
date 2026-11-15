import type { City } from './cities'

export interface CityStatus {
  city: City
  name: string
  agency: string
  source_url: string
  license: string
  ready: boolean
  imported_at: string | null
  start_date: string | null
  end_date: string | null
  realtime_supported: boolean
  attributions: { organization_name: string; attribution_url: string | null }[]
}

export interface Place {
  name: string
  lat: number
  lon: number
}

export interface NearbyStop extends Place {
  stop_id: string
  distance_m: number
  crowd_index: number
}

export interface AddressSuggestion {
  label: string
  lat: number
  lon: number
  type?: string | null
}

export interface StopCall {
  stop_id: string
  name: string
  lat: number
  lon: number
  stop_sequence: number
  scheduled_arrival: string
  scheduled_departure: string
  realtime_arrival: string | null
  realtime_departure: string | null
  delay_seconds: number | null
  crowd_index: number
}

export interface RouteInfo {
  route_id: string
  short_name: string
  long_name: string
  mode: string
  color: string | null
}

export interface Leg {
  mode: 'walk' | 'transit'
  start: Place
  end: Place
  departure: string
  arrival: string
  duration_seconds: number
  distance_m: number
  geometry: [number, number][]
  crowd_index: number
  crowd_reasons: string[]
  crowd_rule_based: number | null
  crowd_laya: number | null
  route: RouteInfo | null
  trip_id: string | null
  headsign: string | null
  service_date: string | null
  stops: StopCall[]
  walk_routed: boolean
}

export interface Itinerary {
  id: string
  crowd_index: number
  crowd_level: string
  crowd_avoiding: boolean
  departure: string
  arrival: string
  duration_seconds: number
  transfers: number
  walk_distance_m: number
  type: 'walk' | 'direct' | 'transfer'
  legs: Leg[]
  notes: string[]
}

export interface PlanResponse {
  city: City
  origin: Place
  destination: Place
  departure: string
  laya_enabled: boolean
  itineraries: Itinerary[]
  crowd_tolerance: number
  avoided_stop_ids: string[]
  laya_requested: boolean
  laya_status: 'used' | 'partial' | 'disabled' | 'unavailable' | 'not-needed'
  laya_message: string | null
}

export interface CrowdArea {
  id: string
  name: string
  kind: string
  lat: number
  lon: number
  radius_m: number
  crowd_index: number
  reasons: string[]
  geometry: [number, number][][]
}

export interface CrowdAreasResponse {
  at: string
  areas: CrowdArea[]
  estimated: boolean
}

export interface CrowdForecastResponse extends CrowdAreasResponse {
  until: string
  changes: (Place & { current_index: number; predicted_index: number; change: number })[]
  flows: { origin: Place; destination: Place; change_points: number }[]
}

export interface VehiclePrediction extends Place {
  trip_id: string
  at: string
}

export interface VehicleForecastResponse {
  at: string
  until: string
  predictions: VehiclePrediction[]
  estimated: boolean
}

export interface MapBounds {
  south: number
  west: number
  north: number
  east: number
}

export interface StopDelay {
  stop_id: string
  stop_sequence: number
  delay_seconds: number | null
  realtime_arrival: string | null
  realtime_departure: string | null
}

export interface Vehicle {
  trip_id: string | null
  route_id: string | null
  vehicle_id: string | null
  label: string | null
  lat: number
  lon: number
  bearing: number | null
  timestamp: number | null
}

export interface TripRealtime {
  trip_id: string
  has_realtime: boolean
  stops: StopDelay[]
  vehicle: Vehicle | null
  tracking_status?: 'live' | 'no-position' | 'unavailable' | 'stale' | 'not-current-service'
  tracking_message?: string | null
}
