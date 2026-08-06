#!/usr/bin/env python3
"""Generic map + route generator — markers, values, icons, optional routing.

Runs fully offline in the Copilot Studio sandbox (no outbound HTTP).

Supports two kinds of output:

* ``map`` — plot locations with markers (optional values + weather/icons)
* ``route`` — optimise visit order and draw a path (haversine + road factor)

Location resolution (per point), in order:
1. ``lat``/``lon`` already in the payload (user, prior tools such as Dataverse,
   or agent web search) — always preferred
2. Bundled ``assets/place_lookup.json`` alias match

If neither works, the **agent** must obtain coordinates (prior tool or web
search) and pass ``lat``/``lon`` — the sandbox cannot call external geocoders.

Always produces PNG + markdown. Optional HTML uses Leaflet + OpenStreetMap
tiles in the user's browser (not fetched by Python). For routes, the HTML
also loads a road-following path from the public OSRM demo API (with an
offline straight-line fallback). Optional GeoJSON/KML.

Usage::

    from map_generator import generate

    # lat/lon often arrive from a previous Dataverse / CRM / list step
    generate({
        "kind": "map",
        "points": [
            {"name": "Sydney", "lat": -33.8688, "lon": 151.2093, "value": "24 C", "icon": "sunny"},
            {"name": "Manly", "lat": -33.7969, "lon": 151.2870, "value": "22 C", "icon": "cloudy"},
        ],
        "html": True,
    })
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import textwrap
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

PROFILES = ("driving", "walking", "cycling")
KINDS = ("auto", "map", "route")

# Approximate urban speeds (m/s) and road-factor vs straight-line distance.
_SPEED_MPS = {"driving": 35_000 / 3600, "walking": 5_000 / 3600, "cycling": 15_000 / 3600}
_ROAD_FACTOR = {"driving": 1.35, "walking": 1.15, "cycling": 1.25}

# Icon catalogue — emoji for HTML, short label + colour for PNG.
ICONS: dict[str, dict[str, str]] = {
    "pin": {"emoji": "📍", "label": "PIN", "color": "#0078d4"},
    "sunny": {"emoji": "☀️", "label": "SUN", "color": "#f9a825"},
    "partly-cloudy": {"emoji": "⛅", "label": "PC", "color": "#90a4ae"},
    "cloudy": {"emoji": "☁️", "label": "CLD", "color": "#78909c"},
    "rain": {"emoji": "🌧️", "label": "RAIN", "color": "#1565c0"},
    "storm": {"emoji": "⛈️", "label": "STM", "color": "#4527a0"},
    "snow": {"emoji": "❄️", "label": "SNOW", "color": "#4fc3f7"},
    "fog": {"emoji": "🌫️", "label": "FOG", "color": "#9e9e9e"},
    "wind": {"emoji": "💨", "label": "WIND", "color": "#00897b"},
    "hot": {"emoji": "🔥", "label": "HOT", "color": "#e53935"},
    "cold": {"emoji": "🥶", "label": "COLD", "color": "#039be5"},
    "office": {"emoji": "🏢", "label": "OFF", "color": "#5c6bc0"},
    "home": {"emoji": "🏠", "label": "HOME", "color": "#43a047"},
    "factory": {"emoji": "🏭", "label": "FAC", "color": "#6d4c41"},
    "hospital": {"emoji": "🏥", "label": "HOS", "color": "#e53935"},
    "school": {"emoji": "🏫", "label": "SCH", "color": "#fb8c00"},
    "warning": {"emoji": "⚠️", "label": "WRN", "color": "#f9a825"},
    "check": {"emoji": "✅", "label": "OK", "color": "#2e7d32"},
    "star": {"emoji": "⭐", "label": "★", "color": "#fbc02d"},
    "shop": {"emoji": "🛒", "label": "SHOP", "color": "#00838f"},
    "truck": {"emoji": "🚚", "label": "TRK", "color": "#546e7a"},
}


def icon_meta(icon: Optional[str]) -> dict[str, str]:
    key = (icon or "pin").strip().lower().replace("_", "-").replace(" ", "-")
    aliases = {
        "sun": "sunny",
        "clear": "sunny",
        "cloud": "cloudy",
        "clouds": "cloudy",
        "partlycloudy": "partly-cloudy",
        "shower": "rain",
        "showers": "rain",
        "thunder": "storm",
        "thunderstorm": "storm",
        "mist": "fog",
        "haze": "fog",
        "default": "pin",
        "marker": "pin",
        "location": "pin",
    }
    key = aliases.get(key, key)
    return dict(ICONS.get(key, ICONS["pin"]))

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_LOOKUP = os.path.normpath(
    os.path.join(_SCRIPT_DIR, "..", "assets", "place_lookup.json")
)


# ── Place lookup (offline aliases only — no external APIs) ───────────────────

def load_place_lookup(path: Optional[str] = None) -> dict[str, dict[str, Any]]:
    lookup_path = path or _DEFAULT_LOOKUP
    if not os.path.isfile(lookup_path):
        return {}
    with open(lookup_path, encoding="utf-8") as fh:
        data = json.load(fh)
    places = data.get("places") if isinstance(data, dict) else None
    if not isinstance(places, dict):
        return {}
    return {str(k).lower(): v for k, v in places.items() if isinstance(v, dict)}


def lookup_place(
    text: str,
    places: Mapping[str, Mapping[str, Any]],
) -> Optional[tuple[float, float, str]]:
    """Match name/address against bundled approximate centroids."""
    if not text or not places:
        return None
    key = text.strip().lower()
    for noise in (", australia", ", nsw", " nsw", ", sydney", " sydney"):
        key = key.replace(noise, "")
    key = key.strip(" ,")

    if key in places:
        p = places[key]
        return float(p["lat"]), float(p["lon"]), str(p.get("label") or text)

    best: Optional[tuple[int, str]] = None
    for alias in places:
        if alias in key or key in alias:
            score = len(alias)
            if best is None or score > best[0]:
                best = (score, alias)
    if best:
        p = places[best[1]]
        return float(p["lat"]), float(p["lon"]), str(p.get("label") or text)
    return None


def _customer_coords(raw: Mapping[str, Any]) -> Optional[tuple[float, float]]:
    """Return lat/lon from payload if present (user, prior tools, or agent).

    Always takes precedence over place_lookup. Accepts common aliases used by
    Dataverse/CRM exports (latitude/longitude, lng).
    """
    lat_raw = raw.get("lat", raw.get("latitude"))
    lon_raw = raw.get("lon", raw.get("lng", raw.get("longitude")))
    if lat_raw is None or lon_raw is None:
        return None
    if isinstance(lat_raw, str) and not lat_raw.strip():
        return None
    if isinstance(lon_raw, str) and not lon_raw.strip():
        return None
    try:
        lat, lon = float(lat_raw), float(lon_raw)
    except (TypeError, ValueError) as e:
        raise ValueError(
            f"Invalid lat/lon values: lat={lat_raw!r}, lon={lon_raw!r}"
        ) from e
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError(f"lat/lon out of range: lat={lat}, lon={lon}")
    return lat, lon


def resolve_points(
    points: Sequence[Mapping[str, Any]],
    *,
    place_lookup_path: Optional[str] = None,
    min_count: int = 1,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalise points to lat/lon + optional value/icon (offline only).

    Precedence per point:
    1. ``lat``/``lon`` already in the payload (user, prior tools like Dataverse,
       or agent web search) — always wins
    2. Bundled place lookup on location/address/name

    Does not call external APIs. If unresolved, raises with guidance for the agent
    to obtain coordinates (prior tool or web search) and re-invoke with lat/lon.
    """
    if len(points) < min_count:
        raise ValueError(f"Need at least {min_count} location(s).")
    places = load_place_lookup(place_lookup_path)
    warnings: list[str] = []
    resolved: list[dict[str, Any]] = []

    for i, raw in enumerate(points):
        name = str(raw.get("name") or f"Point {i + 1}")
        location = str(
            raw.get("location")
            or raw.get("address")
            or raw.get("display_name")
            or name
        )
        display = location
        value = raw.get("value", raw.get("label_value"))
        value_num = raw.get("value_num", raw.get("metric"))
        if value_num is not None:
            try:
                value_num = float(value_num)
            except (TypeError, ValueError):
                value_num = None
        icon = str(raw.get("icon") or raw.get("marker") or "pin")
        color = raw.get("color")

        coords = _customer_coords(raw)
        if coords is not None:
            lat, lon = coords
            source = "coords"
            if display == name:
                display = f"{lat:.5f}, {lon:.5f}"
        else:
            query = str(raw.get("location") or raw.get("address") or name)
            hit = lookup_place(query, places) or lookup_place(name, places)
            if hit:
                lat, lon, display = hit
                source = "place_lookup"
                warnings.append(
                    f"{name!r}: used bundled place_lookup centroid (approximate)."
                )
            else:
                raise ValueError(
                    f"Point {i + 1} ({name!r}): no lat/lon and no place_lookup match "
                    f"for {query!r}. The sandbox cannot call external geocoders — "
                    "use lat/lon from a prior tool (e.g. Dataverse) or web-search "
                    "them, then pass lat and lon into the payload."
                )

        meta = icon_meta(icon)
        fill = str(color) if color else meta["color"]
        resolved.append(
            {
                "name": name,
                "lat": lat,
                "lon": lon,
                "address": str(raw.get("address") or raw.get("location") or ""),
                "location": location,
                "display_name": display,
                "coord_source": source,
                "value": None if value is None else str(value),
                "value_num": value_num,
                "icon": (icon or "pin").strip().lower().replace("_", "-"),
                "emoji": meta["emoji"],
                "icon_label": meta["label"],
                "color": fill,
            }
        )
    return resolved, warnings


def resolve_stops(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], list[str]]:
    kwargs.setdefault("min_count", 2)
    return resolve_points(*args, **kwargs)


# ── Distance / geometry (offline haversine) ──────────────────────────────────

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def offline_table(
    stops: Sequence[Mapping[str, Any]],
    profile: str = "driving",
) -> tuple[list[list[float]], list[list[float]]]:
    """Haversine distances with road factor + speed → (durations, distances)."""
    n = len(stops)
    factor = _ROAD_FACTOR.get(profile, 1.3)
    speed = _SPEED_MPS.get(profile, _SPEED_MPS["driving"])
    distances = [[0.0] * n for _ in range(n)]
    durations = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            d = haversine_m(stops[i]["lat"], stops[i]["lon"], stops[j]["lat"], stops[j]["lon"])
            d *= factor
            distances[i][j] = d
            durations[i][j] = d / speed
    return durations, distances


def offline_route(
    ordered: Sequence[Mapping[str, Any]],
    profile: str = "driving",
) -> dict[str, Any]:
    """Straight-line segments between ordered stops (GeoJSON LineString)."""
    if not ordered:
        return {
            "distance_m": 0.0,
            "duration_s": 0.0,
            "geometry": {"type": "LineString", "coordinates": []},
        }
    factor = _ROAD_FACTOR.get(profile, 1.3)
    speed = _SPEED_MPS.get(profile, _SPEED_MPS["driving"])
    coords: list[list[float]] = []
    total = 0.0
    for a, b in zip(ordered, ordered[1:]):
        # densify segment for smoother PNG/SVG
        steps = 8
        for t in range(steps + 1):
            if t == 0 and coords:
                continue
            u = t / steps
            lat = a["lat"] + (b["lat"] - a["lat"]) * u
            lon = a["lon"] + (b["lon"] - a["lon"]) * u
            coords.append([lon, lat])
        total += haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) * factor
    return {
        "distance_m": total,
        "duration_s": total / speed,
        "geometry": {"type": "LineString", "coordinates": coords},
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


def markdown_summary(
    points: Sequence[Mapping[str, Any]],
    *,
    kind: str,
    chart_path: str,
    distance_m: float = 0.0,
    duration_s: float = 0.0,
    round_trip: bool = False,
    profile: str = "driving",
    routing_source: str = "none",
) -> str:
    """Markdown the agent can paste inline with the map image."""
    if kind == "map":
        lines = [
            "### Map",
            "",
            f"**Locations:** {len(points)} | **Kind:** marker map (no routing)",
            "",
            "| # | Name | Location | Value | Icon |",
            "| ---: | --- | --- | --- | --- |",
        ]
        for i, s in enumerate(points, start=1):
            loc = s.get("display_name") or s.get("location") or f"{s['lat']:.5f}, {s['lon']:.5f}"
            val = s.get("value") or ""
            icon = s.get("icon") or "pin"
            lines.append(f"| {i} | {s['name']} | {loc} | {val} | {icon} |")
        lines += [
            "",
            f"![Map]({chart_path})",
            "",
            "*Static lat/lon plot (PNG). Open the HTML export for an OpenStreetMap basemap.*",
        ]
        return "\n".join(lines)

    method = "offline straight-line estimate (not road geometry)"
    foot = (
        "**PNG** = straight-line sketch between stops (approximate distance).  \n"
        "**HTML** = actual road-following route on OpenStreetMap (via OSRM in the browser)."
    )
    lines = [
        "### Optimised route",
        "",
        f"**Profile:** {profile} | **Distance (PNG est.):** {_fmt_km(distance_m)} | "
        f"**Est. time (PNG):** {_fmt_duration(duration_s)}"
        + (" | **Round trip**" if round_trip else "")
        + f" | **Method:** {method}",
        "",
        "> **Route display:** The PNG uses **straight lines** between stops. "
        "Open the **HTML** export for the **real road path** (the line follows streets).",
        "",
        "| # | Stop | Location |",
        "| ---: | --- | --- |",
    ]
    for i, s in enumerate(points, start=1):
        loc = s.get("display_name") or s.get("address") or f"{s['lat']:.5f}, {s['lon']:.5f}"
        lines.append(f"| {i} | {s['name']} | {loc} |")
    if round_trip and points:
        lines.append(f"| {len(points) + 1} | *(return)* | {points[0]['name']} |")
    lines += ["", f"![Route map]({chart_path})", "", f"*{foot}*"]
    return "\n".join(lines)


markdown_route = markdown_summary  # alias


# ── PNG map ──────────────────────────────────────────────────────────────────

def save_png(
    ordered: Sequence[Mapping[str, Any]],
    geometry: Mapping[str, Any],
    *,
    out: str,
    title: str,
    round_trip: bool = False,
    distance_m: float = 0.0,
    duration_s: float = 0.0,
    kind: str = "route",
) -> str:
    coords = geometry.get("coordinates") or []
    xs = [c[0] for c in coords] if coords else [s["lon"] for s in ordered]
    ys = [c[1] for c in coords] if coords else [s["lat"] for s in ordered]

    fig, ax = plt.subplots(figsize=(11, 8.5), dpi=140)
    ax.set_facecolor("#f4f7fb")
    fig.patch.set_facecolor("white")

    if kind == "route" and coords:
        ax.plot(
            [c[0] for c in coords],
            [c[1] for c in coords],
            color="#0078d4",
            linewidth=2.4,
            solid_capstyle="round",
            zorder=2,
            label="Route",
        )

    for i, s in enumerate(ordered):
        colour = s.get("color") or (
            "#107c10"
            if kind == "route" and i == 0
            else (
                "#d83b01"
                if kind == "route" and i == len(ordered) - 1 and not round_trip
                else "#0078d4"
            )
        )
        ax.scatter(
            s["lon"], s["lat"], s=160, c=colour, edgecolors="white",
            linewidths=1.6, zorder=4,
        )
        marker_text = str(i + 1) if kind == "route" else str(s.get("icon_label") or (i + 1))
        ax.annotate(
            marker_text,
            (s["lon"], s["lat"]),
            ha="center", va="center",
            fontsize=7 if len(str(marker_text)) > 2 else 8,
            fontweight="bold", color="white", zorder=5,
        )
        label = s["name"]
        if s.get("value"):
            label = f"{label} ({s['value']})"
        ax.annotate(
            label, (s["lon"], s["lat"]),
            textcoords="offset points", xytext=(10, 10),
            fontsize=8, color="#323130", zorder=5,
        )

    if xs and ys:
        pad_x = max((max(xs) - min(xs)) * 0.15, 0.02)
        pad_y = max((max(ys) - min(ys)) * 0.15, 0.02)
        ax.set_xlim(min(xs) - pad_x, max(xs) + pad_x)
        ax.set_ylim(min(ys) - pad_y, max(ys) + pad_y)

    if kind == "route":
        subtitle = f"{_fmt_km(distance_m)} | {_fmt_duration(duration_s)} | straight-line estimate"
        subtitle += " | round trip" if round_trip else ""
    else:
        subtitle = f"{len(ordered)} locations | marker map"

    # Title + metrics as separate figure texts with an inch-based gap.
    # (ax.set_title + fig.suptitle previously overlapped on long titles.)
    wrapped_title = "\n".join(textwrap.wrap(title, width=56)) or title
    n_title_lines = wrapped_title.count("\n") + 1
    fig_h = float(fig.get_size_inches()[1])
    title_pt, title_ls = 13.0, 1.35
    title_h_in = n_title_lines * (title_pt / 72.0) * title_ls
    gap_in = 0.22  # clear air between title baseline and metrics
    title_top = 0.97
    fig.text(
        0.5, title_top, wrapped_title,
        transform=fig.transFigure, ha="center", va="top",
        fontsize=title_pt, fontweight="bold", color="#201f1e",
        linespacing=title_ls,
    )
    sub_y = title_top - (title_h_in + gap_in) / fig_h
    fig.text(
        0.5, sub_y, subtitle,
        transform=fig.transFigure, ha="center", va="top",
        fontsize=10, fontweight="normal", color="#605e5c",
    )
    ax.set_xlabel("Longitude", fontsize=8, color="#605e5c")
    ax.set_ylabel("Latitude", fontsize=8, color="#605e5c")
    ax.tick_params(labelsize=7, colors="#605e5c")
    ax.grid(True, color="#e1dfdd", linewidth=0.6, zorder=0)
    for spine in ax.spines.values():
        spine.set_color("#d2d0ce")
    # PNG is a matplotlib coordinate plot — not an OSM tile basemap.
    footer = (
        "Straight-line sketch (not road route)"
        if kind == "route"
        else "Static plot (lat/lon)"
    )
    ax.text(
        0.01, 0.01, footer,
        transform=ax.transAxes, fontsize=7, color="#8a8886",
        ha="left", va="bottom",
    )

    top = max(0.68, sub_y - 0.06)
    fig.subplots_adjust(left=0.09, right=0.97, bottom=0.08, top=top)
    fig.savefig(out, facecolor=fig.get_facecolor(), pad_inches=0.3)
    plt.close(fig)
    return out


# ── Interactive HTML (Leaflet + OpenStreetMap) ───────────────────────────────

def save_html(
    ordered: Sequence[Mapping[str, Any]],
    geometry: Mapping[str, Any],
    *,
    out: str,
    title: str,
    round_trip: bool = False,
    distance_m: float = 0.0,
    duration_s: float = 0.0,
    profile: str = "driving",
    routing_source: str = "haversine_offline",
    kind: str = "route",
) -> str:
    """Leaflet + OSM interactive map with markers/icons, optional route, legend."""
    stops_js = json.dumps(
        [
            {
                "n": i + 1,
                "name": s["name"],
                "lat": s["lat"],
                "lon": s["lon"],
                "display": s.get("display_name") or s.get("location") or s.get("address") or "",
                "value": s.get("value") or "",
                "icon": s.get("icon") or "pin",
                "emoji": s.get("emoji") or "📍",
                "color": s.get("color") or "#0078d4",
            }
            for i, s in enumerate(ordered)
        ],
        ensure_ascii=False,
    )
    geom_js = json.dumps(
        geometry or {"type": "LineString", "coordinates": []}, ensure_ascii=False
    )
    title_esc = (
        title.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    is_route = kind == "route"
    if is_route:
        method_label = "Loading road route…"
        chips = (
            f'<div class="chip" id="chipDistance"><strong>{_fmt_km(distance_m)}</strong> distance (est.)</div>'
            f'<div class="chip" id="chipDuration"><strong>{_fmt_duration(duration_s)}</strong> est. time</div>'
            f'<div class="chip"><strong>{profile}</strong> profile</div>'
            f'<div class="chip"><strong>'
            f'{"Round trip" if round_trip else "One way"}</strong></div>'
            f'<div class="chip" id="chipRouteSrc"><strong>OSRM</strong> road path</div>'
        )
        panel_title = "Visit order"
        hint = (
            "Click a stop to fly to it. "
            "Blue line = real road route (OSRM). Falls back to straight lines if offline."
        )
        route_btn_display = "inline-block"
        point_word = "stops"
    else:
        method_label = "Marker map (no routing)"
        chips = (
            f'<div class="chip"><strong>{len(ordered)}</strong> locations</div>'
            f'<div class="chip"><strong>Icons / values</strong> supported</div>'
        )
        panel_title = "Locations"
        hint = "Click a location to fly to its marker."
        route_btn_display = "none"
        point_word = "points"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>{title_esc}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
  integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
  integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<style>
  :root {{
    --bg: #eef2f6; --card: #ffffff; --text: #1b1a19; --muted: #605e5c;
    --accent: #0078d4; --border: #d8d6d4; --shadow: 0 8px 28px rgba(15, 23, 42, 0.10);
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh;
    font-family: "Segoe UI", system-ui, sans-serif; color: var(--text);
    background:
      radial-gradient(1200px 500px at 10% -10%, #d6e8f8 0%, transparent 55%),
      radial-gradient(900px 400px at 100% 0%, #e7f2ea 0%, transparent 50%),
      var(--bg);
  }}
  .shell {{ max-width: 1180px; margin: 0 auto; padding: 18px 16px 28px; }}
  header {{
    display: flex; flex-wrap: wrap; gap: 12px 20px; align-items: flex-end;
    justify-content: space-between; margin-bottom: 14px;
  }}
  h1 {{ margin: 0 0 4px; font-size: 1.45rem; letter-spacing: -0.02em; }}
  .sub {{ color: var(--muted); font-size: 0.92rem; }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .chip {{
    background: var(--card); border: 1px solid var(--border); border-radius: 999px;
    padding: 6px 12px; font-size: 0.82rem; color: var(--muted);
  }}
  .chip strong {{ color: var(--text); font-weight: 600; }}
  .layout {{ display: grid; grid-template-columns: 320px 1fr; gap: 14px; }}
  @media (max-width: 860px) {{ .layout {{ grid-template-columns: 1fr; }} }}
  .panel, .map-card {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 14px; box-shadow: var(--shadow);
  }}
  .panel {{ padding: 14px; max-height: 72vh; overflow: auto; }}
  .panel h2 {{ margin: 0 0 10px; font-size: 0.95rem; }}
  .hint {{ color: var(--muted); font-size: 0.78rem; margin: -4px 0 12px; }}
  ol.stops {{ margin: 0; padding: 0; list-style: none; }}
  ol.stops li {{
    display: grid; grid-template-columns: 34px 1fr; gap: 10px;
    padding: 9px 8px; margin: 0 0 4px; border-radius: 10px; cursor: pointer;
  }}
  ol.stops li:hover, ol.stops li.active {{ background: #f3f8fd; }}
  .badge {{
    width: 32px; height: 32px; border-radius: 50%; background: var(--accent);
    color: #fff; font: 700 14px/32px "Segoe UI", sans-serif; text-align: center;
  }}
  ol.stops .meta {{ color: var(--muted); font-size: 0.76rem; margin-top: 2px; }}
  ol.stops .val {{ font-weight: 600; color: var(--text); font-size: 0.84rem; }}
  .map-card {{ position: relative; overflow: hidden; min-height: 520px; }}
  #map {{ height: 72vh; min-height: 520px; width: 100%; }}
  .legend {{
    position: absolute; z-index: 1000; right: 12px; bottom: 28px;
    background: rgba(255,255,255,.96); border: 1px solid var(--border);
    border-radius: 12px; padding: 10px 12px; box-shadow: var(--shadow);
    font-size: 0.78rem; min-width: 170px; max-width: 230px;
  }}
  .legend h3 {{ margin: 0 0 6px; font-size: 0.8rem; }}
  .legend-row {{ display: flex; align-items: center; gap: 8px; margin: 4px 0; }}
  .swatch {{
    width: 16px; height: 16px; border-radius: 50%; border: 2px solid #fff;
    box-shadow: 0 0 0 1px rgba(0,0,0,.12); display:inline-flex; align-items:center;
    justify-content:center; font-size: 10px;
  }}
  .swatch.line {{ width: 22px; height: 4px; border-radius: 2px; border: none; box-shadow: none; }}
  .toolbar {{
    position: absolute; z-index: 1000; top: 12px; left: 52px; display: flex; gap: 6px;
  }}
  .toolbar button {{
    border: 1px solid var(--border); background: rgba(255,255,255,.96);
    border-radius: 8px; padding: 7px 11px; cursor: pointer; font-size: 0.8rem;
  }}
  .foot {{ margin-top: 12px; font-size: 0.75rem; color: var(--muted); }}
  .foot a {{ color: var(--accent); }}
  .marker-pin {{
    width: 34px; height: 34px; border-radius: 50%; color: #fff;
    font: 700 14px/34px "Segoe UI", sans-serif; text-align: center;
    border: 3px solid #fff; box-shadow: 0 3px 10px rgba(0,0,0,.28);
  }}
  .marker-pin.icon {{ font-size: 18px; line-height: 34px; }}
</style>
</head>
<body>
<div class="shell">
  <header>
    <div>
      <h1>{title_esc}</h1>
      <div class="sub" id="methodLabel">{method_label} on OpenStreetMap</div>
    </div>
    <div class="chips">
      {chips}
      <div class="chip"><strong>{len(ordered)}</strong> {point_word}</div>
    </div>
  </header>
  <div class="layout">
    <aside class="panel">
      <h2>{panel_title}</h2>
      <p class="hint">{hint}</p>
      <ol class="stops" id="stopList"></ol>
    </aside>
    <div class="map-card">
      <div class="toolbar">
        <button type="button" id="btnFit">Fit all</button>
        <button type="button" id="btnToggleRoute" style="display:{route_btn_display}">Toggle route</button>
        <button type="button" id="btnToggleLabels">Toggle labels</button>
      </div>
      <div id="map"></div>
      <div class="legend" id="legend"><h3>Legend</h3></div>
    </div>
  </div>
  <p class="foot">
    Map data &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>
    · <a href="https://leafletjs.com/" target="_blank" rel="noopener">Leaflet</a>
    · Road routes via <a href="http://project-osrm.org/" target="_blank" rel="noopener">OSRM</a> (browser)
  </p>
</div>
<script>
const STOPS = {stops_js};
const GEOM = {geom_js};
const ROUND = {str(round_trip).lower()};
const SOURCE = {json.dumps(routing_source)};
const PROFILE = {json.dumps(profile)};
const KIND = {json.dumps(kind)};
const IS_ROUTE = KIND === 'route';
const OSRM_PROFILE = ({{ driving: 'driving', walking: 'foot', cycling: 'bike' }})[PROFILE] || 'driving';
const markers = [];
let routeLayer = null;
let labelsOn = true;

function fmtKm(m) {{
  const km = Number(m) / 1000;
  return (km < 10 ? km.toFixed(1) : Math.round(km).toString()) + ' km';
}}
function fmtDuration(s) {{
  const sec = Math.max(0, Math.round(Number(s)));
  const h = Math.floor(sec / 3600);
  const m = Math.round((sec % 3600) / 60);
  if (h <= 0) return m + ' min';
  return h + ' h ' + m + ' min';
}}
function setMethod(text) {{
  const el = document.getElementById('methodLabel');
  if (el) el.textContent = text;
}}
function setChip(id, html) {{
  const el = document.getElementById(id);
  if (el) el.innerHTML = html;
}}

function markerIcon(s) {{
  const useEmoji = s.icon && s.icon !== 'pin';
  const inner = useEmoji ? s.emoji : String(s.n);
  const cls = useEmoji ? 'marker-pin icon' : 'marker-pin';
  return L.divIcon({{
    className: '',
    html: `<div class="${{cls}}" style="background:${{s.color}}">${{inner}}</div>`,
    iconSize: [34, 34], iconAnchor: [17, 17], popupAnchor: [0, -18]
  }});
}}

const list = document.getElementById('stopList');
const legend = document.getElementById('legend');
const seenIcons = new Map();
STOPS.forEach((s, i) => {{
  const li = document.createElement('li');
  li.dataset.idx = String(i);
  const badge = (s.icon && s.icon !== 'pin') ? s.emoji : String(s.n);
  li.innerHTML = `<span class="badge" style="background:${{s.color}}">${{badge}}</span>
    <div><strong>${{s.n}}. ${{s.name}}</strong>
    ${{s.value ? `<div class="val">${{s.value}}</div>` : ''}}
    <div class="meta">${{s.display || (s.lat.toFixed(5)+', '+s.lon.toFixed(5))}}</div></div>`;
  li.addEventListener('click', () => focusStop(i));
  list.appendChild(li);
  if (!seenIcons.has(s.icon)) seenIcons.set(s.icon, s);
}});
if (IS_ROUTE && ROUND && STOPS.length) {{
  const li = document.createElement('li');
  li.innerHTML = `<span class="badge" style="background:#8764b8">↩</span>
    <div><strong>Return to start</strong><div class="meta">Back to 1. ${{STOPS[0].name}}</div></div>`;
  li.addEventListener('click', () => focusStop(0));
  list.appendChild(li);
}}
if (IS_ROUTE) {{
  legend.innerHTML += `
    <div class="legend-row"><span class="swatch" style="background:#107c10"></span> Start</div>
    <div class="legend-row"><span class="swatch" style="background:#0078d4"></span> Stop</div>
    <div class="legend-row"><span class="swatch line" style="background:#0f6cbd"></span> Road route</div>`;
}} else {{
  seenIcons.forEach((s) => {{
    legend.innerHTML += `<div class="legend-row"><span class="swatch" style="background:${{s.color}}">${{s.emoji}}</span> ${{s.icon}}</div>`;
  }});
}}

const map = L.map('map', {{ zoomControl: true }});
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 19, attribution: '&copy; OpenStreetMap'
}}).addTo(map);

function drawRoute(latlngs, {{ road }}) {{
  if (routeLayer) map.removeLayer(routeLayer);
  routeLayer = null;
  if (!latlngs || latlngs.length < 2) return;
  routeLayer = L.polyline(latlngs, {{
    color: '#0f6cbd',
    weight: road ? 5 : 5,
    opacity: 0.92,
    dashArray: road ? null : '10 8',
  }}).addTo(map);
}}

function fallbackRoute() {{
  const latlngs = (GEOM.coordinates || []).map(c => [c[1], c[0]]);
  drawRoute(latlngs, {{ road: false }});
  setMethod('Approximate path (OSRM unavailable) on OpenStreetMap');
  setChip('chipRouteSrc', '<strong>Offline</strong> straight-line est.');
}}

async function loadRoadRoute() {{
  if (!IS_ROUTE || STOPS.length < 2) return;
  const pts = STOPS.map(s => s.lon.toFixed(6) + ',' + s.lat.toFixed(6));
  if (ROUND) pts.push(STOPS[0].lon.toFixed(6) + ',' + STOPS[0].lat.toFixed(6));
  // Public OSRM demo — used only from the browser (not the Python sandbox).
  const url = 'https://router.project-osrm.org/route/v1/' + OSRM_PROFILE + '/'
    + pts.join(';') + '?overview=full&geometries=geojson&steps=false';
  try {{
    const res = await fetch(url);
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    if (data.code !== 'Ok' || !data.routes || !data.routes[0]) {{
      throw new Error(data.code || 'no route');
    }}
    const route = data.routes[0];
    const latlngs = (route.geometry.coordinates || []).map(c => [c[1], c[0]]);
    drawRoute(latlngs, {{ road: true }});
    setChip('chipDistance', '<strong>' + fmtKm(route.distance) + '</strong> distance');
    setChip('chipDuration', '<strong>' + fmtDuration(route.duration) + '</strong> drive time');
    setChip('chipRouteSrc', '<strong>OSRM</strong> road path');
    setMethod('Road-following route (OSRM) on OpenStreetMap');
    fitAll();
  }} catch (err) {{
    console.warn('OSRM road route failed; using approximate path', err);
    fallbackRoute();
    fitAll();
  }}
}}

STOPS.forEach((s, i) => {{
  const m = L.marker([s.lat, s.lon], {{ icon: markerIcon(s), riseOnHover: true }});
  const valHtml = s.value ? `<div style="margin-top:4px"><strong>${{s.value}}</strong></div>` : '';
  m.bindPopup(`<strong>${{s.n}}. ${{s.name}}</strong>${{valHtml}}
    <div style="color:#605e5c;margin-top:4px">${{s.display || ''}}</div>`);
  m.bindTooltip(s.value ? (s.name + ' · ' + s.value) : s.name, {{ direction: 'top', offset: [0, -14] }});
  m.on('click', () => highlightList(i));
  m.addTo(map);
  markers.push(m);
}});

function fitAll() {{
  const layers = [...markers];
  if (routeLayer) layers.push(routeLayer);
  if (layers.length) map.fitBounds(L.featureGroup(layers).getBounds().pad(0.18));
  else map.setView([-33.87, 151.21], 11);
}}
function highlightList(i) {{
  document.querySelectorAll('#stopList li').forEach(el => el.classList.remove('active'));
  const el = document.querySelector('#stopList li[data-idx="' + i + '"]');
  if (el) el.classList.add('active');
}}
function focusStop(i) {{
  const m = markers[i]; if (!m) return;
  highlightList(i);
  map.flyTo(m.getLatLng(), Math.max(map.getZoom(), 13), {{ duration: 0.7 }});
  m.openPopup();
}}
document.getElementById('btnFit').onclick = fitAll;
document.getElementById('btnToggleRoute').onclick = () => {{
  if (!routeLayer) return;
  if (map.hasLayer(routeLayer)) map.removeLayer(routeLayer); else routeLayer.addTo(map);
}};
document.getElementById('btnToggleLabels').onclick = () => {{
  labelsOn = !labelsOn;
  markers.forEach((m, i) => {{
    m.unbindTooltip();
    if (labelsOn) {{
      const s = STOPS[i];
      m.bindTooltip(s.value ? s.name + ' · ' + s.value : s.name, {{ direction: 'top', offset: [0, -14] }});
    }}
  }});
}};
fitAll();
if (IS_ROUTE) loadRoadRoute();
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
    features: list[dict[str, Any]] = []
    coords = (geometry or {}).get("coordinates") or []
    if coords:
        features.append(
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
        )
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
                    "value": s.get("value"),
                    "icon": s.get("icon"),
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

    coords = (geometry or {}).get("coordinates") or []
    if coords:
        route_pm = ET.SubElement(doc, f"{{{kml_ns}}}Placemark")
        ET.SubElement(route_pm, f"{{{kml_ns}}}name").text = "Route"
        line = ET.SubElement(route_pm, f"{{{kml_ns}}}LineString")
        ET.SubElement(line, f"{{{kml_ns}}}tessellate").text = "1"
        ET.SubElement(line, f"{{{kml_ns}}}coordinates").text = " ".join(
            f"{c[0]},{c[1]},0" for c in coords
        )

    for i, s in enumerate(ordered, start=1):
        pm = ET.SubElement(doc, f"{{{kml_ns}}}Placemark")
        ET.SubElement(pm, f"{{{kml_ns}}}name").text = f"{i}. {s['name']}"
        bits = [
            s.get("display_name") or s.get("address") or "",
            f"value={s['value']}" if s.get("value") else "",
            f"icon={s['icon']}" if s.get("icon") else "",
        ]
        desc = " | ".join(b for b in bits if b)
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
        fh.write("sequence,name,lat,lon,location,value,icon\n")
        for i, s in enumerate(ordered, start=1):
            loc = (
                s.get("display_name") or s.get("location") or s.get("address") or ""
            ).replace('"', "'")
            val = str(s.get("value") or "").replace('"', "'")
            icon = str(s.get("icon") or "pin")
            fh.write(
                f'{i},"{s["name"]}",{s["lat"]:.6f},{s["lon"]:.6f},'
                f'"{loc}","{val}","{icon}"\n'
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


def _detect_kind(payload: Mapping[str, Any]) -> str:
    kind = str(payload.get("kind", "auto")).lower()
    if kind in ("map", "markers", "marker"):
        return "map"
    if kind == "route":
        return "route"
    if payload.get("optimize") is False or payload.get("route") is False:
        return "map"
    if payload.get("optimize") is True or payload.get("route") is True:
        return "route"
    if payload.get("round_trip") is True:
        return "route"
    if payload.get("points") and not payload.get("stops"):
        return "map"
    return "map"


def generate(data: Mapping[str, Any] | str) -> dict[str, Any]:
    """Generate a marker map and/or optimised route. Returns paths + markdown."""
    payload = _as_payload(data)
    points_in = payload.get("points") or payload.get("stops") or payload.get("locations")
    if not isinstance(points_in, list) or not points_in:
        raise ValueError(
            "`points` (or `stops` / `locations`) must be a non-empty list."
        )

    kind = _detect_kind(payload)
    profile = str(payload.get("profile", "driving")).lower()
    if profile not in PROFILES:
        raise ValueError(f"`profile` must be one of {PROFILES}.")

    round_trip = bool(payload.get("round_trip", True if kind == "route" else False))
    title = str(
        payload.get("title")
        or payload.get("chart_title")
        or ("Optimised route" if kind == "route" else "Map")
    )
    prefix = str(payload.get("out_prefix", "map" if kind == "map" else "route"))
    lookup_path = payload.get("place_lookup_path")

    # Fully offline — no external geocoding/routing APIs in the sandbox.
    resolved, warnings = resolve_points(
        points_in,
        place_lookup_path=lookup_path,
        min_count=2 if kind == "route" else 1,
    )

    routing_source = "none"
    ordered = list(resolved)
    route: dict[str, Any] = {
        "distance_m": 0.0,
        "duration_s": 0.0,
        "geometry": {"type": "LineString", "coordinates": []},
    }

    if kind == "route":
        start = payload.get("start", 0)
        if isinstance(start, str):
            names = [s["name"].lower() for s in resolved]
            if start.lower() not in names:
                raise ValueError(f"start stop {start!r} not found in stop names.")
            start_idx = names.index(start.lower())
        else:
            start_idx = int(start)

        durations, _distances = offline_table(resolved, profile=profile)
        order_idx = optimise_order(durations, start=start_idx, round_trip=round_trip)
        ordered = [resolved[i] for i in order_idx]
        route_stops = ordered + (
            [ordered[0]] if round_trip and len(ordered) > 1 else []
        )
        route = offline_route(route_stops, profile=profile)
        routing_source = "haversine_offline"

    chart_path = str(payload.get("chart_path", f"{prefix}_map.png"))
    save_png(
        ordered,
        route["geometry"],
        out=chart_path,
        title=title,
        round_trip=round_trip,
        distance_m=route["distance_m"],
        duration_s=route["duration_s"],
        kind=kind,
    )

    csv_path = str(payload.get("csv_path", f"{prefix}_points.csv"))
    save_stops_csv(ordered, csv_path)

    md = markdown_summary(
        ordered,
        kind=kind,
        chart_path=os.path.abspath(chart_path),
        distance_m=route["distance_m"],
        duration_s=route["duration_s"],
        round_trip=round_trip,
        profile=profile,
        routing_source=routing_source,
    )
    md_path = str(payload.get("markdown_path", f"{prefix}_summary.md"))
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)

    if kind == "route":
        method = "haversine offline + nearest-neighbour + 2-opt"
        attribution = (
            "PNG: straight-line sketch | HTML: road-following route via OSRM (browser)"
        )
    else:
        method = "marker map (no routing)"
        attribution = "PNG is a static lat/lon plot; OSM tiles only in the HTML export"

    result: dict[str, Any] = {
        "title": title,
        "kind": kind,
        "profile": profile,
        "routing_source": routing_source,
        "round_trip": round_trip if kind == "route" else False,
        "distance_m": route["distance_m"],
        "distance_label": _fmt_km(route["distance_m"]),
        "duration_s": route["duration_s"],
        "duration_label": _fmt_duration(route["duration_s"]),
        "stop_order": [s["name"] for s in ordered],
        "points": ordered,
        "stops": ordered,
        "chart_path": os.path.abspath(chart_path),
        "csv_path": os.path.abspath(csv_path),
        "markdown_path": os.path.abspath(md_path),
        "markdown": md,
        "method": method,
        "attribution": attribution,
        "warnings": warnings,
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
            routing_source=routing_source,
            kind=kind,
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


def plan_route(data: Mapping[str, Any] | str) -> dict[str, Any]:
    """Backward-compatible alias — forces route kind unless payload sets kind=map."""
    payload = _as_payload(data)
    if "kind" not in payload and payload.get("optimize") is not False:
        payload = dict(payload)
        payload.setdefault("kind", "route")
    return generate(payload)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate marker maps or optimised routes (OSM / offline).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--payload", required=True, help="Path to JSON payload.")
    p.add_argument("--kind", choices=["auto", "map", "route"], default=None)
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
    if args.kind is not None:
        data["kind"] = args.kind
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

    result = generate(data)

    print(result["markdown"])
    print()
    print(f"Kind:     {result.get('kind')}")
    print(f"Routing:  {result.get('routing_source')}")
    print(f"PNG:      {result['chart_path']}")
    print(f"CSV:      {result['csv_path']}")
    if result.get("html_path"):
        print(f"HTML:     {result['html_path']}")
    if result.get("geojson_path"):
        print(f"GeoJSON:  {result['geojson_path']}")
    if result.get("kml_path"):
        print(f"KML:      {result['kml_path']}")
    for w in result.get("warnings") or []:
        print(f"Warning:  {w}")

    if args.json_out:
        slim = {k: v for k, v in result.items() if k != "markdown"}
        print()
        print(json.dumps(slim, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
