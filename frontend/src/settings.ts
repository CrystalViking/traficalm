import { createContext, useContext } from 'react'
import type { Language, Message } from './translations'
import { translate, translateExternal } from './translations'
import { isCity } from './cities'
import type { City } from './cities'

export interface AppSettings {
  city: City
  gps: boolean
  language: Language
  darkMode: boolean
  walkRadiusM: number
  timeSlider: boolean
  strictWalking: boolean
}

export const DEFAULT_CROWD_TOLERANCE = 60
export const DEFAULT_SETTINGS: AppSettings = { city: 'gdansk', gps: false, language: 'en', darkMode: false, walkRadiusM: 500, timeSlider: false, strictWalking: false }
export const SETTINGS_KEY = 'traficalm-settings'
export const SettingsContext = createContext<AppSettings>(DEFAULT_SETTINGS)

export function readSettings(): { value: AppSettings; error: string | null } {
  try {
    const saved = localStorage.getItem(SETTINGS_KEY)
    if (saved === null) return { value: DEFAULT_SETTINGS, error: null }
    const value: unknown = JSON.parse(saved)
    if (typeof value === 'object' && value !== null && 'language' in value && 'darkMode' in value && 'walkRadiusM' in value
      && (value.language === 'en' || value.language === 'pl') && typeof value.darkMode === 'boolean'
      && (!('timeSlider' in value) || typeof value.timeSlider === 'boolean')
      && (!('city' in value) || isCity(value.city)) && (!('gps' in value) || typeof value.gps === 'boolean')
      && typeof value.walkRadiusM === 'number' && Number.isInteger(value.walkRadiusM) && value.walkRadiusM >= 100 && value.walkRadiusM <= 20000) {
      return { value: {
        city: 'city' in value && isCity(value.city) ? value.city : 'gdansk',
        gps: 'gps' in value ? value.gps === true : false,
        language: value.language, darkMode: value.darkMode, walkRadiusM: value.walkRadiusM,
        timeSlider: 'timeSlider' in value ? value.timeSlider === true : false,
        strictWalking: 'strictWalking' in value ? value.strictWalking === true : false,
      }, error: null }
    }
    return { value: DEFAULT_SETTINGS, error: 'Saved app settings are invalid; using defaults.' }
  } catch (err) {
    return { value: DEFAULT_SETTINGS, error: `Could not read app settings: ${err instanceof Error ? err.message : String(err)}` }
  }
}

export function useSettings() {
  const settings = useContext(SettingsContext)
  return {
    ...settings,
    t: (message: Message, parameters?: Record<string, string | number>) => translate(settings.language, message, parameters),
    external: (message: string) => translateExternal(settings.language, message),
  }
}
