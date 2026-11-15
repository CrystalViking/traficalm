export function formatTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  return date.toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Warsaw' })
}

export function formatDuration(seconds: number): string {
  const minutes = Math.max(0, Math.round(seconds / 60))
  if (minutes < 60) return `${minutes} min`
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`
}

export function formatDistance(meters: number): string {
  return meters < 1000 ? `${Math.round(meters)} m` : `${(meters / 1000).toFixed(1)} km`
}

export function formatDelay(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return ''
  const minutes = Math.round(seconds / 60)
  if (minutes === 0) return 'on time'
  return minutes > 0 ? `+${minutes} min` : `${minutes} min`
}

export type CrowdLevel = 'low' | 'moderate' | 'high' | 'very-high'

export function crowdLevel(index: number): CrowdLevel {
  if (index < 25) return 'low'
  if (index < 50) return 'moderate'
  if (index < 75) return 'high'
  return 'very-high'
}

const LEVEL_COLORS: Record<CrowdLevel, string> = {
  low: '#2e7d32',
  moderate: '#f9a825',
  high: '#ef6c00',
  'very-high': '#c62828',
}

export function crowdColor(index: number): string {
  return LEVEL_COLORS[crowdLevel(index)]
}

export function crowdLabel(index: number): string {
  return { low: 'Low crowd', moderate: 'Moderate crowd', high: 'High crowd', 'very-high': 'Very high crowd' }[crowdLevel(index)]
}

/** Value for <input type="datetime-local"> in the user's local time. */
export function toLocalInputValue(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}
