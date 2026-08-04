# Route Map Optimizer

Turn a list of places into an efficient visit order and a clear route map —
powered by **OpenStreetMap** (Nominatim geocoding + OSRM road routing).

Paste addresses or coordinates; get a numbered stop sequence, travel distance
and time, a shareable map image, and optional interactive or GIS exports.

## Why this skill exists

Planning “which stop next?” by eye is slow and often wrong. This skill:

1. Finds each place on the map  
2. Measures real road travel between them  
3. Reorders stops to reduce travel time  
4. Shows the result as markdown + a map image you can drop into chat or email  

## Real use cases

- **Field sales / account visits** — best order for a day’s client calls  
- **Insurance inspections** — route assessors across several properties  
- **Home care / community services** — daily visit sequencing  
- **Event or logistics drop-offs** — pickup and delivery order  
- **Climate / field surveys** — efficient sampling-site loops  

## How to use it

> Optimise a driving round trip for these stops, starting at the office. Show
> the route in markdown with a map image, and give me interactive HTML plus
> GeoJSON.

Or pass structured JSON (see `assets/sample_stops.json`).

## What you get

1. **Markdown summary** — distance, time, numbered stop table, and the map
   image referenced inline  
2. **PNG route map** — road path + numbered markers  
3. **CSV** of the ordered stops  
4. Optional **interactive HTML** — Leaflet map on OSM tiles, pan/zoom, stop popups  
5. Optional **GeoJSON** — route line + stop points for GIS tools  
6. Optional **KML** — open in Google Earth / many map apps  

## Profiles

| Profile | When to use |
| --- | --- |
| `driving` (default) | Cars / vans |
| `walking` | On-foot visits in a small area |
| `cycling` | Bike couriers / campus loops |

## Limits (v1)

- Best for **about 2–15 stops**  
- Uses public OSM/OSRM services (needs network); not live traffic  
- Optimisation is nearest-neighbour + 2-opt (strong practical result, not a
  guaranteed global optimum for huge fleets)  
- Address geocoding is rate-limited (~1 request/second)  

## Dependencies

`matplotlib` for the PNG. Network access to Nominatim and OSRM. No API key
required for the public endpoints used in v1.

## Attribution

Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors.  
Routing © [OSRM](http://project-osrm.org/) / OpenStreetMap.
