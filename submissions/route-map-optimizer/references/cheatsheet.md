# Route Map Optimizer — Cheat Sheet

Engine: `scripts/route_map.py`

```python
from route_map import plan_route
result = plan_route({ ... })
print(result["markdown"])   # paste into chat with the map image
```

---

## Payload fields

| Field | Required | Notes |
| --- | --- | --- |
| `stops` | yes | List of `{name?, address?}` or `{name?, lat, lon}` |
| `profile` | no | `driving` (default) \| `walking` \| `cycling` |
| `round_trip` | no | default `true` — return to first stop |
| `start` | no | index or stop `name` (default `0`) |
| `title` | no | map / HTML title |
| `html` | no | `true` → Leaflet interactive map |
| `geojson` | no | `true` → FeatureCollection export |
| `kml` | no | `true` → KML export |
| `out_prefix` | no | default `route` |

### Stop object

| Keys | Notes |
| --- | --- |
| `name` | optional label |
| `address` | geocoded via Nominatim if lat/lon missing |
| `lat`, `lon` | skip geocoding when both provided |

---

## Return payload (key fields)

```json
{
  "distance_m": 0,
  "distance_label": "12.4 km",
  "duration_s": 0,
  "duration_label": "28 min",
  "stop_order": ["Office", "A", "B"],
  "chart_path": ".../route_map.png",
  "csv_path": ".../route_stops.csv",
  "markdown_path": ".../route_summary.md",
  "markdown": "### Optimised route\n...",
  "html_path": ".../route_map.html",
  "geojson_path": ".../route.geojson",
  "kml_path": ".../route.kml"
}
```

Always paste `markdown` into the user reply so the table and map appear together.

---

## CLI examples

```bash
# Coords demo (fast — no geocoding delay)
python scripts/route_map.py \
  --payload assets/sample_stops_coords.json \
  --html --geojson --kml

# Address demo (Nominatim; ~1s per address)
python scripts/route_map.py \
  --payload assets/sample_stops.json \
  --html --geojson --kml

# One-way walking route
python scripts/route_map.py \
  --payload assets/sample_stops_coords.json \
  --profile walking --one-way --html
```

---

## Test prompts

### 1. Field visits (addresses)

> Optimise a driving round trip for: Office at 1 Macquarie St Sydney, then
> Circular Quay, Bondi Beach, Newtown, and Pyrmont. Show markdown + map image,
> and export HTML and GeoJSON.

Expect: geocoding, reordered stops, PNG + markdown table, HTML + GeoJSON paths.

### 2. Coords only

> Plan a route for these lat/lon points… give me KML for Google Earth.

Expect: no Nominatim calls; KML written.

### 3. Disambiguation

> Draw a map for our delivery run tomorrow.

Expect: ask for stop list / addresses — **no script run**.
