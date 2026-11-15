import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { searchAddress } from '../api'
import type { AddressSuggestion, Place } from '../types'
import { AddressInput } from './AddressInput'
import { DEFAULT_SETTINGS, SettingsContext } from '../settings'

vi.mock('../api', () => ({
  searchAddress: vi.fn(async (q: string) => [
    { label: `${q} 1, Gdańsk`, lat: 54.35, lon: 18.65 },
    { label: `${q} 2, Gdańsk`, lat: 54.36, lon: 18.66 },
  ]),
  reverseGeocode: vi.fn(async () => ({ label: 'Długi Targ, Gdańsk', lat: 54.348, lon: 18.653 })),
}))

function Harness({ onPlace, initial = null }: { onPlace: (p: Place | null) => void; initial?: Place | null }) {
  const [value, setValue] = useState<Place | null>(initial)
  return (
    <>
      <AddressInput
        label="From (A)"
        value={value}
        onChange={(p) => {
          setValue(p)
          onPlace(p)
        }}
      />
      <button type="button" onClick={() => setValue(null)}>
        clear
      </button>
    </>
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('AddressInput', () => {
  it('does not request a GPS position when GPS is disabled', async () => {
    const getCurrentPosition = vi.fn()
    vi.stubGlobal('navigator', { geolocation: { getCurrentPosition } })
    render(<Harness onPlace={vi.fn()} />)
    const button = screen.getByRole('button', { name: /Use my GPS location/ })
    expect(button).toBeDisabled()
    await userEvent.click(button)
    expect(getCurrentPosition).not.toHaveBeenCalled()
  })
  it('shows real-time suggestions and selects one', async () => {
    const onPlace = vi.fn()
    render(<Harness onPlace={onPlace} />)
    await userEvent.type(screen.getByRole('combobox'), 'Grunwaldzka')
    const options = await screen.findAllByRole('option', {}, { timeout: 2000 })
    expect(options).toHaveLength(2)
    await userEvent.keyboard('{ArrowDown}{ArrowDown}{Enter}')
    expect(onPlace).toHaveBeenLastCalledWith({ name: 'Grunwaldzka 2, Gdańsk', lat: 54.36, lon: 18.66 })
    expect(screen.getByRole('combobox')).toHaveValue('Grunwaldzka 2, Gdańsk')
    expect(screen.queryByRole('option')).not.toBeInTheDocument()
  })

  it('hides old-city results immediately and ignores a late response after switching cities', async () => {
    let resolvePoznan: (items: AddressSuggestion[]) => void = () => { throw new Error('Missing Poznań request') }
    const poznanResponse = new Promise<AddressSuggestion[]>((resolve) => { resolvePoznan = resolve })
    const search = vi.mocked(searchAddress)
    search.mockResolvedValueOnce([{ label: 'Rynek, Gdańsk', lat: 54.35, lon: 18.65 }])
      .mockReturnValueOnce(poznanResponse)
      .mockResolvedValueOnce([{ label: 'Rynek, Wrocław', lat: 51.11, lon: 17.03 }])

    const onPlace = vi.fn()
    const { rerender } = render(
      <SettingsContext.Provider value={{ ...DEFAULT_SETTINGS, city: 'gdansk' }}><Harness onPlace={onPlace} /></SettingsContext.Provider>,
    )
    await userEvent.type(screen.getByRole('combobox'), 'Rynek')
    expect(await screen.findByRole('option')).toHaveTextContent('Rynek, Gdańsk')

    rerender(<SettingsContext.Provider value={{ ...DEFAULT_SETTINGS, city: 'poznan' }}><Harness onPlace={onPlace} /></SettingsContext.Provider>)
    expect(screen.queryByRole('option')).not.toBeInTheDocument()
    await waitFor(() => expect(search).toHaveBeenCalledWith('Rynek', expect.any(AbortSignal), 'poznan'))
    const poznanSignal = search.mock.calls.find((call) => call[2] === 'poznan')?.[1]

    rerender(<SettingsContext.Provider value={{ ...DEFAULT_SETTINGS, city: 'wroclaw' }}><Harness onPlace={onPlace} /></SettingsContext.Provider>)
    expect(await screen.findByRole('option')).toHaveTextContent('Rynek, Wrocław')
    expect(poznanSignal?.aborted).toBe(true)
    await act(async () => resolvePoznan([{ label: 'Rynek, Poznań', lat: 52.4, lon: 16.9 }]))
    expect(screen.getByRole('option')).toHaveTextContent('Rynek, Wrocław')
  })

  it('does not restore old-city suggestions when the new city search fails', async () => {
    const search = vi.mocked(searchAddress)
    search.mockResolvedValueOnce([{ label: 'Rynek, Gdańsk', lat: 54.35, lon: 18.65 }])
      .mockRejectedValueOnce(new Error('Unavailable'))
    const onPlace = vi.fn()
    const { rerender } = render(
      <SettingsContext.Provider value={{ ...DEFAULT_SETTINGS, city: 'gdansk' }}><Harness onPlace={onPlace} /></SettingsContext.Provider>,
    )
    await userEvent.type(screen.getByRole('combobox'), 'Rynek')
    await screen.findByRole('option')
    rerender(<SettingsContext.Provider value={{ ...DEFAULT_SETTINGS, city: 'poznan' }}><Harness onPlace={onPlace} /></SettingsContext.Provider>)
    expect(await screen.findByRole('alert')).toHaveTextContent('Address suggestions are unavailable')
    expect(screen.queryByRole('option')).not.toBeInTheDocument()
  })

  it('explains when no addresses match in the selected city', async () => {
    vi.mocked(searchAddress).mockResolvedValueOnce([])
    render(<SettingsContext.Provider value={{ ...DEFAULT_SETTINGS, city: 'poznan' }}><Harness onPlace={vi.fn()} /></SettingsContext.Provider>)
    await userEvent.type(screen.getByRole('combobox'), 'Unknown address')
    expect(await screen.findByRole('status')).toHaveTextContent('No addresses found in Poznań.')
  })

  it('uses the GPS location', async () => {
    vi.stubGlobal('navigator', {
      ...navigator,
      geolocation: {
        getCurrentPosition: (ok: PositionCallback) =>
          ok({ coords: { latitude: 54.348, longitude: 18.653, accuracy: 15 } } as GeolocationPosition),
      },
    })
    const onPlace = vi.fn()
    render(<SettingsContext.Provider value={{ ...DEFAULT_SETTINGS, gps: true }}><Harness onPlace={onPlace} /></SettingsContext.Provider>)
    await act(async () => {
      await userEvent.click(screen.getByRole('button', { name: /Use my GPS location/ }))
    })
    await waitFor(() =>
      expect(onPlace).toHaveBeenLastCalledWith({ name: 'My location – Długi Targ, Gdańsk', lat: 54.348, lon: 18.653 }),
    )
    expect(screen.getByRole('combobox')).toHaveValue('My location – Długi Targ, Gdańsk')
  })

  it('keeps typed text when editing a chosen place', async () => {
    const onPlace = vi.fn()
    render(<Harness onPlace={onPlace} initial={{ name: 'Długi Targ', lat: 54.348, lon: 18.653 }} />)
    const input = screen.getByRole('combobox')
    expect(input).toHaveValue('Długi Targ')
    await userEvent.type(input, ' 1')
    expect(input).toHaveValue('Długi Targ 1')
    expect(onPlace).toHaveBeenCalledWith(null)
  })

  it('clears the text when the place is reset from outside', async () => {
    render(<Harness onPlace={vi.fn()} initial={{ name: 'Wrzeszcz', lat: 54.38, lon: 18.6 }} />)
    await userEvent.click(screen.getByRole('button', { name: 'clear' }))
    expect(screen.getByRole('combobox')).toHaveValue('')
  })
})
