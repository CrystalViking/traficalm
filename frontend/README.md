# TrafiCalm – frontend

React + TypeScript (Vite) single-page app with an OpenStreetMap map (Leaflet / react-leaflet).
See the [main README](../README.md) for the whole project.

```bash
npm install
npm run dev     # http://localhost:5173, proxies /api to VITE_API_PROXY (default http://localhost:8000)
npm test        # vitest + Testing Library
npm run lint    # oxlint
npm run build   # type-check and production build into dist/
```

* `src/components/AddressInput.tsx` – address field with real-time suggestions and GPS button
* `src/components/RouteOptions.tsx` – options sorted by crowd index (lowest first)
* `src/components/ItineraryDetails.tsx` – legs and stop list with scheduled and real-time times
* `src/components/RouteMap.tsx` – map with transit/walking legs, stops, vehicles and the user's position
