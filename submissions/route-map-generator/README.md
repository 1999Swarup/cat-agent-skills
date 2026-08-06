# Route Map Generator

A generic **map + route** skill for Copilot Studio.

- **Maps** — plot locations with optional values and icons (weather, sites, stores)
- **Routes** — optimise multi-stop visit order and draw a path  

The Python toolkit runs **fully offline** in the sandbox (no external API calls).
Coordinates are expected in the payload — from the user, from a **previous tool**
in the same conversation (e.g. Dataverse / CRM / list query), or via agent
**web search** when still missing.

## What you get

1. Markdown table + **PNG** map (always)  
2. Optional **Leaflet + OSM** HTML — tiles load in the browser when opened  
3. Optional **GeoJSON** / **KML**  
4. CSV of points  

## Two kinds

| Kind | Use when | Output |
| --- | --- | --- |
| `map` | “Show these cities”, weather, site list | Markers + values/icons, no path |
| `route` | “Optimise visit order”, deliveries | Ordered stops + estimated path |

## Point fields

| Field | Notes |
| --- | --- |
| `lat` / `lon` | **Always preferred** — from user, prior tools (Dataverse, etc.), or web search |
| `location` / `address` / `name` | Labels; may match bundled place_lookup |
| `value` | Label on map/HTML (e.g. `24 C`) |
| `icon` | `sunny`, `rain`, `office`, `pin`, … |
| `color` | Optional hex colour |

## Examples

**Weather map**

> Show Sydney, Bondi, Manly, and Parramatta on a map with today’s temperatures
> and weather icons. Give me HTML.

*(Agent uses existing lat/lon or web-searches missing ones, then calls the toolkit.)*

**After Dataverse / another tool**

> Get open service accounts from Dataverse, then map them and optimise a visit route.

*(Map latitude/longitude fields from the prior result into `points` / `stops`.)*

**Route**

> Optimise a driving round trip for these five inspection sites. Markdown + map
> image + interactive HTML.

## Dependencies

`matplotlib` for PNG. No outbound network from the script. Browser network is
only needed when a user opens the HTML file to load Leaflet/OSM tiles.
