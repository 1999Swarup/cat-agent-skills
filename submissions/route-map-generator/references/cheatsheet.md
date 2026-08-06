# Route Map Generator — Cheat Sheet

```python
from map_generator import generate
result = generate({ ... })
print(result["markdown"])
```

Sandbox: **no external APIs** from Python. Resolve lat/lon via user input,
**prior tool results** (Dataverse, CRM, lists, connectors), `place_lookup.json`,
or **agent web search**, then pass them in.

HTML routes: when opened in a browser, the page calls the public **OSRM** demo
API and draws a **road-following** polyline (real streets).

- **PNG** → straight lines between stops (approximate)
- **HTML** → actual road path (falls back to straight lines if OSRM is offline)

## Payload

| Field | Notes |
| --- | --- |
| `kind` | `map` \| `route` \| `auto` |
| `points` / `stops` / `locations` | list of place objects |
| `html` / `geojson` / `kml` | optional exports |
| `map_links` | `true` → Google / Apple / Bing deep route URLs (+ JSON file) |
| `google_maps` / `apple_maps` / `bing_maps` | emit only selected provider link(s) |
| `qr_codes` | `true` → QR code PNGs per provider + combined sheet (requires `map_links: true`) |
| `round_trip` / `profile` / `optimize` | route options |

### Point object

| Field | Notes |
| --- | --- |
| `lat`, `lon` | **always win** when set (prefer these) |
| `name`, `location`, `address` | labels; optional place_lookup match |
| `value`, `value_num` | optional metrics |
| `icon`, `color` | marker style |

### Icons

`pin`, `sunny`, `partly-cloudy`, `cloudy`, `rain`, `storm`, `snow`, `fog`,
`wind`, `hot`, `cold`, `office`, `home`, `factory`, `hospital`, `school`,
`warning`, `check`, `star`, `shop`, `truck`

## CLI

```bash
# Weather / marker map
python scripts/map_generator.py --payload assets/sample_weather_map.json --html

# Route + deep links
python scripts/map_generator.py --payload assets/sample_stops.json --kind route --html --map-links
```

### Map deep links + QR codes

```python
result = generate({
    "kind": "route",
    "stops": [...],   # with lat/lon
    "round_trip": True,
    "map_links": True,   # or google_maps / apple_maps / bing_maps
    "qr_codes": True,    # QR PNG per provider + combined sheet
})
print(result["google_maps_url"])
print(result["apple_maps_url"])
print(result["bing_maps_url"])
print(result["qr_sheet_path"])    # combined PNG — embed with ![QR codes](path)
# result["qr_paths"] has individual paths: google_maps, apple_maps, bing_maps
```

**Dependency:** `pip install qrcode[pil]`

```bash
# Route + deep links + QR codes (CLI)
python scripts/map_generator.py --payload assets/sample_stops.json --kind route --html --map-links --qr-codes
```
