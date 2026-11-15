import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'
import type { AppSettings } from '../settings'
import { useSettings } from '../settings'
import { CITIES, isCity } from '../cities'

interface Props {
  settings: AppSettings
  onChange: (settings: AppSettings) => void
  onClose: () => void
  crowdTolerance: number
  useLaya: boolean
  onToleranceChange: (value: number) => void
  onLayaChange: (value: boolean) => void
  planning: boolean
  errors: string[]
  cityStatus: ReactNode
}

export function SettingsMenu({ settings, onChange, onClose, crowdTolerance, useLaya, onToleranceChange, onLayaChange, planning, errors, cityStatus }: Props) {
  const { t, external } = useSettings()
  const [radius, setRadius] = useState(String(settings.walkRadiusM / 1000))
  const [radiusValid, setRadiusValid] = useState(true)
  const dialog = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const previous = document.activeElement
    dialog.current?.querySelector<HTMLSelectElement>('select')?.focus()
    return () => { if (previous instanceof HTMLElement) previous.focus() }
  }, [])
  function handleKey(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      event.preventDefault()
      onClose()
    } else if (event.key === 'Tab') {
      const inputs = dialog.current?.querySelectorAll<HTMLElement>('button, input, select')
      if (!inputs?.length) return
      const first = inputs[0], last = inputs[inputs.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
  }
  return (
    <div className="settings-backdrop" onClick={event => { if (event.target === event.currentTarget) onClose() }}>
      <div ref={dialog} className="settings-dialog" role="dialog" aria-modal="true" aria-label={t('Settings')} onKeyDown={handleKey}>
        <h2>{t('Settings')}</h2>
        <label>{t('City')}
          <select aria-label={t('City')} value={settings.city} onChange={event => {
            if (isCity(event.target.value)) onChange({ ...settings, city: event.target.value })
          }}>
            {Object.entries(CITIES).map(([value, city]) => <option key={value} value={value}>{city.name}</option>)}
          </select>
        </label>
        <p className="hint">{t('Changing city clears the selected addresses and route. Timetables download automatically on first selection.')}</p>
        {cityStatus}
        {settings.city === 'wroclaw' && <p className="hint">{t('Wrocław uses official UM Wrocław / MPK timetables. Its GPS feed cannot identify selected scheduled trips, so live tracking is unavailable.')}</p>}
        {settings.city === 'warsaw' && <p className="hint">{t('Warsaw uses a community GTFS conversion of ZTM Warszawa data, not an official GTFS API. Selected-trip live tracking is unavailable; timetable forecasts still work.')}</p>}
        <label>{t('Language')}
          <select aria-label={t('Language')} value={settings.language} onChange={event => {
            if (event.target.value === 'en' || event.target.value === 'pl') onChange({ ...settings, language: event.target.value })
          }}>
            <option value="en">{t('English')}</option>
            <option value="pl">{t('Polish')}</option>
          </select>
        </label>
        <label className="laya-setting">
          <input type="checkbox" checked={settings.gps} onChange={event => onChange({ ...settings, gps: event.target.checked })} />
          {t('Use GPS')}
        </label>
        <p className="hint">{t('Off by default. Enable to show your location and use GPS address buttons.')}</p>
        <label className="laya-setting">
          <input type="checkbox" checked={settings.darkMode} onChange={event => onChange({ ...settings, darkMode: event.target.checked })} />
          {t('Dark Mode')}
        </label>
        <label className="radius-setting">{t('Walking distance cap (km)')}
          <input type="number" min="0.1" max="20" step="0.1" required value={radius} aria-invalid={!radiusValid} onChange={event => {
            setRadius(event.target.value)
            const valid = event.target.validity.valid && Number.isFinite(event.target.valueAsNumber)
            setRadiusValid(valid)
            if (valid) {
              onChange({ ...settings, walkRadiusM: Math.round(event.target.valueAsNumber * 1000) })
            }
          }} />
        </label>
        <input type="range" aria-label={t('Walking distance cap (km)')} min="0.1" max="20" step="0.1"
          value={settings.walkRadiusM / 1000} onChange={event => {
            const value = event.target.valueAsNumber
            setRadius(String(value))
            setRadiusValid(true)
            onChange({ ...settings, walkRadiusM: Math.round(value * 1000) })
          }} />
        {!radiusValid && <p className="error" role="alert">{t('Enter a radius between 0.1 and 20 km, in steps of 0.1 km.')}</p>}
        <p className="hint">{t('Nearby-stop search radius, measured in a straight line. Walking can be longer. Applies to new searches.')}</p>
        <label htmlFor="crowd-tolerance">{t('Crowd tolerance: {value}%', { value: crowdTolerance })}</label>
        <input id="crowd-tolerance" type="range" min="0" max="100" step="1" value={crowdTolerance}
          disabled={planning} onChange={event => onToleranceChange(Number(event.target.value))} />
        <p className="hint">{t('Stops above this level are highlighted. Changing tolerance does not replace your selected route.')}</p>
        <label className="laya-setting">
          <input type="checkbox" checked={useLaya} disabled={planning} onChange={event => onLayaChange(event.target.checked)} />
          {t('Use Laya AI')}
        </label>
        <p className="hint">{t('Off by default. Applies to new plans and alternatives; Laya refines ride crowd scores, not live crowd counts or map areas.')}</p>
        <label className="laya-setting">
          <input type="checkbox" checked={settings.timeSlider} onChange={event => onChange({ ...settings, timeSlider: event.target.checked })} />
          {t('Show forecast time slider')}
        </label>
        {errors.map(error => <p key={error} className="error" role="alert">{external(error)}</p>)}
        <button type="button" className="primary" onClick={onClose}>{t('Close settings')}</button>
      </div>
    </div>
  )
}
