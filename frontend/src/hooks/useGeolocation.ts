import { useEffect, useState } from 'react'

export interface GeoPosition {
  lat: number
  lon: number
  accuracy: number
}

/** Continuously tracks the user's GPS position (if the browser allows it). */
export function useGeolocation(enabled = false): { position: GeoPosition | null; error: string | null } {
  const [position, setPosition] = useState<GeoPosition | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [previousEnabled, setPreviousEnabled] = useState(enabled)
  if (enabled !== previousEnabled) {
    setPreviousEnabled(enabled)
    setPosition(null)
    setError(null)
  }

  const supported = typeof navigator !== 'undefined' && 'geolocation' in navigator

  useEffect(() => {
    if (!enabled || !supported) return
    let active = true
    const id = navigator.geolocation.watchPosition(
      (pos) => {
        if (!active) return
        setPosition({ lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy: pos.coords.accuracy })
        setError(null)
      },
      (err) => { if (active) setError(err.message) },
      { enableHighAccuracy: true, maximumAge: 10_000, timeout: 20_000 },
    )
    return () => {
      active = false
      navigator.geolocation.clearWatch(id)
    }
  }, [enabled, supported])

  return {
    position: enabled ? position : null,
    error: !enabled ? null : supported ? error : 'Geolocation is not supported by this browser',
  }
}

export function getCurrentPosition(): Promise<GeoPosition> {
  return new Promise((resolve, reject) => {
    if (!('geolocation' in navigator)) {
      reject(new Error('Geolocation is not supported by this browser'))
      return
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy: pos.coords.accuracy }),
      (err) => reject(new Error(err.message)),
      { enableHighAccuracy: true, timeout: 15_000 },
    )
  })
}
