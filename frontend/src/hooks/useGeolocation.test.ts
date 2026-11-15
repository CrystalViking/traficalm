import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useGeolocation } from './useGeolocation'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('does not access GPS until enabled, stops watching on disable and ignores late updates', () => {
  const watchPosition = vi.fn<Geolocation['watchPosition']>().mockReturnValue(7)
  const clearWatch = vi.fn()
  vi.stubGlobal('navigator', { geolocation: { watchPosition, clearWatch } })
  const { result, rerender } = renderHook(({ enabled }) => useGeolocation(enabled), { initialProps: { enabled: false } })
  expect(watchPosition).not.toHaveBeenCalled()
  expect(result.current).toEqual({ position: null, error: null })
  rerender({ enabled: true })
  const oldSuccess = watchPosition.mock.calls[0][0]
  const position = { coords: { latitude: 54.35, longitude: 18.65, accuracy: 10 } } as GeolocationPosition
  act(() => oldSuccess(position))
  expect(result.current.position).toEqual({ lat: 54.35, lon: 18.65, accuracy: 10 })
  rerender({ enabled: false })
  expect(clearWatch).toHaveBeenCalledWith(7)
  expect(result.current.position).toBeNull()
  rerender({ enabled: true })
  act(() => oldSuccess(position))
  expect(result.current.position).toBeNull()
})
