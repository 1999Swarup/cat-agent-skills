#!/usr/bin/env python3
"""Optimize a multi-stop route with OpenStreetMap (Nominatim + OSRM).

Geocodes addresses, builds a driving/walking/cycling distance matrix,
optimises visit order (nearest-neighbour + 2-opt), draws a PNG map,
and optionally writes interactive HTML, GeoJSON, and KML.

Usage (import)::

    from route_map import plan_route
    result = plan_route({
        "stops": [
            {"name": "Office", "address": "1 Macquarie St, Sydney NSW"},
            {"name": "Client A", "address": "Circular Quay, Sydney"},
            {"name": "Client B", "lat": -33.8915, "lon": 151.2767},
        ],
        "round_trip": True,
        "html": True,
        "geojson": True,
        "kml": True,
    })

CLI::

    python route_map.py --payload assets/sample_stops.json --html --geojson --kml
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Mapping, Optional, Sequence

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "matplotlib is required for map PNG output. "
        "Install with: pip install matplotlib"
    ) from exc

USER_AGENT = "cat-agent-skills-route-map-optimizer/1.0 (github.com/microsoft/cat-agent-skills)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_BASE = "https://router.project-osrm.org"
PROFILES = ("driving", "walking", "cycling")


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _http_get(url: str, params: Optional[dict[str, Any]] = None, timeout: int = 30) -> Any:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HTTP {e.code} from {url.split('?')[0]}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error calling {url.split('?')[0]}: {e.reason}") from e


# ── Geocoding (Nominatim / OSM) ──────────────────────────────────────────────

def geocode(address: str, *, sleep: float = 1.1) -> tuple[float, float, str]:
    """Return (lat, lon, display_name). Respects Nominatim 1 req/s guidance."""
    data = _http_get(
        NOMINATIM_URL,
        {
            "q": address,
            "format": "json",
            "limit": 1,
            "addressdetails": 0,
        },
    )
    time.sleep(sleep)
    if not data:
        raise ValueError(f"Could not geocode address: {address!r}")
    hit = data[0]
    return float(hit["lat"]), float(hit["lon"]), str(hit.get("display_name") or address)


def resolve_stops(stops: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalise stops to name + lat/lon (+ address / display_name)."""
    if len(stops) < 2:
        raise ValueError("Need at least 2 stops to plan a route.")
    resolved: list[dict[str, Any]] = []
    for i, raw in enumerate(stops):
        name = str(raw.get("name") or f"Stop {i + 1}")
        if "lat" in raw and "lon" in raw:
            lat, lon = float(raw["lat"]), float(raw["lon"])
            display = str(raw.get("address") or raw.get("display_name") or name)
        elif raw.get("address"):
            lat, lon, display = geocode(str(raw["address"]))
        else:
            raise ValueError(
                f"Stop {i + 1} ({name!r}) needs `address` or both `lat` and `lon`."
            )
        resolved.append(
            {
                "name": name,
                "lat": lat,
                "lon": lon,
                "address": str(raw.get("address") or ""),
                "display_name": display,
            }
        )
    return resolved


# ── OSRM matrix + route ──────────────────────────────────────────────────────

def _coords_path(stops: Sequence[Mapping[str, Any]]) -> str:
    # OSRM wants lon,lat
    return ";".join(f"{s['lon']:.6f},{s['lat']:.6f}" for s in stops)


def osrm_table(
    stops: Sequence[Mapping[str, Any]],
    profile: str = "driving",
) -> tuple[list[list[float]], list[list[float]]]:
    """Return (durations_sec[n][n], distances_m[n][n])."""
    if profile not in PROFILES:
        raise ValueError(f"profile must be one of {PROFILES}")
    url = f"{OSRM_BASE}/table/v1/{profile}/{_coords_path(stops)}"
    data = _http_get(url, {"annotations": "duration,distance"})
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM table failed: {data.get('message') or data.get('code')}")
    durations = data["durations"]
    distances = data["distances"]
    # Replace None (unreachable) with large sentinel
    n = len(stops)
    for i in range(n):
        for j in range(n):
            if durations[i][j] is None:
                durations[i][j] = 1e12
            if distances[i][j] is None:
                distances[i][j] = 1e12
    return durations, distances


def osrm_route(
    ordered: Sequence[Mapping[str, Any]],
    profile: str = "driving",
) -> dict[str, Any]:
    """Full route geometry for an ordered stop list."""
    url = f"{OSRM_BASE}/route/v1/{profile}/{_coords_path(ordered)}"
    data = _http_get(
        url,
        {
            "overview": "full",
            "geometries": "geojson",
            "steps": "false",
        },
    )
    if data.get("code") != "Ok" or not data.get("routes"):
        raise RuntimeError(f"OSRM route failed: {data.get('message') or data.get('code')}")
    route = data["routes"][0]
    return {
        "distance_m": float(route["distance"]),
        "duration_s": float(route["duration"]),
        "geometry": route["geometry"],  # GeoJSON LineString
    }


# ── Optimisation (NN + 2-opt) ────────────────────────────────────────────────

def _tour_cost(order: list[int], matrix: list[list[float]], round_trip: bool) -> float:
    cost = 0.0
    for a, b in zip(order, order[1:]):
        cost += matrix[a][b]
    if round_trip:
        cost += matrix[order[-1]][order[0]]
    return cost


def _nearest_neighbour(matrix: list[list[float]], start: int, round_trip: bool) -> list[int]:
    n = len(matrix)
    unvisited = set(range(n)) - {start}
    order = [start]
    cur = start
    while unvisited:
        nxt = min(unvisited, key=lambda j: matrix[cur][j])
        unvisited.remove(nxt)
        order.append(nxt)
        cur = nxt
    return order


def _two_opt(order: list[int], matrix: list[list[float]], round_trip: bool) -> list[int]:
    """Improve a tour with 2-opt. Keeps order[0] fixed as start."""
    best = order[:]
    improved = True
    while improved:
        improved = False
        best_cost = _tour_cost(best, matrix, round_trip)
        # Do not reverse across the fixed start at index 0
        for i in range(1, len(best) - 1):
            for j in range(i + 1, len(best)):
                if j - i == 1:
                    continue
                candidate = best[:i] + best[i : j + 1][::-1] + best[j + 1 :]
                cost = _tour_cost(candidate, matrix, round_trip)
                if cost + 1e-9 < best_cost:
                    best = candidate
                    best_cost = cost
                    improved = True
                    break
            if improved:
                break
    return best


def optimise_order(
    matrix: list[list[float]],
    start: int = 0,
    round_trip: bool = True,
) -> list[int]:
    if start < 0 or start >= len(matrix):
        raise ValueError("`start` index out of range.")
    seed = _nearest_neighbour(matrix, start, round_trip)
    return _two_opt(seed, matrix, round_trip)


# ── Formatting helpers ───────────────────────────────────────────────────────

def _fmt_km(metres: float) -> str:
    if metres < 1000:
        return f"{metres:.0f} m"
    return f"{metres / 1000:.1f} km"


def _fmt_duration(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h} h {m} min"
    if m:
        return f"{m} min"
    return f"{sec} s"


def markdown_route(
    ordered_stops: Sequence[Mapping[str, Any]],
    *,
    distance_m: float,
    duration_s: float,
    round_trip: bool,
    profile: str,
    chart_path: str,
) -> str:
    """Markdown the agent can paste inline with the map image."""
    lines = [
        "### Optimised route",
        "",
        f"**Profile:** {profile} | **Distance:** {_fmt_km(distance_m)} | "
        f"**Est. time:** {_fmt_duration(duration_s)}"
        + (" | **Round trip**" if round_trip else ""),
        "",
        "| # | Stop | Location |",
        "| ---: | --- | --- |",
    ]
    for i, s in enumerate(ordered_stops, start=1):
        loc = s.get("display_name") or s.get("address") or f"{s['lat']:.5f}, {s['lon']:.5f}"
        lines.append(f"| {i} | {s['name']} | {loc} |")
    if round_trip and ordered_stops:
        lines.append(
            f"| {len(ordered_stops) + 1} | *(return)* | {ordered_stops[0]['name']} |"
        )
    lines += [
        "",
        f"![Route map]({chart_path})",
        "",
        "*Map data (c) OpenStreetMap contributors | Routing via OSRM*",
    ]
    return "\n".join(lines)


# ── PNG map ──────────────────────────────────────────────────────────────────

def save_png(
    ordered: Sequence[Mapping[str, Any]],
    geometry: Mapping[str, Any],
    *,
    out: str,
    title: str,
    round_trip: bool,
    distance_m: float,
    duration_s: float,
) -> str:
    coords = geometry.get("coordinates") or []
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]

    fig, ax = plt.subplots(figsize=(10, 8), dpi=140)
    ax.set_facecolor("#f4f7fb")
    fig.patch.set_facecolor("white")

    if xs and ys:
        ax.plot(xs, ys, color="#0078d4", linewidth=2.4, solid_capstyle="round", zorder=2, label="Route")

    # Stop markers
    for i, s in enumerate(ordered):
        colour = "#107c10" if i == 0 else ("#d83b01" if i == len(ordered) - 1 and not round_trip else "#0078d4")
        ax.scatter(s["lon"], s["lat"], s=120, c=colour, edgecolors="white", linewidths=1.5, zorder=4)
        ax.annotate(
            str(i + 1),
            (s["lon"], s["lat"]),
            textcoords="offset points",
            xytext=(0, 0),
            ha="center",
            va="center",
            fontsize=8,
            fontweight="bold",
            color="white",
            zorder=5,
        )
        ax.annotate(
            s["name"],
            (s["lon"], s["lat"]),
            textcoords="offset points",
            xytext=(8, 8),
            fontsize=8,
            color="#323130",
            zorder=5,
        )

    if round_trip and ordered and xs:
        # subtle note already in title/subtitle
        pass

    pad_x = max((max(xs) - min(xs)) * 0.12, 0.01) if xs else 0.02
    pad_y = max((max(ys) - min(ys)) * 0.12, 0.01) if ys else 0.02
    if xs:
        ax.set_xlim(min(xs) - pad_x, max(xs) + pad_x)
        ax.set_ylim(min(ys) - pad_y, max(ys) + pad_y)

    ax.set_title(title, fontsize=13, fontweight="bold", color="#201f1e", pad=12)
    ax.text(
        0.5,
        1.02,
        f"{_fmt_km(distance_m)} | {_fmt_duration(duration_s)}"
        + (" | round trip" if round_trip else ""),
        transform=ax.transAxes,
        ha="center",
        va="bottom",
        fontsize=9,
        color="#605e5c",
    )
    ax.set_xlabel("Longitude", fontsize=8, color="#605e5c")
    ax.set_ylabel("Latitude", fontsize=8, color="#605e5c")
    ax.tick_params(labelsize=7, colors="#605e5c")
    ax.grid(True, color="#e1dfdd", linewidth=0.6, zorder=0)
    for spine in ax.spines.values():
        spine.set_color("#d2d0ce")

    ax.text(
        0.01,
        0.01,
        "(c) OpenStreetMap | OSRM",
        transform=ax.transAxes,
        fontsize=7,
        color="#8a8886",
        ha="left",
        va="bottom",
    )

    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return out


# ── Interactive HTML (Leaflet + OSM tiles) ───────────────────────────────────

def save_html(
    ordered: Sequence[Mapping[str, Any]],
    geometry: Mapping[str, Any],
    *,
    out: str,
    title: str,
    round_trip: bool,
    distance_m: float,
    duration_s: float,
    profile: str,
) -> str:
    stops_js = json.dumps(
        [
            {
                "name": s["name"],
                "lat": s["lat"],
                "lon": s["lon"],
                "display": s.get("display_name") or s.get("address") or "",
            }
            for s in ordered
        ],
        ensure_ascii=False,
    )
    geom_js = json.dumps(geometry, ensure_ascii=False)
    title_esc = (
        title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
    subtitle = f"{_fmt_km(distance_m)} | {_fmt_duration(duration_s)} | {profile}"
    if round_trip:
        subtitle += " | round trip"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>{title_esc}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root {{
    --bg: #f5f7fa; --card: #fff; --text: #201f1e; --muted: #605e5c;
    --accent: #0078d4; --border: #e1dfdd;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; font-family: "Segoe UI", system-ui, sans-serif;
    background: var(--bg); color: var(--text);
  }}
  header {{
    padding: 16px 20px 8px; max-width: 1100px; margin: 0 auto;
  }}
  h1 {{ margin: 0 0 4px; font-size: 1.35rem; }}
  .sub {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 12px; }}
  .layout {{
    display: grid; grid-template-columns: 280px 1fr; gap: 12px;
    max-width: 1100px; margin: 0 auto; padding: 0 16px 20px;
  }}
  @media (max-width: 800px) {{ .layout {{ grid-template-columns: 1fr; }} }}
  .panel {{
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 12px 14px; max-height: 70vh; overflow: auto;
  }}
  .panel h2 {{ margin: 0 0 10px; font-size: 0.95rem; }}
  ol.stops {{ margin: 0; padding-left: 1.2rem; }}
  ol.stops li {{ margin: 0 0 8px; font-size: 0.88rem; line-height: 1.35; }}
  ol.stops .meta {{ color: var(--muted); font-size: 0.78rem; }}
  #map {{
    height: 70vh; min-height: 420px; border-radius: 10px;
    border: 1px solid var(--border); z-index: 0;
  }}
  .foot {{
    max-width: 1100px; margin: 0 auto; padding: 0 16px 24px;
    font-size: 0.75rem; color: var(--muted);
  }}
</style>
</head>
<body>
<header>
  <h1>{title_esc}</h1>
  <div class="sub">{subtitle}</div>
</header>
<div class="layout">
  <aside class="panel">
    <h2>Stop sequence</h2>
    <ol class="stops" id="stopList"></ol>
  </aside>
  <div id="map"></div>
</div>
<p class="foot">© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors ·
Routing via <a href="http://project-osrm.org/">OSRM</a> · Drag the map to explore</p>
<script>
const STOPS = {stops_js};
const GEOM = {geom_js};
const ROUND = {str(round_trip).lower()};

const list = document.getElementById('stopList');
STOPS.forEach((s, i) => {{
  const li = document.createElement('li');
  li.innerHTML = `<strong>${{s.name}}</strong><div class="meta">${{s.display || (s.lat.toFixed(5)+', '+s.lon.toFixed(5))}}</div>`;
  list.appendChild(li);
}});
if (ROUND && STOPS.length) {{
  const li = document.createElement('li');
  li.innerHTML = `<em>Return to ${{STOPS[0].name}}</em>`;
  list.appendChild(li);
}}

const map = L.map('map');
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap'
}}).addTo(map);

const latlngs = (GEOM.coordinates || []).map(c => [c[1], c[0]]);
const line = L.polyline(latlngs, {{ color: '#0078d4', weight: 5, opacity: 0.9 }}).addTo(map);

const bounds = [];
STOPS.forEach((s, i) => {{
  const colour = i === 0 ? '#107c10' : (i === STOPS.length - 1 && !ROUND ? '#d83b01' : '#0078d4');
  const m = L.circleMarker([s.lat, s.lon], {{
    radius: 10, color: '#fff', weight: 2, fillColor: colour, fillOpacity: 1
  }}).addTo(map);
  m.bindPopup(`<strong>${{i + 1}}. ${{s.name}}</strong><br/>${{s.display || ''}}`);
  const icon = L.divIcon({{
    className: '',
    html: `<div style="color:white;font:bold 11px Segoe UI,sans-serif;text-align:center;line-height:20px;width:20px;height:20px;border-radius:50%;background:${{colour}};border:2px solid white;box-shadow:0 1px 3px rgba(0,0,0,.35)">${{i + 1}}</div>`,
    iconSize: [20, 20], iconAnchor: [10, 10]
  }});
  L.marker([s.lat, s.lon], {{ icon }}).addTo(map)
    .bindPopup(`<strong>${{i + 1}}. ${{s.name}}</strong><br/>${{s.display || ''}}`);
  bounds.push([s.lat, s.lon]);
}});
if (latlngs.length) map.fitBounds(line.getBounds().pad(0.12));
else if (bounds.length) map.fitBounds(bounds, {{ padding: [40, 40] }});
</script>
</body>
</html>
"""
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out


# ── GeoJSON / KML ────────────────────────────────────────────────────────────

def save_geojson(
    ordered: Sequence[Mapping[str, Any]],
    geometry: Mapping[str, Any],
    *,
    out: str,
    title: str,
    round_trip: bool,
    distance_m: float,
    duration_s: float,
    profile: str,
) -> str:
    features: list[dict[str, Any]] = [
        {
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "name": title,
                "profile": profile,
                "distance_m": distance_m,
                "duration_s": duration_s,
                "round_trip": round_trip,
            },
        }
    ]
    for i, s in enumerate(ordered, start=1):
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [s["lon"], s["lat"]],
                },
                "properties": {
                    "name": s["name"],
                    "sequence": i,
                    "address": s.get("display_name") or s.get("address") or "",
                },
            }
        )
    doc = {
        "type": "FeatureCollection",
        "features": features,
    }
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
    return out


def save_kml(
    ordered: Sequence[Mapping[str, Any]],
    geometry: Mapping[str, Any],
    *,
    out: str,
    title: str,
    round_trip: bool,
    distance_m: float,
    duration_s: float,
    profile: str,
) -> str:
    kml_ns = "http://www.opengis.net/kml/2.2"
    ET.register_namespace("", kml_ns)
    kml = ET.Element(f"{{{kml_ns}}}kml")
    doc = ET.SubElement(kml, f"{{{kml_ns}}}Document")
    ET.SubElement(doc, f"{{{kml_ns}}}name").text = title
    ET.SubElement(doc, f"{{{kml_ns}}}description").text = (
        f"{profile} | {_fmt_km(distance_m)} | {_fmt_duration(duration_s)}"
        + (" | round trip" if round_trip else "")
    )

    # Route line
    route_pm = ET.SubElement(doc, f"{{{kml_ns}}}Placemark")
    ET.SubElement(route_pm, f"{{{kml_ns}}}name").text = "Route"
    line = ET.SubElement(route_pm, f"{{{kml_ns}}}LineString")
    ET.SubElement(line, f"{{{kml_ns}}}tessellate").text = "1"
    coords = geometry.get("coordinates") or []
    ET.SubElement(line, f"{{{kml_ns}}}coordinates").text = " ".join(
        f"{c[0]},{c[1]},0" for c in coords
    )

    for i, s in enumerate(ordered, start=1):
        pm = ET.SubElement(doc, f"{{{kml_ns}}}Placemark")
        ET.SubElement(pm, f"{{{kml_ns}}}name").text = f"{i}. {s['name']}"
        desc = s.get("display_name") or s.get("address") or ""
        if desc:
            ET.SubElement(pm, f"{{{kml_ns}}}description").text = desc
        point = ET.SubElement(pm, f"{{{kml_ns}}}Point")
        ET.SubElement(point, f"{{{kml_ns}}}coordinates").text = (
            f"{s['lon']},{s['lat']},0"
        )

    tree = ET.ElementTree(kml)
    tree.write(out, encoding="utf-8", xml_declaration=True)
    return out


def save_stops_csv(ordered: Sequence[Mapping[str, Any]], out: str) -> str:
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("sequence,name,lat,lon,address\n")
        for i, s in enumerate(ordered, start=1):
            addr = (s.get("display_name") or s.get("address") or "").replace('"', "'")
            fh.write(
                f'{i},"{s["name"]}",{s["lat"]:.6f},{s["lon"]:.6f},"{addr}"\n'
            )
    return out


# ── Orchestration ────────────────────────────────────────────────────────────

def _as_payload(source: Any) -> dict[str, Any]:
    if isinstance(source, Mapping):
        return dict(source)
    if isinstance(source, str):
        with open(source, encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        raise TypeError("payload must be a dict or path to a JSON file")
    if isinstance(data, list):
        raise TypeError("payload JSON must be an object, not an array")
    if not isinstance(data, dict):
        raise TypeError("payload must be a JSON object")
    return data


def plan_route(data: Mapping[str, Any] | str) -> dict[str, Any]:
    """Plan, optimise, and export a route. Returns summary paths + markdown."""
    payload = _as_payload(data)
    stops_in = payload.get("stops")
    if not isinstance(stops_in, list) or not stops_in:
        raise ValueError("`stops` must be a non-empty list of stop objects.")

    profile = str(payload.get("profile", "driving")).lower()
    if profile not in PROFILES:
        raise ValueError(f"`profile` must be one of {PROFILES}.")

    round_trip = bool(payload.get("round_trip", True))
    title = str(payload.get("title") or payload.get("chart_title") or "Optimised route")
    prefix = str(payload.get("out_prefix", "route"))

    resolved = resolve_stops(stops_in)

    start = payload.get("start", 0)
    if isinstance(start, str):
        names = [s["name"].lower() for s in resolved]
        if start.lower() not in names:
            raise ValueError(f"start stop {start!r} not found in stop names.")
        start_idx = names.index(start.lower())
    else:
        start_idx = int(start)

    durations, distances = osrm_table(resolved, profile=profile)
    # Optimise on duration (travel time); fall back to distance if needed
    order_idx = optimise_order(durations, start=start_idx, round_trip=round_trip)
    ordered = [resolved[i] for i in order_idx]

    # Build OSRM route for the ordered waypoints (+ return home if round trip)
    route_stops = ordered + ([ordered[0]] if round_trip and len(ordered) > 1 else [])
    route = osrm_route(route_stops, profile=profile)

    chart_path = str(payload.get("chart_path", f"{prefix}_map.png"))
    save_png(
        ordered,
        route["geometry"],
        out=chart_path,
        title=title,
        round_trip=round_trip,
        distance_m=route["distance_m"],
        duration_s=route["duration_s"],
    )

    csv_path = str(payload.get("csv_path", f"{prefix}_stops.csv"))
    save_stops_csv(ordered, csv_path)

    md = markdown_route(
        ordered,
        distance_m=route["distance_m"],
        duration_s=route["duration_s"],
        round_trip=round_trip,
        profile=profile,
        chart_path=os.path.abspath(chart_path),
    )
    md_path = str(payload.get("markdown_path", f"{prefix}_summary.md"))
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)

    result: dict[str, Any] = {
        "title": title,
        "profile": profile,
        "round_trip": round_trip,
        "distance_m": route["distance_m"],
        "distance_label": _fmt_km(route["distance_m"]),
        "duration_s": route["duration_s"],
        "duration_label": _fmt_duration(route["duration_s"]),
        "stop_order": [s["name"] for s in ordered],
        "stops": ordered,
        "chart_path": os.path.abspath(chart_path),
        "csv_path": os.path.abspath(csv_path),
        "markdown_path": os.path.abspath(md_path),
        "markdown": md,
        "method": "OSRM table + nearest-neighbour + 2-opt",
        "attribution": "(c) OpenStreetMap contributors | Routing via OSRM",
    }

    if bool(payload.get("html", False)):
        html_path = str(payload.get("html_path", f"{prefix}_map.html"))
        save_html(
            ordered,
            route["geometry"],
            out=html_path,
            title=title,
            round_trip=round_trip,
            distance_m=route["distance_m"],
            duration_s=route["duration_s"],
            profile=profile,
        )
        result["html_path"] = os.path.abspath(html_path)

    if bool(payload.get("geojson", False)):
        gj_path = str(payload.get("geojson_path", f"{prefix}.geojson"))
        save_geojson(
            ordered,
            route["geometry"],
            out=gj_path,
            title=title,
            round_trip=round_trip,
            distance_m=route["distance_m"],
            duration_s=route["duration_s"],
            profile=profile,
        )
        result["geojson_path"] = os.path.abspath(gj_path)

    if bool(payload.get("kml", False)):
        kml_path = str(payload.get("kml_path", f"{prefix}.kml"))
        save_kml(
            ordered,
            route["geometry"],
            out=kml_path,
            title=title,
            round_trip=round_trip,
            distance_m=route["distance_m"],
            duration_s=route["duration_s"],
            profile=profile,
        )
        result["kml_path"] = os.path.abspath(kml_path)

    return result


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Optimise a multi-stop route with OSM/OSRM and export map + data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--payload", required=True, help="Path to JSON payload with stops.")
    p.add_argument("--profile", choices=list(PROFILES), default=None)
    p.add_argument("--round-trip", dest="round_trip", action="store_true", default=None)
    p.add_argument("--one-way", dest="round_trip", action="store_false")
    p.add_argument("--title", default=None)
    p.add_argument("--out-prefix", default=None, dest="out_prefix")
    p.add_argument("--html", action="store_true")
    p.add_argument("--geojson", action="store_true")
    p.add_argument("--kml", action="store_true")
    p.add_argument("--json-out", action="store_true", help="Print full result JSON.")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    data = _as_payload(args.payload)
    if args.profile is not None:
        data["profile"] = args.profile
    if args.round_trip is not None:
        data["round_trip"] = args.round_trip
    if args.title is not None:
        data["title"] = args.title
    if args.out_prefix is not None:
        data["out_prefix"] = args.out_prefix
    if args.html:
        data["html"] = True
    if args.geojson:
        data["geojson"] = True
    if args.kml:
        data["kml"] = True

    result = plan_route(data)

    # Always print the markdown summary for chat paste.
    print(result["markdown"])
    print()
    print(f"PNG:      {result['chart_path']}")
    print(f"CSV:      {result['csv_path']}")
    if result.get("html_path"):
        print(f"HTML:     {result['html_path']}")
    if result.get("geojson_path"):
        print(f"GeoJSON:  {result['geojson_path']}")
    if result.get("kml_path"):
        print(f"KML:      {result['kml_path']}")

    if args.json_out:
        slim = {k: v for k, v in result.items() if k != "markdown"}
        print()
        print(json.dumps(slim, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
