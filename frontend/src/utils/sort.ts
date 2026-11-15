import type { Itinerary } from '../types'

/** Options sorted by crowd index, lowest first (ties: earlier arrival). */
export function sortByCrowd(itineraries: Itinerary[]): Itinerary[] {
  return [...itineraries].sort(
    (a, b) => a.crowd_index - b.crowd_index || new Date(a.arrival).getTime() - new Date(b.arrival).getTime(),
  )
}
