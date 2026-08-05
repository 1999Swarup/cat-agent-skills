# Route Map Optimizer

Turn a list of places into an efficient visit order and a clear route map.

Built for **Copilot Studio sandboxes** where live OpenStreetMap calls often fail
(SSL/network limits): the skill runs **fully offline** with approximate place
centroids or your lat/lon, and still produces a markdown summary, PNG map, and
optional interactive HTML with **numbered stop markers**.

When the network is available, it can use live Nominatim geocoding and OSRM
road routing (`mode: auto` or `online`).

## Why this skill exists

Planning “which stop next?” by eye is slow. This skill:

1. Resolves each place (coords, lookup, or live geocode)  
2. Optimises visit order (nearest-neighbour + 2-opt)  
3. Estimates distance and travel time  
4. Shows a numbered stop table **with a map image inline**  
5. Optionally exports HTML / GeoJSON / KML  

## Sandbox behaviour (important)

| Mode | Behaviour |
| --- | --- |
| `offline` (recommended in Copilot Studio) | No network. Uses lat/lon or `assets/place_lookup.json`. Haversine × road factor. |
| `auto` | Try OSRM; on SSL/network failure, fall back offline automatically. |
| `online` | Require live OSRM (fails clearly if blocked). |

HTML uses **Leaflet + OpenStreetMap** tiles: pan/zoom, numbered markers,
route polyline, legend, and a clickable stop list that flies to each pin.
Needs a browser with network access for tiles (usual when sharing the file).

## Real use cases

- Field sales / account visits  
- Insurance property inspections  
- Home care daily rounds  
- Event or logistics drop-offs  
- Climate / field survey site loops  

## How to use it

> Optimise a driving round trip for Bondi, Manly, Newtown, Parramatta, and the
> CBD. Use offline mode. Show markdown + map, and give me HTML with numbered markers.

## What you get

1. **Markdown** — distance, time, numbered table, map image  
2. **PNG** — route path + numbered markers  
3. **CSV** — ordered stops  
4. Optional **HTML** — Leaflet/OSM interactive map with numbered markers + legend  
5. Optional **GeoJSON** / **KML**  

## Dependencies

`matplotlib` for PNG. Network only needed for `online` / successful `auto` live
routing. No API key for the public OSM/OSRM endpoints.

## Attribution

Live mode: map data (c) OpenStreetMap contributors; routing via OSRM.  
Offline mode: approximate centroids / haversine estimates — not turn-by-turn.
