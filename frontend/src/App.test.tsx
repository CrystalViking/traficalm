import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { continueJourney, nearbyStops, planJourney, prepareCity, tripRealtime } from './api'
import App from './App'
import type { NearbyStop, Place, PlanResponse, TripRealtime, Vehicle } from './types'
import { makeItinerary } from './test/fixtures'

vi.mock('./api', () => ({ planJourney: vi.fn(), prepareCity: vi.fn(), tripRealtime: vi.fn(), nearbyStops: vi.fn(), continueJourney: vi.fn() }))
vi.mock('./hooks/useGeolocation', () => ({ useGeolocation: () => ({ position: null, error: null }) }))
vi.mock('./components/RouteMap', () => ({ RouteMap: ({ itinerary, vehicles, alternativeStops = [], hoveredAlternativeStop }: {
  itinerary: { id: string } | null; vehicles: Vehicle[]; alternativeStops?: NearbyStop[]; hoveredAlternativeStop?: string | null
}) => (
  <div><span>Map route: {itinerary?.id}</span><span>Live vehicles: {vehicles.map(vehicle => vehicle.vehicle_id).join(',')}</span>
    <span>Alternative map stops: {alternativeStops.map(stop => stop.name).join(',')}</span>
    <span>Hovered map stop: {hoveredAlternativeStop}</span></div>
) }))
vi.mock('./components/AddressInput', () => ({
  AddressInput: ({ label, onChange }: { label: string; onChange: (place: Place) => void }) => (
    <button type="button" onClick={() => onChange({ name: label, lat: 54.35, lon: 18.65 })}>{label}</button>
  ),
}))

const response = (id: string, avoided: string[] = []): PlanResponse => {
  const itinerary = makeItinerary(id, 30)
  itinerary.legs[1].stops[1].crowd_index = 75
  return {
    city: 'gdansk',
    origin: { name: 'Home', lat: 54.35, lon: 18.6 },
    destination: { name: 'Office', lat: 54.36, lon: 18.63 },
    departure: '2026-11-16T07:20:00+01:00',
    laya_enabled: false, crowd_tolerance: 60, avoided_stop_ids: avoided,
    itineraries: [itinerary],
    laya_requested: false, laya_status: 'disabled', laya_message: null,
  }
}

const exitChoice = async (key: string) => {
  await userEvent.click(document.querySelector(`[data-exit-key="${key}"]`) as HTMLElement)
}

async function plan() {
  const close = screen.queryByRole('button', { name: 'Close settings' })
  if (close) await userEvent.click(close)
  await userEvent.click(screen.getByRole('button', { name: 'From (A)' }))
  await userEvent.click(screen.getByRole('button', { name: 'To (B)' }))
  await userEvent.click(screen.getByRole('button', { name: 'Find calm routes' }))
  await screen.findByRole('region', { name: 'Selected route details' })
}

beforeEach(() => {
  localStorage.clear()
  vi.mocked(planJourney).mockReset()
  vi.mocked(prepareCity).mockReset().mockImplementation(async city => ({
    city, name: city, agency: `Transit ${city}`, source_url: 'https://example.com', license: 'Test',
    ready: true, imported_at: null, start_date: null, end_date: null, realtime_supported: city !== 'wroclaw',
    attributions: city === 'warsaw' ? [{ organization_name: 'Mikołaj Kuranowski', attribution_url: 'https://mkuran.pl/' }] : [],
  }))
  vi.mocked(nearbyStops).mockReset()
  vi.mocked(continueJourney).mockReset()
  vi.mocked(tripRealtime).mockResolvedValue({ trip_id: 'T8-1', has_realtime: false, stops: [], vehicle: null })
})

describe('crowd tolerance and route editing', () => {
  it.each(['poznan', 'warsaw'] as const)('prepares %s, clears old routes and persists the choice', async city => {
    vi.mocked(planJourney).mockResolvedValue(response('old-city'))
    const { unmount } = render(<App />)
    await plan()
    await userEvent.click(screen.getByRole('button', { name: 'Settings' }))
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'City' }), city)
    await waitFor(() => expect(prepareCity).toHaveBeenLastCalledWith(city, expect.any(AbortSignal)))
    expect(screen.queryByRole('region', { name: 'Selected route details' })).not.toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Settings' })).toBeInTheDocument()
    if (city === 'warsaw') {
      expect(await screen.findByRole('link', { name: 'Mikołaj Kuranowski' })).toHaveAttribute('href', 'https://mkuran.pl/')
      expect(screen.getByText(/Warsaw uses a community GTFS conversion/)).toBeInTheDocument()
    }
    vi.mocked(planJourney).mockResolvedValue({ ...response('new-city-route'), city })
    await plan()
    expect(vi.mocked(planJourney).mock.calls.at(-1)?.[7]).toBe(city)
    expect(vi.mocked(tripRealtime).mock.calls.at(-1)?.[3]).toBe(city)
    expect(JSON.parse(localStorage.getItem('traficalm-settings')!).city).toBe(city)
    unmount()
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: 'Settings' }))
    expect(screen.getByRole('combobox', { name: 'City' })).toHaveValue(city)
  })

  it('disables planning while the city is preparing and exposes a retry after failure', async () => {
    vi.mocked(prepareCity).mockRejectedValueOnce(new Error('Download failed'))
    render(<App />)
    expect(screen.getByRole('button', { name: 'Find calm routes' })).toBeDisabled()
    await screen.findByRole('alert')
    await userEvent.click(screen.getByRole('button', { name: 'Retry timetable download' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Find calm routes' })).toBeEnabled())
  })

  it('keeps GPS off by default and persists explicit opt-in', async () => {
    const { unmount } = render(<App />)
    await userEvent.click(screen.getByRole('button', { name: 'Settings' }))
    const gps = screen.getByRole('checkbox', { name: 'Use GPS' })
    expect(gps).not.toBeChecked()
    await userEvent.click(gps)
    expect(JSON.parse(localStorage.getItem('traficalm-settings')!).gps).toBe(true)
    unmount()
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: 'Settings' }))
    expect(screen.getByRole('checkbox', { name: 'Use GPS' })).toBeChecked()
  })
  it('changes language and dark mode immediately and restores them after reopening the app', async () => {
    vi.mocked(planJourney).mockResolvedValue(response('original'))
    const { unmount } = render(<App />)
    await userEvent.click(screen.getByRole('button', { name: 'Settings' }))
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Language' }), 'pl')
    expect(screen.getByRole('button', { name: 'Znajdź spokojne trasy' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Tryb ciemny' }))
    expect(document.documentElement).toHaveAttribute('lang', 'pl')
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark')
    await userEvent.click(screen.getByRole('button', { name: 'Zamknij ustawienia' }))
    await userEvent.click(screen.getByRole('button', { name: 'Z (A)' }))
    await userEvent.click(screen.getByRole('button', { name: 'Do (B)' }))
    await userEvent.click(screen.getByRole('button', { name: 'Znajdź spokojne trasy' }))
    await screen.findByRole('region', { name: 'Szczegóły wybranej trasy' })
    await userEvent.click(screen.getByRole('button', { name: 'Tak, omiń Stop 3' }))
    expect(await screen.findByRole('region', { name: 'Zmień trasę przy Stop 3' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Znajdź alternatywy omijające Stop 3' })).not.toBeInTheDocument()
    unmount()
    render(<App />)
    expect(screen.getByRole('button', { name: 'Ustawienia' })).toBeInTheDocument()
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark')
    await userEvent.click(screen.getByRole('button', { name: 'Ustawienia' }))
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Język' }), 'en')
    await userEvent.click(screen.getByRole('checkbox', { name: 'Dark Mode' }))
    expect(document.documentElement).toHaveAttribute('lang', 'en')
    expect(document.documentElement).toHaveAttribute('data-theme', 'light')
  })

  it('validates and saves the walking radius and sends it to stop search and continuation', async () => {
    vi.mocked(planJourney).mockResolvedValue(response('original'))
    const nearby = { stop_id: 'S4', name: 'New stop', lat: 54.361, lon: 18.642, distance_m: 3000, crowd_index: 20 }
    vi.mocked(nearbyStops).mockResolvedValue([nearby])
    vi.mocked(continueJourney).mockResolvedValue(response('continued'))
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: 'Settings' }))
    const cap = screen.getByRole('spinbutton', { name: 'Walking distance cap (km)' })
    fireEvent.change(cap, { target: { value: '0' } })
    expect(screen.getByRole('alert')).toHaveTextContent('Enter a radius between')
    fireEvent.change(cap, { target: { value: '3.5' } })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Close settings' }))
    await plan()
    await userEvent.click(screen.getByRole('button', { name: 'Yes, avoid Stop 3' }))
    await userEvent.click(screen.getByRole('button', { name: 'Get off and continue' }))
    await exitChoice('1:3')
    await userEvent.click(await screen.findByRole('button', { name: /New stop ·/ }))
    expect(vi.mocked(nearbyStops).mock.calls[0][3]).toBe(3500)
    expect(vi.mocked(continueJourney).mock.calls[0][8]).toBe(3500)
    await userEvent.click(screen.getByRole('button', { name: 'Settings' }))
    expect(screen.getByRole('spinbutton', { name: 'Walking distance cap (km)' })).toHaveValue(3.5)
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Walking distance cap (km)' }), { target: { value: '4' } })
    await userEvent.click(screen.getByRole('button', { name: 'Close settings' }))
    expect(screen.queryByRole('region', { name: 'Change route at Stop 3' })).not.toBeInTheDocument()
    expect(screen.getByText('Map route: original')).toBeInTheDocument()
  })

  it('reports corrupt saved settings rather than silently accepting them', () => {
    localStorage.setItem('traficalm-settings', JSON.stringify({ language: 'fr', darkMode: true, walkRadiusM: -1 }))
    render(<App />)
    expect(screen.getByRole('alert')).toHaveTextContent('Saved app settings are invalid')
    expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument()
  })

  it('defaults to 60%, sends it when planning and preserves saved changes', async () => {
    vi.mocked(planJourney).mockResolvedValue(response('original'))
    const { unmount } = render(<App />)
    await userEvent.click(screen.getByRole('button', { name: 'Settings' }))
    expect(screen.getByRole('slider', { name: 'Crowd tolerance: 60%' })).toHaveValue('60')
    await plan()
    expect(vi.mocked(planJourney).mock.calls[0][4]).toBe(60)
    await userEvent.click(screen.getByRole('button', { name: 'Settings' }))
    const slider = screen.getByRole('slider', { name: 'Crowd tolerance: 60%' })
    fireEvent.change(slider, { target: { value: '42' } })
    expect(localStorage.getItem('traficalm-crowd-tolerance')).toBe('42')
    unmount()
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: 'Settings' }))
    expect(screen.getByRole('slider', { name: 'Crowd tolerance: 42%' })).toHaveValue('42')
    await plan()
    expect(vi.mocked(planJourney).mock.calls.at(-1)?.[4]).toBe(42)
  })

  it('preserves a previously saved 35% tolerance', async () => {
    localStorage.setItem('traficalm-crowd-tolerance', '35')
    vi.mocked(planJourney).mockResolvedValue(response('original'))
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: 'Settings' }))
    expect(screen.getByRole('slider', { name: 'Crowd tolerance: 35%' })).toHaveValue('35')
    await plan()
    expect(vi.mocked(planJourney).mock.calls[0][4]).toBe(35)
  })

  it('reports an invalid saved tolerance and falls back to 60%', async () => {
    localStorage.setItem('traficalm-crowd-tolerance', 'invalid')
    render(<App />)
    expect(screen.getByRole('alert')).toHaveTextContent('Saved crowd tolerance is invalid; using 60%.')
    await userEvent.click(screen.getByRole('button', { name: 'Settings' }))
    expect(screen.getByRole('slider', { name: 'Crowd tolerance: 60%' })).toHaveValue('60')
  })

  it('prompts only for stop crowd above the default 60% threshold', async () => {
    const original = response('original')
    original.itineraries[0].legs[1].stops[0].crowd_index = 60
    original.itineraries[0].legs[1].stops[1].crowd_index = 61
    vi.mocked(planJourney).mockResolvedValue(original)
    render(<App />)
    await plan()
    expect(screen.queryByRole('button', { name: 'Yes, avoid Stop 1' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Yes, avoid Stop 3' })).toBeInTheDocument()
  })

  it('removes a stop and applies the replanned route only after confirmation', async () => {
    const alternative = response('alternative', ['S3'])
    alternative.itineraries[0].legs[1].route = {
      route_id: '9', short_name: '9', long_name: 'Alternative bus', mode: 'bus', color: null,
    }
    vi.mocked(planJourney).mockResolvedValueOnce(response('original')).mockResolvedValueOnce(alternative)
    render(<App />)
    await plan()
    await userEvent.click(screen.getByRole('button', { name: 'Yes, avoid Stop 3' }))
    const panel = await screen.findByRole('region', { name: 'Change route at Stop 3' })
    expect(screen.queryByRole('button', { name: 'Find alternatives avoiding Stop 3' })).not.toBeInTheDocument()
    expect(vi.mocked(planJourney)).toHaveBeenCalledTimes(1)
    await userEvent.click(within(panel).getByRole('button', { name: 'Remove stop and replan whole journey' }))
    await within(panel).findByText('🚌 9')
    expect(vi.mocked(planJourney).mock.calls[1][5]).toEqual(['S3'])
    const details = screen.getByRole('region', { name: 'Selected route details' })
    expect(within(details).getByText('🚋 8')).toBeInTheDocument()
    await userEvent.click(within(panel).getByRole('button', { name: /30%/ }))
    expect(screen.getByText('Map route: alternative')).toBeInTheDocument()
    await userEvent.click(within(panel).getByRole('button', { name: 'Confirm new route' }))
    await waitFor(() => expect(within(screen.getByRole('region', { name: 'Selected route details' })).getByText('🚌 9')).toBeInTheDocument())
    expect(screen.queryByRole('region', { name: 'Change route at Stop 3' })).not.toBeInTheDocument()
    expect(screen.getByText('Map route: alternative')).toBeInTheDocument()
  })

  it('reports unavailable alternatives and keeps the selected route', async () => {
    vi.mocked(planJourney).mockResolvedValueOnce(response('original')).mockResolvedValueOnce({
      ...response('none', ['S3']), itineraries: [],
    })
    render(<App />)
    await plan()
    await userEvent.click(screen.getByRole('button', { name: 'Yes, avoid Stop 3' }))
    await userEvent.click(screen.getByRole('button', { name: 'Remove stop and replan whole journey' }))
    await screen.findByText(/No onward route was found/)
    expect(within(screen.getByRole('region', { name: 'Selected route details' })).getByText('🚋 8')).toBeInTheDocument()
  })

  it('defaults Laya off and persists opting in for planning and alternatives', async () => {
    vi.mocked(planJourney).mockResolvedValue(response('original'))
    const { unmount } = render(<App />)
    await userEvent.click(screen.getByRole('button', { name: 'Settings' }))
    expect(screen.getByRole('checkbox', { name: 'Use Laya AI' })).not.toBeChecked()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Use Laya AI' }))
    await plan()
    expect(vi.mocked(planJourney).mock.calls[0][6]).toBe(true)
    await userEvent.click(screen.getByRole('button', { name: 'Yes, avoid Stop 3' }))
    await userEvent.click(screen.getByRole('button', { name: 'Remove stop and replan whole journey' }))
    await waitFor(() => expect(vi.mocked(planJourney).mock.calls[1][6]).toBe(true))
    unmount()
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: 'Settings' }))
    expect(screen.getByRole('checkbox', { name: 'Use Laya AI' })).toBeChecked()
  })

  it.each([1, 3])('chooses exit sequence %s and shows routes below the new stop before updating the map', async (sequence) => {
    const nearby = { stop_id: 'S4', name: 'New stop', lat: 54.361, lon: 18.642, distance_m: 900, crowd_index: 20 }
    vi.mocked(planJourney).mockResolvedValue(response('original'))
    vi.mocked(nearbyStops).mockResolvedValue([nearby])
    vi.mocked(continueJourney).mockResolvedValue(response('continued', ['S3']))
    render(<App />)
    await plan()
    await userEvent.click(screen.getByRole('button', { name: 'Yes, avoid Stop 3' }))
    await userEvent.click(screen.getByRole('button', { name: 'Get off and continue' }))
    await exitChoice(`1:${sequence}`)
    const stopButton = await screen.findByRole('button', { name: /New stop ·/ })
    expect(screen.getByText('Alternative map stops: New stop')).toBeInTheDocument()
    fireEvent.mouseEnter(stopButton)
    expect(screen.getByText('Hovered map stop: S4')).toBeInTheDocument()
    fireEvent.mouseLeave(stopButton)
    expect(screen.getByText('Hovered map stop:')).toBeInTheDocument()
    await userEvent.click(await screen.findByRole('button', { name: /New stop ·/ }))
    const routes = await screen.findByRole('region', { name: 'Routes from New stop' })
    await within(routes).findByRole('button', { name: /30%/ })
    expect(vi.mocked(continueJourney).mock.calls[0][2].stop_sequence).toBe(sequence)
    expect(vi.mocked(continueJourney).mock.calls[0][3]).toEqual(nearby)
    expect(vi.mocked(continueJourney).mock.calls[0][5]).toEqual(['S3'])
    expect(screen.getByText('Map route: original')).toBeInTheDocument()
    expect(within(routes).getByRole('button', { name: 'Confirm new route' })).toBeDisabled()
    await userEvent.click(within(routes).getByRole('button', { name: /30%/ }))
    expect(screen.getByText('Map route: continued')).toBeInTheDocument()
    await userEvent.click(within(routes).getByRole('button', { name: 'Confirm new route' }))
    expect(screen.getByText('Map route: continued')).toBeInTheDocument()
    expect(screen.getByText('Alternative map stops:')).toBeInTheDocument()
  })

  it('discards an in-flight continuation when the exit stop changes', async () => {
    let complete: ((result: PlanResponse) => void) | undefined
    vi.mocked(planJourney).mockResolvedValue(response('original'))
    vi.mocked(nearbyStops).mockResolvedValue([
      { stop_id: 'S4', name: 'New stop', lat: 54.361, lon: 18.642, distance_m: 900, crowd_index: 20 },
    ])
    vi.mocked(continueJourney).mockReturnValue(new Promise(resolve => { complete = resolve }))
    render(<App />)
    await plan()
    await userEvent.click(screen.getByRole('button', { name: 'Yes, avoid Stop 3' }))
    await userEvent.click(screen.getByRole('button', { name: 'Get off and continue' }))
    await exitChoice('1:3')
    await userEvent.click(await screen.findByRole('button', { name: /New stop ·/ }))
    await screen.findByText(/Finding onward routes/)
    await exitChoice('1:1')
    expect(vi.mocked(continueJourney).mock.calls[0][7]?.aborted).toBe(true)
    await act(async () => complete?.(response('stale')))
    expect(screen.queryByRole('button', { name: 'Confirm new route' })).not.toBeInTheDocument()
    expect(screen.getByText('Map route: original')).toBeInTheDocument()
  })

  it('offers exits from previous transit legs with distinct stop-visit keys', async () => {
    const original = response('original')
    const firstRide = original.itineraries[0].legs[1]
    original.itineraries[0].legs.push({
      ...firstRide, trip_id: 'T10-1',
      stops: [
        { ...firstRide.stops[0], stop_id: 'BOARD', name: 'Transfer boarding', crowd_index: 80 },
        { ...firstRide.stops[1], stop_id: 'LATER', name: 'Later stop' },
      ],
    })
    vi.mocked(planJourney).mockResolvedValue(original)
    vi.mocked(nearbyStops).mockResolvedValue([
      { stop_id: 'NEW', name: 'New stop', lat: 54.361, lon: 18.642, distance_m: 300, crowd_index: 20 },
    ])
    vi.mocked(continueJourney).mockResolvedValue(response('continued'))
    render(<App />)
    await plan()
    await userEvent.click(screen.getByRole('button', { name: 'Yes, avoid Transfer boarding' }))
    await userEvent.click(screen.getByRole('button', { name: 'Get off and continue' }))
    const choices = screen.getByRole('radiogroup', { name: 'Where to get off' })
    expect(within(choices).getByRole('radio', { name: 'Stop 1 (before Stop 3)' })).toHaveAttribute('data-exit-key', '1:1')
    expect(within(choices).getByRole('radio', { name: 'Stop 3 (before Transfer boarding)' })).toHaveAttribute('data-exit-key', '1:3')
    expect(within(choices).getByRole('radio', { name: 'Transfer boarding (selected stop)' })).toHaveAttribute('data-exit-key', '3:1')
    expect(within(choices).getByRole('radio', { name: 'Stop 3 (before Transfer boarding)' })).toBeChecked()
    await exitChoice('1:3')
    await userEvent.click(await screen.findByRole('button', { name: /New stop ·/ }))
    expect(vi.mocked(continueJourney).mock.calls[0][2]).toBe(firstRide.stops[1])
    await userEvent.click(screen.getByRole('button', { name: 'Keep current route' }))
    expect(screen.getByText('Alternative map stops:')).toBeInTheDocument()
    expect(screen.getByText('Map route: original')).toBeInTheDocument()
  })

  it.each([1, 6])('limits exit choices to three preceding route stops and labels their next stop (selected %s)', async selectedSequence => {
    const original = response('original')
    const ride = original.itineraries[0].legs[1]
    ride.stops = Array.from({ length: 6 }, (_, index) => ({
      ...ride.stops[0], stop_id: `ROUTE${index + 1}`, stop_sequence: index + 1,
      name: `Route stop ${index + 1}`, crowd_index: index + 1 === selectedSequence ? 80 : 20,
    }))
    vi.mocked(planJourney).mockResolvedValue(original)
    vi.mocked(nearbyStops).mockResolvedValue([
      { stop_id: 'NEAREST', name: 'Geographically closest stop', lat: 54.351, lon: 18.61, distance_m: 10, crowd_index: 20 },
    ])
    render(<App />)
    await plan()
    await userEvent.click(screen.getByRole('button', { name: `Yes, avoid Route stop ${selectedSequence}` }))
    await userEvent.click(screen.getByRole('button', { name: 'Get off and continue' }))
    const choices = screen.getByRole('radiogroup', { name: 'Where to get off' })
    const preceding = Array.from({ length: Math.min(3, selectedSequence - 1) }, (_, index) =>
      selectedSequence - Math.min(3, selectedSequence - 1) + index)
    expect(within(choices).getAllByRole('radio').map(option => option.getAttribute('aria-label'))).toEqual([
      ...preceding.map(sequence => `Route stop ${sequence} (before Route stop ${sequence + 1})`),
      `Route stop ${selectedSequence} (selected stop)`,
    ])
    const exitSequence = preceding[0] ?? selectedSequence
    await exitChoice(`1:${exitSequence}`)
    await screen.findByRole('button', { name: /Geographically closest stop ·/ })
    expect(vi.mocked(nearbyStops).mock.calls.at(-1)?.[0]).toBe(ride.stops[exitSequence - 1])
  })

  it('uses the 500 m default and removes stops with no onward route, but not stops with a service error', async () => {
    vi.mocked(planJourney).mockResolvedValue(response('original'))
    vi.mocked(nearbyStops).mockResolvedValue([
      { stop_id: 'NONE', name: 'Unavailable stop', lat: 54.361, lon: 18.642, distance_m: 300, crowd_index: 20 },
      { stop_id: 'RETRY', name: 'Retry stop', lat: 54.362, lon: 18.641, distance_m: 400, crowd_index: 20 },
    ])
    vi.mocked(continueJourney).mockResolvedValueOnce({ ...response('none'), itineraries: [] })
      .mockRejectedValueOnce(new Error('Service unavailable'))
    render(<App />)
    await plan()
    await userEvent.click(screen.getByRole('button', { name: 'Yes, avoid Stop 3' }))
    await userEvent.click(screen.getByRole('button', { name: 'Get off and continue' }))
    await exitChoice('1:3')
    await userEvent.click(await screen.findByRole('button', { name: /Unavailable stop ·/ }))
    await screen.findByText('No onward route from Unavailable stop. This stop was removed; choose another stop.')
    expect(screen.queryByRole('button', { name: /Unavailable stop ·/ })).not.toBeInTheDocument()
    expect(screen.getByText('Alternative map stops: Retry stop')).toBeInTheDocument()
    expect(vi.mocked(nearbyStops).mock.calls[0][3]).toBe(500)
    expect(vi.mocked(continueJourney).mock.calls[0][8]).toBe(500)
    await userEvent.click(screen.getByRole('button', { name: /Retry stop ·/ }))
    await screen.findByRole('alert')
    expect(screen.getByRole('button', { name: /Retry stop ·/ })).toBeInTheDocument()
    expect(screen.getByText('Map route: original')).toBeInTheDocument()
    vi.mocked(continueJourney).mockResolvedValueOnce({ ...response('none'), itineraries: [] })
    await userEvent.click(screen.getByRole('button', { name: /Retry stop ·/ }))
    await screen.findByText('No usable nearby stops remain. Choose another exit stop or increase the walking distance cap.')
    expect(screen.queryByRole('button', { name: /Retry stop ·/ })).not.toBeInTheDocument()
    expect(screen.getByText('Alternative map stops:')).toBeInTheDocument()
  })

  it('switches live tracking to the new service date and ignores a late old-trip response', async () => {
    let oldResponse: ((result: TripRealtime) => void) | undefined
    vi.mocked(tripRealtime).mockReturnValueOnce(new Promise(resolve => { oldResponse = resolve }))
    const replacement = response('replacement', ['S3'])
    replacement.itineraries[0].legs[1].service_date = '2026-11-17'
    vi.mocked(tripRealtime).mockResolvedValue({
      trip_id: 'T8-1', has_realtime: true, stops: [],
      vehicle: { trip_id: 'T8-1', route_id: '8', vehicle_id: 'new', label: null, lat: 54.35, lon: 18.62, bearing: null, timestamp: 1800000000 },
    })
    vi.mocked(planJourney).mockResolvedValueOnce(response('original')).mockResolvedValueOnce(replacement)
    render(<App />)
    await plan()
    await userEvent.click(screen.getByRole('button', { name: 'Yes, avoid Stop 3' }))
    await userEvent.click(screen.getByRole('button', { name: 'Remove stop and replan whole journey' }))
    const routes = await screen.findByRole('region', { name: 'Routes without this stop' })
    await userEvent.click(await within(routes).findByRole('button', { name: /30%/ }))
    await userEvent.click(within(routes).getByRole('button', { name: 'Confirm new route' }))
    await waitFor(() => expect(vi.mocked(tripRealtime).mock.calls.at(-1)?.[1]).toBe('2026-11-17'))
    await screen.findByText('Live vehicles: new')
    await act(async () => oldResponse?.({
      trip_id: 'T8-1', has_realtime: true, stops: [],
      vehicle: { trip_id: 'T8-1', route_id: '8', vehicle_id: 'old', label: null, lat: 54.35, lon: 18.62, bearing: null, timestamp: 1800000000 },
    }))
    expect(screen.getByText('Live vehicles: new')).toBeInTheDocument()
    expect(screen.queryByText(/Live vehicles: old/)).not.toBeInTheDocument()
  })
})
