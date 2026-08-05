# Route Map Optimizer — Cheat Sheet

Engine: `scripts/route_map.py`

```python
from route_map import plan_route
result = plan_route({"mode": "offline", "stops": [...], "html": True})
print(result["markdown"])
```

---

## Payload fields

| Field | Required | Notes |
| --- | --- | --- |
| `stops` | yes | `{name?, address?}` or `{name?, lat, lon}` |
| `mode` | no | `offline` (sandbox) \| `auto` (default) \| `online` |
| `profile` | no | `driving` \| `walking` \| `cycling` |
| `round_trip` | no | default `true` |
| `start` | no | index or stop name |
| `title` | no | map / HTML title |
| `html` | no | self-contained SVG with **numbered markers** |
| `geojson` / `kml` | no | GIS exports |
| `out_prefix` | no | default `route` |

### Resolving locations (offline-friendly)

1. **Customer `lat` + `lon` always win** (aliases: `latitude`/`longitude`/`lng`).
   Never overwritten by address lookup or Nominatim.  
2. Else match `name` / `address` against `assets/place_lookup.json`  
3. Else (auto/online) call Nominatim  

---

## CLI

```bash
# Sandbox-safe (no network)
python scripts/route_map.py \
  --payload assets/sample_stops.json --mode offline --html --geojson --kml

# Coords demo
python scripts/route_map.py \
  --payload assets/sample_stops_coords.json --mode offline --html

# Try live OSRM, fall back if SSL blocked
python scripts/route_map.py \
  --payload assets/sample_stops_coords.json --mode auto --html
```

---

## Test prompts

### Sandbox suburbs

> Optimise a round trip for Bondi, Manly, Coogee, Newtown, Parramatta. Offline.
> Markdown + PNG + HTML with numbered markers.

Expect: `routing_source=haversine_offline`, numbered HTML badges 1…N.

### Network failure (auto)

Expect toolkit fallback without asking the user to hardcode coords manually.
