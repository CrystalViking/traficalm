export const CITIES = {
  gdansk: { name: 'Gdańsk', centre: [54.352, 18.646], bounds: [54.25, 18.30, 54.65, 18.95] },
  poznan: { name: 'Poznań', centre: [52.4064, 16.9252], bounds: [52.15, 16.55, 52.65, 17.25] },
  wroclaw: { name: 'Wrocław', centre: [51.1079, 17.0385], bounds: [50.90, 16.75, 51.30, 17.35] },
  warsaw: { name: 'Warszawa', centre: [52.2297, 21.0122], bounds: [51.95, 20.65, 52.55, 21.45] },
} satisfies Record<string, { name: string; centre: [number, number]; bounds: [number, number, number, number] }>

export type City = keyof typeof CITIES

export function isCity(value: unknown): value is City {
  return value === 'gdansk' || value === 'poznan' || value === 'wroclaw' || value === 'warsaw'
}

export function isInCity(city: City, lat: number, lon: number): boolean {
  const [south, west, north, east] = CITIES[city].bounds
  return south <= lat && lat <= north && west <= lon && lon <= east
}
