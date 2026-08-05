# Route Map Generator

A generic **map + route** skill for Copilot Studio.

- **Maps** — plot locations with optional values and icons (weather, sites, stores)
- **Routes** — optimise multi-stop visit order and draw a path  

Works **offline** in restricted sandboxes (place lookup + haversine) and can use
live OpenStreetMap / OSRM for generating HTML.

## What you get

1. Markdown table + **PNG** map (always)  
2. Optional **Leaflet + OSM** HTML — pan/zoom, numbered or icon markers, legend,
   clickable list, route line when applicable  
3. Optional **GeoJSON** / **KML**  
4. CSV of points  

## Two modes

| Kind | Use when | Output |
| --- | --- | --- |
| `map` | “Show these cities”, weather, site list | Markers + values/icons, no path |
| `route` | “Optimise visit order”, deliveries | Ordered stops + route path |

## Point fields

| Field | Notes |
| --- | --- |
| `lat` / `lon` | **Always preferred** when provided |
| `location` / `address` / `name` | Geocoded or matched offline |
| `value` | Label on map/HTML (e.g. `24 C`) |
| `icon` | `sunny`, `rain`, `office`, `pin`, … |
| `color` | Optional hex colour |

## Examples

**Weather map**

> Show Sydney, Bondi, Manly, and Parramatta on a map with today’s temperatures
> and weather icons. Give me HTML.

**Route**

> Optimise a driving round trip for these five inspection sites. Markdown + map
> image + interactive HTML.

## Dependencies

`matplotlib` for PNG. Browser network needed to load Leaflet/OSM tiles in HTML.
No API key for public OSM/OSRM endpoints.
