import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, expect, it, vi } from 'vitest'
import { crowdForecast, vehicleForecast } from '../api'
import { DEFAULT_SETTINGS, SettingsContext } from '../settings'
import { makeItinerary } from '../test/fixtures'
import type { Vehicle, VehicleForecastResponse } from '../types'
import { RouteMap } from './RouteMap'

vi.mock('../api', () => ({ crowdForecast: vi.fn(), vehicleForecast: vi.fn() }))
vi.mock('react-leaflet', () => {
  const map = {
    getBounds: () => ({ getSouth: () => 54.25, getWest: () => 18.3, getNorth: () => 54.65, getEast: () => 18.95 }),
    getSize: () => ({ x: 1000, y: 700 }), fitBounds: vi.fn(), setView: vi.fn(), on: vi.fn(), off: vi.fn(),
  }
  const container = ({ children }: { children?: ReactNode }) => <div>{children}</div>
  return {
    MapContainer: container, Pane: container, Circle: container,
    CircleMarker: ({ children, radius }: { children?: ReactNode; radius: number }) => <div data-radius={radius}>{children}</div>,
    Polygon: container, Polyline: container, Tooltip: container, TileLayer: () => null, ZoomControl: () => null,
    useMap: () => map, useMapEvents: () => null,
  }
})

const at = '2026-11-16T07:20:00+01:00'
const props = {
  origin: null, destination: null, itinerary: null, userPosition: null, vehicles: [],
  at, crowdTolerance: 35, onSelectStop: vi.fn(), trackingLoading: false, trackingErrors: [],
}

beforeEach(() => {
  vi.mocked(crowdForecast).mockReset().mockImplementation(async at => ({
    at, until: new Date(new Date(at).getTime() + 3600000).toISOString(),
    areas: [], changes: [], flows: [], estimated: true,
  }))
  vi.mocked(vehicleForecast).mockReset().mockImplementation(async at => ({
    at, until: new Date(new Date(at).getTime() + 3600000).toISOString(), predictions: [], estimated: true,
  }))
})

it('keeps the crowd legend collapsed until hover or keyboard focus', async () => {
  render(<RouteMap {...props} />)
  const button = screen.getByRole('button', { name: 'Crowd map legend' })
  expect(button).toHaveAttribute('title', 'Crowd map legend')
  expect(button.textContent).toBe('')
  expect(button.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
  expect(button).toHaveAttribute('aria-expanded', 'false')
  expect(screen.queryByText('Estimated area crowd')).not.toBeInTheDocument()
  fireEvent.mouseEnter(button.parentElement!)
  expect(screen.getByText('Estimated area crowd')).toBeInTheDocument()
  expect(button.querySelector('svg')).not.toBeInTheDocument()
  fireEvent.mouseLeave(button.parentElement!)
  expect(screen.queryByText('Estimated area crowd')).not.toBeInTheDocument()
  expect(button.querySelector('svg')).toBeInTheDocument()
  fireEvent.focus(button)
  expect(screen.getByText('Estimated area crowd')).toBeInTheDocument()
  expect(button.querySelector('svg')).not.toBeInTheDocument()
  fireEvent.blur(button)
  fireEvent.click(button)
  expect(screen.getByText('Estimated area crowd')).toBeInTheDocument()
  expect(button.querySelector('svg')).not.toBeInTheDocument()
  fireEvent.click(button)
  expect(screen.queryByText('Estimated area crowd')).not.toBeInTheDocument()
  expect(button.querySelector('svg')).toBeInTheDocument()
  fireEvent.mouseEnter(button.parentElement!)
  fireEvent.focus(button)
  fireEvent.click(button)
  fireEvent.mouseLeave(button.parentElement!)
  expect(button).toHaveAttribute('aria-expanded', 'false')
  expect(button.querySelector('svg')).toBeInTheDocument()
  await waitFor(() => expect(crowdForecast).toHaveBeenCalled())
})

it('shows live bus details on hover or focus and closes on leave or Escape', () => {
  const itinerary = makeItinerary('original', 30)
  const { rerender } = render(<RouteMap {...props} itinerary={itinerary} />)
  const icon = screen.getByRole('button', { name: 'Live bus tracking' })
  expect(icon.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
  expect(icon).toHaveAttribute('aria-expanded', 'false')
  expect(screen.queryByText('Live buses: 0')).not.toBeInTheDocument()
  fireEvent.mouseEnter(icon.parentElement!)
  expect(icon).toHaveAttribute('aria-expanded', 'true')
  expect(screen.getByText('Live buses: 0')).toBeInTheDocument()
  fireEvent.focus(icon)
  fireEvent.click(icon)
  fireEvent.mouseLeave(icon.parentElement!)
  expect(icon).toHaveAttribute('aria-expanded', 'false')
  expect(screen.queryByText('Live buses: 0')).not.toBeInTheDocument()
  fireEvent.blur(icon)
  fireEvent.focus(icon)
  expect(screen.getByText('Live buses: 0')).toBeInTheDocument()
  fireEvent.keyDown(icon, { key: 'Escape' })
  expect(icon).toHaveAttribute('aria-expanded', 'false')
  expect(screen.queryByText('Live buses: 0')).not.toBeInTheDocument()
  fireEvent.blur(icon)
  fireEvent.focus(icon)
  fireEvent.blur(icon)
  expect(icon).toHaveAttribute('aria-expanded', 'false')
  rerender(<RouteMap {...props} />)
  expect(screen.queryByRole('button', { name: 'Live bus tracking' })).not.toBeInTheDocument()
})

it('keeps tracking messages accessible while collapsed and updates the open legend', () => {
  const itinerary = makeItinerary('original', 30)
  const vehicle: Vehicle = {
    trip_id: 'T8-1', route_id: '8', vehicle_id: 'v1', label: null,
    lat: 54.35, lon: 18.62, bearing: null, timestamp: 1800000000,
  }
  const { rerender } = render(<RouteMap {...props} itinerary={itinerary} trackingLoading />)
  const icon = screen.getByRole('button', { name: 'Live bus tracking' })
  fireEvent.mouseEnter(icon.parentElement!)
  expect(screen.getByText('Loading selected-trip GPS positions…')).toBeInTheDocument()
  rerender(<RouteMap {...props} itinerary={itinerary} vehicles={[vehicle]} trackingErrors={['Live vehicle feed is unavailable']} />)
  expect(screen.getByText('Live buses: 1')).toBeInTheDocument()
  expect(screen.getByRole('status')).toHaveTextContent('Live vehicle feed is unavailable')
  expect(icon).toHaveAttribute('aria-describedby', 'live-tracking-messages')
  expect(icon.querySelector('.live-tracking-indicator')).toBeInTheDocument()
  fireEvent.mouseLeave(icon.parentElement!)
  expect(screen.queryByText('Live buses: 1')).not.toBeInTheDocument()
  expect(screen.getByRole('status')).toHaveClass('visually-hidden')
  expect(screen.getByRole('status')).toHaveTextContent('Live vehicle feed is unavailable')
})

it('localizes the live bus icon and legend', () => {
  render(
    <SettingsContext.Provider value={{ ...DEFAULT_SETTINGS, language: 'pl' }}>
      <RouteMap {...props} itinerary={makeItinerary('original', 30)} />
    </SettingsContext.Provider>,
  )
  const icon = screen.getByRole('button', { name: 'Śledzenie autobusów na żywo' })
  fireEvent.mouseEnter(icon.parentElement!)
  expect(screen.getByText('Autobusy na żywo: 0')).toBeInTheDocument()
})

it('changes both forecast requests with the optional time slider', async () => {
  render(
    <SettingsContext.Provider value={{ ...DEFAULT_SETTINGS, timeSlider: true }}>
      <RouteMap {...props} itinerary={makeItinerary('original', 30)} />
    </SettingsContext.Provider>,
  )
  const slider = screen.getByRole('slider', { name: /Forecast time:/ })
  fireEvent.change(slider, { target: { value: '60' } })
  const expected = new Date(new Date(at).getTime() + 3600000).toISOString()
  await waitFor(() => expect(vi.mocked(crowdForecast).mock.calls.at(-1)?.[0]).toBe(expected))
  await waitFor(() => expect(vi.mocked(vehicleForecast).mock.calls.at(-1)?.[0]).toBe(expected))
  expect(vi.mocked(vehicleForecast).mock.calls[0][2]?.aborted).toBe(true)
})

it('enlarges a hovered alternative marker, restores its size and removes previews when dismissed', () => {
  const stop = { stop_id: 'ALT', name: 'Alternative stop', lat: 54.36, lon: 18.62, distance_m: 300, crowd_index: 20 }
  const { rerender } = render(<RouteMap {...props} alternativeStops={[stop]} />)
  const marker = () => screen.getByText('Alternative stop · crowd 20%').parentElement
  expect(marker()).toHaveAttribute('data-radius', '6')
  rerender(<RouteMap {...props} alternativeStops={[stop]} hoveredAlternativeStop="ALT" />)
  expect(marker()).toHaveAttribute('data-radius', '11')
  rerender(<RouteMap {...props} alternativeStops={[stop]} hoveredAlternativeStop={null} />)
  expect(marker()).toHaveAttribute('data-radius', '6')
  rerender(<RouteMap {...props} />)
  expect(screen.queryByText('Alternative stop · crowd 20%')).not.toBeInTheDocument()
})

it('discards old vehicle forecasts after the selected route changes', async () => {
  let complete: ((response: VehicleForecastResponse) => void) | undefined
  vi.mocked(vehicleForecast).mockReturnValueOnce(new Promise(resolve => { complete = resolve }))
  const { rerender } = render(<RouteMap {...props} itinerary={makeItinerary('original', 30)} />)
  const replacement = makeItinerary('replacement', 20)
  replacement.legs[1].trip_id = 'T9-1'
  vi.mocked(vehicleForecast).mockResolvedValue({
    at, until: '2026-11-16T08:20:00+01:00', estimated: true,
    predictions: [{ trip_id: 'T9-1', name: '9', lat: 54.35, lon: 18.62, at }],
  })
  rerender(<RouteMap {...props} itinerary={replacement} />)
  await screen.findByText(/Predicted 9 at/)
  await act(async () => complete?.({
    at, until: '2026-11-16T08:20:00+01:00', estimated: true,
    predictions: [{ trip_id: 'T8-1', name: '8', lat: 54.35, lon: 18.62, at }],
  }))
  expect(screen.queryByText(/Predicted 8 at/)).not.toBeInTheDocument()
  expect(screen.getByText(/Predicted 9 at/)).toBeInTheDocument()
})
