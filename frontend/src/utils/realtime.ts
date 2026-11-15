export function realtimeKey(tripId: string, serviceDate: string | null): string {
  return `${tripId}:${serviceDate ?? ''}`
}
