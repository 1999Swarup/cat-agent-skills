---
name: route-map-generator
description: "Use this skill whenever the user asks to plot places on a map, show markers with values or icons (including weather by city), optimise a multi-stop route, or export a map as PNG/HTML/GeoJSON/KML — including as a later step after other tools (Dataverse, lists, CRM, APIs) have already returned locations or lat/lon. Trigger on phrases like 'show these cities on a map', 'map these records', 'weather map for', 'optimise this route', 'best order to visit', or when they paste locations/coordinates and want a visual map. Do NOT trigger for non-geographic process routing or single-destination turn-by-turn navigation apps."
---

Generate **marker maps** and/or **optimised routes** via `scripts/map_generator.py`.

The Copilot Studio **sandbox cannot call external APIs** from Python (no
Nominatim/OSRM). The script is fully offline. Coordinates must already be in
the payload when you call it.

This skill is often used **as one step in a wider flow**: another tool finds
records or places first (e.g. Dataverse accounts with latitude/longitude,
SharePoint lists, CRM, connectors, prior agent results), then you map or route
those rows here. Prefer lat/lon from those prior results — do not re-geocode
them.

Always return markdown **inline with a PNG**. Optional Leaflet + OSM HTML loads
map tiles in the **user's browser** (not from the Python sandbox).

## Instructions

1. **Choose kind**
   - `kind: "map"` — plot locations only (no routing)
   - `kind: "route"` — optimise visit order and draw a path
   - `kind: "auto"` — map unless `round_trip` / `optimize` implies a route

2. **Resolve coordinates (critical)**  
   For each point, in order:
   1. **`lat` / `lon` already available** — from the user, **or from a previous
      tool call** (Dataverse, Dynamics, Excel/list rows, connectors, earlier
      agent steps). Map common field names into `lat`/`lon` (e.g.
      `latitude`/`longitude`, `address1_latitude`/`address1_longitude`).
      These **always win** and must not be overwritten.
   2. Else try a name that matches `assets/place_lookup.json` (Sydney-area aliases)
   3. Else **web-search** (or other knowledge sources / websites) for the
      place's latitude and longitude, then include them in the payload

   Do **not** call external geocoding APIs from the Python script — they will fail
   in the sandbox. Do **not** invent coordinates. If prior tools and web search
   are inconclusive, ask the user for lat/lon.

3. **Point fields** (`points`, `stops`, or `locations`)
   - `name`, `location` / `address`
   - `lat`, `lon` (required unless place_lookup matches)
   - `value` — e.g. `"24 C"`, `"$1.2M"`, or a field from the prior tool
   - `icon` — see list below
   - `color` — optional hex

   Map mode needs ≥1 point. Route mode needs ≥2.

4. **Execute**

```python
import sys
sys.path.insert(0, "scripts")
from map_generator import generate

# Coords may come from Dataverse / prior tools / web search — already filled in:
result = generate({
    "kind": "map",
    "title": "Accounts by site",
    "points": [
        {"name": "Sydney HQ", "lat": -33.8688, "lon": 151.2093, "value": "24 C", "icon": "sunny"},
        {"name": "Manly depot", "lat": -33.7969, "lon": 151.2870, "value": "21 C", "icon": "cloudy"},
    ],
    "html": True,
})

result = generate({
    "kind": "route",
    "stops": [
        {"name": "Office", "lat": -33.8635, "lon": 151.2125},
        {"name": "Bondi", "lat": -33.8915, "lon": 151.2767},
    ],
    "round_trip": True,
    "html": True,
})

print(result["markdown"])
```

5. **Reply.** Paste `result["markdown"]`. Note that route distances/times are
   offline estimates (haversine × road factor), not live traffic routing.

## Icons

`pin`, `sunny`, `partly-cloudy`, `cloudy`, `rain`, `storm`, `snow`, `fog`,
`wind`, `hot`, `cold`, `office`, `home`, `factory`, `hospital`, `school`,
`warning`, `check`, `star`, `shop`, `truck`

## Guardrails

- Never overwrite lat/lon from the user or from prior tools (Dataverse, etc.).
- Never expect the script to reach the public internet.
- Prefer prior-tool or web-searched lat/lon over guessing.
- Prefer the toolkit over ad-hoc map scripts.

## Bundled files

- `scripts/map_generator.py` — offline engine (`generate`)
- `assets/place_lookup.json` — optional Sydney-area centroids
- `assets/sample_weather_map.json` — marker/weather demo
- `assets/sample_stops.json` / `sample_stops_coords.json` — route demos
- `references/cheatsheet.md`
