# Route Map Generator — Cheat Sheet

```python
from map_generator import generate
result = generate({ ... })
print(result["markdown"])
```

## Payload

| Field | Notes |
| --- | --- |
| `kind` | `map` \| `route` \| `auto` |
| `points` / `stops` / `locations` | list of place objects |
| `mode` | `offline` (sandbox) \| `auto` \| `online` |
| `html` / `geojson` / `kml` | optional exports |
| `round_trip` / `profile` / `optimize` | route options |

### Point object

| Field | Notes |
| --- | --- |
| `lat`, `lon` | **always win** when set |
| `name`, `location`, `address` | labels / geocoding |
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

# Route
python scripts/map_generator.py --payload assets/sample_stops.json --kind route --html
```
