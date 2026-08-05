---
name: route-map-optimizer
description: "Use this skill whenever the user asks to plan, optimise, or map a multi-stop route — field visits, deliveries, inspections, drop-offs — and wants a stop order, distance/time estimate, route map image, or exports (HTML, GeoJSON, KML). Trigger on phrases like 'optimise this route', 'best order to visit these addresses', 'draw a map of these stops', or when they paste a list of places and ask for a driving/walking path. Do NOT trigger for single-destination turn-by-turn navigation apps, or for non-geographic process 'routing'."
---

Convert a list of stops into an optimised visit order via `scripts/route_map.py`.
Works **offline in restricted sandboxes** (no live OSM/OSRM required) and can
use live Nominatim + OSRM when the network allows.

Always return a markdown stop table **inline with a PNG map**. Optional
self-contained HTML shows **numbered route markers** (no CDN/tiles). Optional
GeoJSON / KML on request.

## Instructions

1. **Intake.** Collect at least 2 stops. Prefer one of:
   - `lat` + `lon` (best — no geocoding)
   - Suburb/landmark `name` or `address` that matches `assets/place_lookup.json`
   - Full address (live Nominatim only when online)

   If fewer than 2 stops: **stop and ask**. Do not invent coordinates outside
   the bundled place lookup without telling the user they are approximate.

2. **Sandbox / Copilot Studio default.** Set `"mode": "offline"` unless the
   user explicitly wants live road routing. Offline uses:
   - bundled place centroids or supplied lat/lon
   - haversine distance × road factor
   - nearest-neighbour + 2-opt
   - straight-line path on PNG/HTML (clearly labelled as an estimate)

   In `auto` mode the toolkit tries OSRM first and **falls back offline** on
   SSL/network failure — do not ask the user A/B questions about hardcoding
   coords; call the toolkit and report `routing_source` from the result.

3. **Defaults**
   - `mode`: `offline` in sandboxes; otherwise `auto`
   - `profile`: `driving` (`walking`, `cycling` also supported)
   - `round_trip`: `true`
   - Always: PNG + CSV + markdown
   - Optional: `html`, `geojson`, `kml`

4. **Execute**

```python
import sys
sys.path.insert(0, "scripts")
from route_map import plan_route

result = plan_route({
    "mode": "offline",          # use in Copilot Studio / SSL-restricted hosts
    "stops": [
        {"name": "Office", "address": "1 Macquarie St, Sydney"},
        {"name": "Bondi", "address": "Bondi Beach"},
        {"name": "Manly", "address": "Manly"},
        {"name": "Newtown", "address": "Newtown"},
    ],
    "round_trip": True,
    "profile": "driving",
    "title": "Tuesday field visits",
    "html": True,
    "geojson": True,
    "out_prefix": "route",
})

print(result["markdown"])   # paste into chat (table + map image)
```

5. **Reply layout.** Paste `result["markdown"]` so the numbered table and map
   appear together. State clearly if `routing_source` is `haversine_offline`
   (approximate). List export paths. HTML markers are numbered 1…N to match
   the table.

## Guardrails

- Do not claim live traffic or turn-by-turn accuracy in offline mode.
- Prefer toolkit `mode: offline` over hand-rolled coordinate scripts.
- Only use place-lookup centroids for known aliases; for unknown places ask
  for lat/lon (or switch to online if available).
- Keep chat to markdown + paths; do not dump raw GeoJSON.

## Bundled files

- `scripts/route_map.py` — offline/online planner, PNG, HTML, GeoJSON, KML
- `assets/place_lookup.json` — approximate Sydney-area centroids for offline use
- `assets/sample_stops.json` / `sample_stops_coords.json` — demos
- `references/cheatsheet.md` — payload fields and CLI
