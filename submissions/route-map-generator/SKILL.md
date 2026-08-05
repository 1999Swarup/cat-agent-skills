---
name: route-map-generator
description: "Use this skill whenever the user asks to plot places on a map, show markers with values or icons (including weather by city), optimise a multi-stop route, or export a map as PNG/HTML/GeoJSON/KML. Trigger on phrases like 'show these cities on a map', 'weather map for', 'optimise this route', 'best order to visit', or when they paste locations/coordinates and want a visual map. Do NOT trigger for non-geographic process routing or single-destination turn-by-turn navigation apps."
---

Generate **marker maps** and/or **optimised routes** via `scripts/map_generator.py`.
Works offline in Copilot Studio sandboxes (`mode: offline`) and can use live
OSM/OSRM when the network allows.

Always return markdown **inline with a PNG**. Optional Leaflet + OSM HTML shows
numbered or icon markers, values, legend, and (for routes) a path.

## Instructions

1. **Choose kind**
   - `kind: "map"` — plot locations only (no routing). Use for weather maps,
     store locations, site lists, choropleth-style value labels.
   - `kind: "route"` — optimise visit order and draw a path.
   - `kind: "auto"` — map unless `round_trip`/`optimize` implies a route.

2. **Points.** Accept `points`, `stops`, or `locations`. Each item may include:
   - **Customer `lat`/`lon` always win** (also `latitude`/`longitude`/`lng`)
   - `name`, `location` or `address` (place lookup / Nominatim)
   - `value` — display string (e.g. `"24 C"`, `"$1.2M"`, `"High"`)
   - `value_num` — optional numeric metric
   - `icon` — see icon list below (default `pin`)
   - `color` — optional hex override

   Map mode needs ≥1 point. Route mode needs ≥2.

3. **Sandbox.** Prefer `"mode": "offline"` in Copilot Studio. Use bundled
   `assets/place_lookup.json` or lat/lon. Do not invent coordinates outside
   the lookup without saying they are approximate.

4. **Execute**

```python
import sys
sys.path.insert(0, "scripts")
from map_generator import generate

# Marker / weather map
result = generate({
    "kind": "map",
    "mode": "offline",
    "title": "NSW weather",
    "points": [
        {"name": "Sydney", "location": "Sydney", "value": "24 C", "icon": "sunny"},
        {"name": "Manly", "lat": -33.7969, "lon": 151.2870, "value": "21 C", "icon": "cloudy"},
    ],
    "html": True,
})

# Optimised route
result = generate({
    "kind": "route",
    "mode": "offline",
    "stops": [
        {"name": "Office", "address": "1 Macquarie St, Sydney"},
        {"name": "Bondi", "address": "Bondi Beach"},
    ],
    "round_trip": True,
    "html": True,
})

print(result["markdown"])
```

5. **Reply.** Paste `result["markdown"]` (table + image). Mention exports.
   For HTML, markers show numbers (routes) or weather/icons (maps).

## Icons

`pin`, `sunny`, `partly-cloudy`, `cloudy`, `rain`, `storm`, `snow`, `fog`,
`wind`, `hot`, `cold`, `office`, `home`, `factory`, `hospital`, `school`,
`warning`, `check`, `star`, `shop`, `truck`

## Guardrails

- Lat/lon from the user are never overwritten by geocoding.
- Offline routes are estimates — say so when `routing_source` is not `osrm`.
- Prefer the toolkit over ad-hoc map scripts.

## Bundled files

- `scripts/map_generator.py` — engine (`generate` / `plan_route` alias)
- `assets/place_lookup.json` — offline place centroids
- `assets/sample_weather_map.json` — marker/weather demo
- `assets/sample_stops.json` / `sample_stops_coords.json` — route demos
- `references/cheatsheet.md`
