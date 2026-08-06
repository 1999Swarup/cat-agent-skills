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

### Map deep links

```python
result = generate({
    "kind": "route",
    "stops": [...],   # with lat/lon
    "round_trip": True,
    "map_links": True,   # or google_maps / apple_maps / bing_maps
})
print(result["google_maps_url"])
print(result["apple_maps_url"])
print(result["bing_maps_url"])
```
