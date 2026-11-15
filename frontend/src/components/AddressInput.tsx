import { useEffect, useId, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { reverseGeocode, searchAddress } from '../api'
import { useDebouncedValue } from '../hooks/useDebouncedValue'
import { getCurrentPosition } from '../hooks/useGeolocation'
import type { AddressSuggestion, Place } from '../types'
import type { City } from '../cities'
import { useSettings } from '../settings'
import { CITIES, isInCity } from '../cities'

interface Props {
  label: string
  placeholder?: string
  value: Place | null
  onChange: (place: Place | null) => void
}

export function AddressInput({ label, placeholder, value, onChange }: Props) {
  const { t, external, city, gps } = useSettings()
  const gpsRequest = useRef<AbortController | null>(null)
  const id = useId()
  const listId = `${id}-list`
  const [text, setText] = useState(value?.name ?? '')
  const [suggestions, setSuggestions] = useState<AddressSuggestion[]>([])
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [loadedQuery, setLoadedQuery] = useState('')
  const [loadedCity, setLoadedCity] = useState<City | null>(null)
  const [locating, setLocating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const query = useDebouncedValue(text.trim(), 400)
  const [previousGps, setPreviousGps] = useState(gps)
  if (gps !== previousGps) {
    setPreviousGps(gps)
    setLocating(false)
  }
  useEffect(() => {
    if (!gps) gpsRequest.current?.abort()
    return () => gpsRequest.current?.abort()
  }, [gps, city])

  const [prevValue, setPrevValue] = useState(value)
  // Sync the text when a place is chosen from outside (GPS, swapping A/B...)
  if (value !== prevValue) {
    setPrevValue(value)
    setText(value?.name ?? '')
  }

  const textQuery = text.trim()
  const shouldSearch = query.length >= 3 && query === textQuery && query !== value?.name
  const currentResult = shouldSearch && loadedQuery === query && loadedCity === city
  const loading = textQuery.length >= 3 && textQuery !== value?.name && !currentResult
  const visible = currentResult ? suggestions : []

  useEffect(() => {
    if (!shouldSearch) return
    const controller = new AbortController()
    searchAddress(query, controller.signal, city)
      .then((items) => {
        if (controller.signal.aborted) return
        setSuggestions(items)
        setActive(-1)
        setOpen(true)
        setError(null)
        setLoadedQuery(query)
        setLoadedCity(city)
      })
      .catch(() => {
        if (controller.signal.aborted) return
        setSuggestions([])
        setOpen(false)
        setError('Address suggestions are unavailable')
        setLoadedQuery(query)
        setLoadedCity(city)
      })
    return () => controller.abort()
  }, [query, shouldSearch, city])

  const select = (item: AddressSuggestion) => {
    onChange({ name: item.label, lat: item.lat, lon: item.lon })
    setText(item.label)
    setOpen(false)
    setSuggestions([])
  }

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!open || visible.length === 0) return
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActive((i) => (i + 1) % visible.length)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((i) => (i <= 0 ? visible.length - 1 : i - 1))
    } else if (event.key === 'Enter' && active >= 0) {
      event.preventDefault()
      select(visible[active])
    } else if (event.key === 'Escape') {
      setOpen(false)
    }
  }

  const useMyLocation = async () => {
    if (!gps) return
    gpsRequest.current?.abort()
    const controller = new AbortController()
    gpsRequest.current = controller
    setLocating(true)
    setError(null)
    try {
      const pos = await getCurrentPosition()
      if (controller.signal.aborted) return
      if (!isInCity(city, pos.lat, pos.lon)) {
        setError('Your GPS location is outside the selected city region. Change city in Settings or enter an address.')
        return
      }
      let name = t('My location')
      try {
        const place = await reverseGeocode(pos.lat, pos.lon, controller.signal, city)
        if (controller.signal.aborted) return
        if (place?.label) name = `${t('My location')} – ${place.label}`
      } catch (err) {
        if (controller.signal.aborted) return
        setError(`GPS coordinates selected, but the address lookup failed: ${err instanceof Error ? err.message : String(err)}`)
      }
      if (controller.signal.aborted) return
      onChange({ name, lat: pos.lat, lon: pos.lon })
    } catch (err) {
      if (controller.signal.aborted) return
      setError(`Cannot get GPS location: ${(err as Error).message}`)
    } finally {
      if (!controller.signal.aborted) setLocating(false)
    }
  }

  return (
    <div className="address-input">
      <label htmlFor={id}>{label}</label>
      <div className="address-row">
        <input
          id={id}
          type="text"
          role="combobox"
          aria-expanded={open && visible.length > 0}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={open && active >= 0 && active < visible.length ? `${listId}-${active}` : undefined}
          autoComplete="off"
          placeholder={placeholder}
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            if (value) {
              // typing clears the chosen place but keeps the typed text
              setPrevValue(null)
              onChange(null)
            }
          }}
          onFocus={() => visible.length > 0 && setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={onKeyDown}
        />
        <button type="button" className="gps-button" onClick={useMyLocation} disabled={locating || !gps}
          title={t(gps ? 'Use my GPS location' : 'Enable GPS in Settings')}>
          {locating ? '…' : '📍'}
          <span className="visually-hidden">{t('Use my GPS location for {label}', { label })}</span>
        </button>
      </div>
      {loading && <div className="hint">{t('Searching…')}</div>}
      {error && <div className="error" role="alert">{external(error)}</div>}
      {currentResult && !error && suggestions.length === 0 && <div className="hint" role="status">
        {t('No addresses found in {city}.', { city: CITIES[city].name })}
      </div>}
      {open && visible.length > 0 && (
        <ul id={listId} role="listbox" className="suggestions">
          {visible.map((item, i) => (
            <li
              key={`${item.lat},${item.lon},${i}`}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              className={i === active ? 'active' : undefined}
              onMouseDown={(e) => {
                e.preventDefault()
                select(item)
              }}
            >
              {item.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
