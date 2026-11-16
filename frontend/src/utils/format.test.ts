import { describe, expect, it } from 'vitest'
import { crowdLabel, crowdLevel, formatDelay, formatDistance, formatDuration, formatEuropeanDateTime, formatTime } from './format'
import { sortByCrowd } from './sort'
import { makeItinerary } from '../test/fixtures'

describe('format helpers', () => {
  it('formats times in Warsaw time', () => {
    expect(formatTime('2026-11-16T06:20:00Z')).toBe('07:20')
    expect(formatTime('2026-11-15T23:05:00Z')).toBe('00:05')
    expect(formatTime('2026-11-16T12:20:00Z', '12h')).toMatch(/0?1:20\s*pm/i)
    expect(formatTime(null)).toBe('')
  })

  it('formats European departure date and time', () => {
    expect(formatEuropeanDateTime('2026-11-16T07:20')).toBe('16/11/2026 07:20')
  })

  it('formats durations, distances and delays', () => {
    expect(formatDuration(1500)).toBe('25 min')
    expect(formatDuration(3900)).toBe('1 h 5 min')
    expect(formatDistance(420)).toBe('420 m')
    expect(formatDistance(2345)).toBe('2.3 km')
    expect(formatDelay(0)).toBe('on time')
    expect(formatDelay(180)).toBe('+3 min')
    expect(formatDelay(-60)).toBe('-1 min')
    expect(formatDelay(null)).toBe('')
  })

  it('maps crowd index to levels', () => {
    expect(crowdLevel(0)).toBe('low')
    expect(crowdLevel(30)).toBe('moderate')
    expect(crowdLevel(60)).toBe('high')
    expect(crowdLevel(100)).toBe('very-high')
    expect(crowdLabel(10)).toBe('Low crowd')
  })
})

describe('sortByCrowd', () => {
  it('sorts by crowd index ascending, then by arrival', () => {
    const items = [
      makeItinerary('a', 70),
      makeItinerary('b', 20, { arrival: '2026-11-16T08:00:00+01:00' }),
      makeItinerary('c', 20, { arrival: '2026-11-16T07:50:00+01:00' }),
      makeItinerary('d', 5),
    ]
    expect(sortByCrowd(items).map((i) => i.id)).toEqual(['d', 'c', 'b', 'a'])
    expect(items[0].id).toBe('a')
  })
})
