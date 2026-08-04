---
name: route-map-optimizer
description: "Use this skill whenever the user asks to plan, optimise, or map a multi-stop route — field visits, deliveries, inspections, drop-offs — and wants a stop order, distance/time estimate, route map image, or exports (HTML, GeoJSON, KML). Trigger on phrases like 'optimise this route', 'best order to visit these addresses', 'draw a map of these stops', or when they paste a list of places and ask for a driving/walking path. Do NOT trigger for single-destination turn-by-turn navigation apps, or for non-geographic process 'routing'."
---

Convert a list of stops into an optimised visit order via the bundled
`scripts/route_map.py` toolkit. Geocode with OpenStreetMap Nominatim, build a
travel matrix and road geometry with OSRM, then return a markdown route table
inline with a PNG map. Offer interactive HTML, GeoJSON, and KML when asked.

## Instructions

1. **Intake.** Collect stops. Each stop needs either an `address` **or** both
   `lat` and `lon`. Optional per stop: `name`. If fewer than 2 stops are given,
   **stop and ask** — do not invent locations.

2. **Defaults**
   - `profile`: `driving` (also `walking`, `cycling`)
   - `round_trip`: `true` (return to the first stop)
   - `start`: first stop (index `0` or stop name)
   - Always produce PNG map + CSV of ordered stops + markdown summary
   - Produce HTML / GeoJSON / KML only when the user asks (or payload flags are set)

3. **Execute** with the toolkit (import or CLI). Always surface:
   - Optimised stop sequence
   - Total distance and estimated travel time
   - Markdown table **and** the map image inline in the reply
   - File paths for downloads

```python
import sys
sys.path.insert(0, "scripts")
from route_map import plan_route

result = plan_route({
    "stops": [
        {"name": "Office", "address": "1 Macquarie St, Sydney NSW"},
        {"name": "Client A", "address": "Bondi Beach, NSW"},
        {"name": "Client B", "lat": -33.8840, "lon": 151.2070},
    ],
    "round_trip": True,
    "profile": "driving",
    "title": "Tuesday field visits",
    "html": True,       # optional Leaflet + OSM interactive map
    "geojson": True,    # optional FeatureCollection
    "kml": True,        # optional Google Earth / GIS
    "out_prefix": "route",
})

# Paste into chat for the user:
print(result["markdown"])
```

4. **Reply layout.** Paste `result["markdown"]` so the user sees the numbered
   stop table and the map image together. Then list optional export paths
   (`html_path`, `geojson_path`, `kml_path`). Mention attribution once:
   (c) OpenStreetMap contributors | Routing via OSRM.

5. **Ambiguous geocoding.** If Nominatim fails or the address is too vague,
   ask for a clearer address or lat/lon. Never invent coordinates.

## Guardrails

- Do not invent stop locations or claim live traffic accuracy.
- Optimisation uses OSRM travel times with nearest-neighbour + 2-opt — good for
  typical field lists (up to ~15 stops); say so if the user asks about optimality.
- Respect Nominatim usage (the toolkit sleeps ~1s between address lookups).
- Prefer the bundled toolkit over ad-hoc map scripts for consistent exports.
- Keep chat replies to the markdown summary + paths; do not dump raw GeoJSON.

## Bundled files

- `scripts/route_map.py` — geocode, optimise, PNG, HTML, GeoJSON, KML, CSV
- `references/cheatsheet.md` — payload fields, CLI, test prompts
- `assets/sample_stops.json` — address-based Sydney demo
- `assets/sample_stops_coords.json` — lat/lon demo (no geocoding wait)
