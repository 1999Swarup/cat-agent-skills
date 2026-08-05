#!/usr/bin/env python3
"""Optimize a multi-stop route — online (OSM/OSRM) or offline (sandbox-safe).

Default mode is ``auto``: use lat/lon or bundled place lookup first, try live
Nominatim/OSRM when possible, and fall back to haversine distances + straight
segments when SSL/network is blocked (typical Copilot Studio sandbox).

Always produces PNG + markdown. Optional HTML uses Leaflet + OpenStreetMap
tiles with numbered markers, route line, legend, and clickable stop list.
Optional GeoJSON / KML exports.

Usage::

    from route_map import plan_route
    result = plan_route({
        "stops": [
            {"name": "Bondi", "address": "Bondi Beach"},
            {"name": "Manly", "address": "Manly"},
            {"name": "CBD", "lat": -33.8688, "lon": 151.2093},
        ],
        "mode": "offline",   # recommended in restricted sandboxes
        "round_trip": True,
        "html": True,
    })
"""

from __future__ import annotations

import argparse
import json
import math
import os
import ssl
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
MODES = ("auto", "online", "offline")

# Approximate urban speeds (m/s) and road-factor vs straight-line distance.
_SPEED_MPS = {"driving": 35_000 / 3600, "walking": 5_000 / 3600, "cycling": 15_000 / 3600}
_ROAD_FACTOR = {"driving": 1.35, "walking": 1.15, "cycling": 1.25}

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_LOOKUP = os.path.normpath(
    os.path.join(_SCRIPT_DIR, "..", "assets", "place_lookup.json")
)


# ── HTTP helpers (SSL-tolerant) ──────────────────────────────────────────────

def _ssl_contexts() -> list[ssl.SSLContext]:
    """Try system certs first, then unverified (some sandboxes strip CA store)."""
    contexts: list[ssl.SSLContext] = []
    try:
        contexts.append(ssl.create_default_context())
    except Exception:
        pass
    unverified = ssl._create_unverified_context()  # noqa: SLF001
    contexts.append(unverified)
    return contexts


def _http_get(url: str, params: Optional[dict[str, Any]] = None, timeout: int = 20) -> Any:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    last_err: Optional[BaseException] = None
    for ctx in _ssl_contexts():
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # SSL, timeout, HTTP — try next context / raise
            last_err = e
            # Only retry on SSL-ish failures; HTTP 4xx/5xx won't improve
            if isinstance(e, urllib.error.HTTPError):
                body = e.read().decode("utf-8", errors="replace")[:300]
                raise RuntimeError(f"HTTP {e.code} from {url.split('?')[0]}: {body}") from e
            continue
    raise RuntimeError(
        f"Network/SSL error calling {url.split('?')[0]}: {last_err}"
    ) from last_err


# ── Place lookup (offline geocoding) ─────────────────────────────────────────

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
    # Strip common suffixes for matching
    for noise in (", australia", ", nsw", " nsw", ", sydney", " sydney"):
        key = key.replace(noise, "")
    key = key.strip(" ,")

    if key in places:
        p = places[key]
        return float(p["lat"]), float(p["lon"]), str(p.get("label") or text)

    # Substring: longest alias wins
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


# ── Geocoding ────────────────────────────────────────────────────────────────

def geocode_online(address: str, *, sleep: float = 1.1) -> tuple[float, float, str]:
    """Nominatim live geocode. Raises on network/SSL/empty result."""
    data = _http_get(
        NOMINATIM_URL,
        {"q": address, "format": "json", "limit": 1, "addressdetails": 0},
    )
    time.sleep(sleep)
    if not data:
        raise ValueError(f"Could not geocode address: {address!r}")
    hit = data[0]
    return float(hit["lat"]), float(hit["lon"]), str(hit.get("display_name") or address)


def _customer_coords(raw: Mapping[str, Any]) -> Optional[tuple[float, float]]:
    """Return customer-supplied (lat, lon) if present. Always takes precedence.

    Accepts lat/lon, latitude/longitude, or lng. Ignores null/blank values so an
    empty lat field does not block address/lookup fallback.
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


def resolve_stops(
    stops: Sequence[Mapping[str, Any]],
    *,
    mode: str = "auto",
    place_lookup_path: Optional[str] = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalise stops to lat/lon. Returns (resolved, warnings).

    Precedence per stop (never overridden once set):
    1. Customer ``lat``/``lon`` (or latitude/longitude/lng)
    2. Bundled place lookup on name/address
    3. Live Nominatim (auto/online only)
    """
    if len(stops) < 2:
        raise ValueError("Need at least 2 stops to plan a route.")
    places = load_place_lookup(place_lookup_path)
    warnings: list[str] = []
    resolved: list[dict[str, Any]] = []
    allow_online = mode in ("auto", "online")

    for i, raw in enumerate(stops):
        name = str(raw.get("name") or f"Stop {i + 1}")
        display = str(raw.get("address") or raw.get("display_name") or name)

        # 1) Customer coordinates always win — even if address/name is also set.
        coords = _customer_coords(raw)
        if coords is not None:
            lat, lon = coords
            source = "coords"
            # Keep address as label only; do not geocode or lookup over the pin.
            if not display or display == name:
                display = f"{lat:.5f}, {lon:.5f}"
        else:
            query = str(raw.get("address") or name)
            hit = lookup_place(query, places) or lookup_place(name, places)
            if hit:
                lat, lon, display = hit
                source = "place_lookup"
            elif allow_online:
                try:
                    lat, lon, display = geocode_online(query)
                    source = "nominatim"
                except Exception as e:
                    if mode == "online":
                        raise
                    warnings.append(
                        f"Live geocode failed for {name!r} ({e}); "
                        "no offline match — provide lat/lon."
                    )
                    raise ValueError(
                        f"Stop {i + 1} ({name!r}): cannot resolve location offline. "
                        f"Add lat/lon, or a name present in assets/place_lookup.json. "
                        f"Online error: {e}"
                    ) from e
            else:
                raise ValueError(
                    f"Stop {i + 1} ({name!r}): offline mode needs `lat`/`lon` "
                    "or a known place name from assets/place_lookup.json."
                )

        resolved.append(
            {
                "name": name,
                "lat": lat,
                "lon": lon,
                "address": str(raw.get("address") or ""),
                "display_name": display,
                "coord_source": source,
            }
        )
    return resolved, warnings


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


# ── Offline distance / geometry (sandbox-safe) ───────────────────────────────

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


def markdown_route(
    ordered_stops: Sequence[Mapping[str, Any]],
    *,
    distance_m: float,
    duration_s: float,
    round_trip: bool,
    profile: str,
    chart_path: str,
    routing_source: str = "osrm",
) -> str:
    """Markdown the agent can paste inline with the map image."""
    if routing_source == "osrm":
        method = "OSRM road routing"
        foot = "Map data (c) OpenStreetMap contributors | Routing via OSRM"
    else:
        method = "offline estimate (straight-line x road factor; not turn-by-turn)"
        foot = (
            "Offline mode: approximate centroids / coordinates | "
            "haversine + road factor (not live OSM routing)"
        )
    lines = [
        "### Optimised route",
        "",
        f"**Profile:** {profile} | **Distance:** {_fmt_km(distance_m)} | "
        f"**Est. time:** {_fmt_duration(duration_s)}"
        + (" | **Round trip**" if round_trip else "")
        + f" | **Method:** {method}",
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
        f"*{foot}*",
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


# ── Interactive HTML (Leaflet + OpenStreetMap) ───────────────────────────────

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
    routing_source: str = "osrm",
) -> str:
    """Leaflet + OSM interactive map with numbered markers, route, and legend."""
    stops_js = json.dumps(
        [
            {
                "n": i + 1,
                "name": s["name"],
                "lat": s["lat"],
                "lon": s["lon"],
                "display": s.get("display_name") or s.get("address") or "",
            }
            for i, s in enumerate(ordered)
        ],
        ensure_ascii=False,
    )
    geom_js = json.dumps(geometry, ensure_ascii=False)
    title_esc = (
        title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
    dist_label = _fmt_km(distance_m)
    time_label = _fmt_duration(duration_s)
    method_label = (
        "OSRM road route"
        if routing_source == "osrm"
        else "Approximate path (offline estimate)"
    )

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
    --accent: #0078d4; --border: #d8d6d4; --start: #107c10; --end: #d83b01;
    --route: #0f6cbd; --shadow: 0 8px 28px rgba(15, 23, 42, 0.10);
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh;
    font-family: "Segoe UI", system-ui, sans-serif;
    color: var(--text);
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
    box-shadow: 0 1px 2px rgba(0,0,0,.04);
  }}
  .chip strong {{ color: var(--text); font-weight: 600; }}
  .layout {{
    display: grid; grid-template-columns: 320px 1fr; gap: 14px;
  }}
  @media (max-width: 860px) {{ .layout {{ grid-template-columns: 1fr; }} }}
  .panel, .map-card {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 14px; box-shadow: var(--shadow);
  }}
  .panel {{ padding: 14px 14px 10px; max-height: 72vh; overflow: auto; }}
  .panel h2 {{ margin: 0 0 10px; font-size: 0.95rem; }}
  .hint {{ color: var(--muted); font-size: 0.78rem; margin: -4px 0 12px; }}
  ol.stops {{ margin: 0; padding: 0; list-style: none; }}
  ol.stops li {{
    display: grid; grid-template-columns: 30px 1fr; gap: 10px;
    padding: 9px 8px; margin: 0 0 4px; border-radius: 10px;
    cursor: pointer; transition: background .15s ease;
  }}
  ol.stops li:hover, ol.stops li.active {{ background: #f3f8fd; }}
  .badge {{
    width: 28px; height: 28px; border-radius: 50%; background: var(--accent);
    color: #fff; font: 700 12px/28px "Segoe UI", sans-serif; text-align: center;
    box-shadow: 0 2px 6px rgba(0,120,212,.35);
  }}
  .badge.start {{ background: var(--start); box-shadow: 0 2px 6px rgba(16,124,16,.35); }}
  .badge.end {{ background: var(--end); box-shadow: 0 2px 6px rgba(216,59,1,.35); }}
  .badge.ret {{ background: #8764b8; box-shadow: 0 2px 6px rgba(135,100,184,.35); }}
  ol.stops .meta {{ color: var(--muted); font-size: 0.76rem; margin-top: 2px; }}
  .map-card {{ position: relative; overflow: hidden; min-height: 520px; }}
  #map {{ height: 72vh; min-height: 520px; width: 100%; }}
  .legend {{
    position: absolute; z-index: 1000; right: 12px; bottom: 28px;
    background: rgba(255,255,255,.96); border: 1px solid var(--border);
    border-radius: 12px; padding: 10px 12px; box-shadow: var(--shadow);
    font-size: 0.78rem; line-height: 1.45; min-width: 160px;
  }}
  .legend h3 {{ margin: 0 0 6px; font-size: 0.8rem; }}
  .legend-row {{ display: flex; align-items: center; gap: 8px; margin: 4px 0; }}
  .swatch {{
    width: 14px; height: 14px; border-radius: 50%; border: 2px solid #fff;
    box-shadow: 0 0 0 1px rgba(0,0,0,.12);
  }}
  .swatch.line {{
    width: 22px; height: 4px; border-radius: 2px; border: none; box-shadow: none;
  }}
  .toolbar {{
    position: absolute; z-index: 1000; top: 12px; left: 52px;
    display: flex; gap: 6px; flex-wrap: wrap;
  }}
  .toolbar button {{
    border: 1px solid var(--border); background: rgba(255,255,255,.96);
    border-radius: 8px; padding: 7px 11px; cursor: pointer; font-size: 0.8rem;
    box-shadow: 0 2px 8px rgba(0,0,0,.08);
  }}
  .toolbar button:hover {{ border-color: var(--accent); color: var(--accent); }}
  .foot {{
    margin-top: 12px; font-size: 0.75rem; color: var(--muted);
  }}
  .foot a {{ color: var(--accent); }}
  .marker-pin {{
    width: 30px; height: 30px; border-radius: 50%;
    color: #fff; font: 700 13px/30px "Segoe UI", sans-serif; text-align: center;
    border: 3px solid #fff; box-shadow: 0 3px 10px rgba(0,0,0,.28);
  }}
  .leaflet-popup-content {{ margin: 10px 12px; font-size: 0.88rem; }}
  .popup-n {{
    display: inline-block; min-width: 22px; height: 22px; border-radius: 50%;
    background: var(--accent); color: #fff; font: 700 11px/22px "Segoe UI", sans-serif;
    text-align: center; margin-right: 6px;
  }}
</style>
</head>
<body>
<div class="shell">
  <header>
    <div>
      <h1>{title_esc}</h1>
      <div class="sub">{method_label} on OpenStreetMap</div>
    </div>
    <div class="chips">
      <div class="chip"><strong>{dist_label}</strong> distance</div>
      <div class="chip"><strong>{time_label}</strong> est. time</div>
      <div class="chip"><strong>{profile}</strong> profile</div>
      <div class="chip"><strong>{"Round trip" if round_trip else "One way"}</strong></div>
      <div class="chip"><strong>{len(ordered)}</strong> stops</div>
    </div>
  </header>
  <div class="layout">
    <aside class="panel">
      <h2>Visit order</h2>
      <p class="hint">Click a stop to fly to it on the map.</p>
      <ol class="stops" id="stopList"></ol>
    </aside>
    <div class="map-card">
      <div class="toolbar">
        <button type="button" id="btnFit">Fit route</button>
        <button type="button" id="btnToggleRoute">Toggle route</button>
        <button type="button" id="btnToggleLabels">Toggle labels</button>
      </div>
      <div id="map"></div>
      <div class="legend" id="legend">
        <h3>Legend</h3>
        <div class="legend-row"><span class="swatch" style="background:#107c10"></span> Start (1)</div>
        <div class="legend-row"><span class="swatch" style="background:#0078d4"></span> Stop (2…n)</div>
        <div class="legend-row"><span class="swatch" style="background:#d83b01"></span> Final stop</div>
        <div class="legend-row"><span class="swatch line" style="background:#0f6cbd"></span> Route path</div>
      </div>
    </div>
  </div>
  <p class="foot">
    Map data &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors
    · Interactive map via <a href="https://leafletjs.com/" target="_blank" rel="noopener">Leaflet</a>
    · Numbered markers show visit sequence
  </p>
</div>
<script>
const STOPS = {stops_js};
const GEOM = {geom_js};
const ROUND = {str(round_trip).lower()};
const SOURCE = {json.dumps(routing_source)};
const markers = [];
let routeLayer = null;
let labelsOn = true;

function colour(i) {{
  if (i === 0) return '#107c10';
  if (i === STOPS.length - 1 && !ROUND) return '#d83b01';
  if (i === STOPS.length - 1 && ROUND) return '#0078d4';
  return '#0078d4';
}}

function badgeClass(i) {{
  if (i === 0) return 'badge start';
  if (i === STOPS.length - 1 && !ROUND) return 'badge end';
  return 'badge';
}}

function numberedIcon(n, fill) {{
  return L.divIcon({{
    className: '',
    html: `<div class="marker-pin" style="background:${{fill}}">${{n}}</div>`,
    iconSize: [30, 30],
    iconAnchor: [15, 15],
    popupAnchor: [0, -16]
  }});
}}

const list = document.getElementById('stopList');
STOPS.forEach((s, i) => {{
  const li = document.createElement('li');
  li.dataset.idx = String(i);
  li.innerHTML = `<span class="${{badgeClass(i)}}">${{s.n}}</span>
    <div><strong>${{s.n}}. ${{s.name}}</strong>
    <div class="meta">${{s.display || (s.lat.toFixed(5) + ', ' + s.lon.toFixed(5))}}</div></div>`;
  li.addEventListener('click', () => focusStop(i));
  list.appendChild(li);
}});
if (ROUND && STOPS.length) {{
  const li = document.createElement('li');
  li.innerHTML = `<span class="badge ret">↩</span>
    <div><strong>Return to start</strong>
    <div class="meta">Back to 1. ${{STOPS[0].name}}</div></div>`;
  li.addEventListener('click', () => focusStop(0));
  list.appendChild(li);
  // Update legend note for round trip
  const leg = document.getElementById('legend');
  const row = document.createElement('div');
  row.className = 'legend-row';
  row.innerHTML = '<span class="swatch" style="background:#8764b8"></span> Return to start';
  leg.appendChild(row);
}}

const map = L.map('map', {{ zoomControl: true, scrollWheelZoom: true }});
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
}}).addTo(map);

const latlngs = (GEOM.coordinates || []).map(c => [c[1], c[0]]);
if (latlngs.length > 1) {{
  routeLayer = L.polyline(latlngs, {{
    color: '#0f6cbd',
    weight: SOURCE === 'osrm' ? 6 : 5,
    opacity: 0.9,
    dashArray: SOURCE === 'osrm' ? null : '10 8',
    lineJoin: 'round',
    lineCap: 'round'
  }}).addTo(map);
  routeLayer.bindPopup(SOURCE === 'osrm'
    ? '<strong>Road route</strong><br/>OSRM / OpenStreetMap'
    : '<strong>Approximate path</strong><br/>Offline estimate (not turn-by-turn)');
}}

const group = L.featureGroup();
STOPS.forEach((s, i) => {{
  const fill = colour(i);
  const m = L.marker([s.lat, s.lon], {{
    icon: numberedIcon(s.n, fill),
    title: s.n + '. ' + s.name,
    riseOnHover: true
  }});
  m.bindPopup(
    `<span class="popup-n" style="background:${{fill}}">${{s.n}}</span>` +
    `<strong>${{s.name}}</strong><br/>` +
    `<span style="color:#605e5c">${{s.display || (s.lat.toFixed(5) + ', ' + s.lon.toFixed(5))}}</span>`
  );
  if (labelsOn) {{
    m.bindTooltip(s.n + '. ' + s.name, {{
      permanent: false, direction: 'top', offset: [0, -12]
    }});
  }}
  m.on('click', () => highlightList(i));
  m.addTo(map);
  group.addLayer(m);
  markers.push(m);
}});

function fitAll() {{
  const layers = [];
  if (routeLayer) layers.push(routeLayer);
  markers.forEach(m => layers.push(m));
  if (layers.length) {{
    const g = L.featureGroup(layers);
    map.fitBounds(g.getBounds().pad(0.18));
  }} else {{
    map.setView([-33.87, 151.21], 11);
  }}
}}

function highlightList(i) {{
  document.querySelectorAll('#stopList li').forEach(el => el.classList.remove('active'));
  const el = document.querySelector('#stopList li[data-idx="' + i + '"]');
  if (el) el.classList.add('active');
}}

function focusStop(i) {{
  const m = markers[i];
  if (!m) return;
  highlightList(i);
  map.flyTo(m.getLatLng(), Math.max(map.getZoom(), 14), {{ duration: 0.7 }});
  m.openPopup();
}}

document.getElementById('btnFit').onclick = fitAll;
document.getElementById('btnToggleRoute').onclick = () => {{
  if (!routeLayer) return;
  if (map.hasLayer(routeLayer)) map.removeLayer(routeLayer);
  else routeLayer.addTo(map);
}};
document.getElementById('btnToggleLabels').onclick = () => {{
  labelsOn = !labelsOn;
  markers.forEach((m, i) => {{
    m.unbindTooltip();
    if (labelsOn) {{
      m.bindTooltip(STOPS[i].n + '. ' + STOPS[i].name, {{
        permanent: false, direction: 'top', offset: [0, -12]
      }});
    }}
  }});
}};

fitAll();
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

    mode = str(payload.get("mode", "auto")).lower()
    if mode not in MODES:
        raise ValueError(f"`mode` must be one of {MODES}.")

    round_trip = bool(payload.get("round_trip", True))
    title = str(payload.get("title") or payload.get("chart_title") or "Optimised route")
    prefix = str(payload.get("out_prefix", "route"))
    lookup_path = payload.get("place_lookup_path")

    resolved, warnings = resolve_stops(
        stops_in, mode=mode, place_lookup_path=lookup_path
    )

    start = payload.get("start", 0)
    if isinstance(start, str):
        names = [s["name"].lower() for s in resolved]
        if start.lower() not in names:
            raise ValueError(f"start stop {start!r} not found in stop names.")
        start_idx = names.index(start.lower())
    else:
        start_idx = int(start)

    routing_source = "haversine_offline"
    durations: list[list[float]]
    route: dict[str, Any]

    if mode in ("auto", "online"):
        try:
            durations, _distances = osrm_table(resolved, profile=profile)
            order_idx = optimise_order(durations, start=start_idx, round_trip=round_trip)
            ordered = [resolved[i] for i in order_idx]
            route_stops = ordered + (
                [ordered[0]] if round_trip and len(ordered) > 1 else []
            )
            route = osrm_route(route_stops, profile=profile)
            routing_source = "osrm"
        except Exception as e:
            if mode == "online":
                raise
            warnings.append(f"Live OSRM unavailable ({e}); using offline haversine.")
            durations, _distances = offline_table(resolved, profile=profile)
            order_idx = optimise_order(durations, start=start_idx, round_trip=round_trip)
            ordered = [resolved[i] for i in order_idx]
            route_stops = ordered + (
                [ordered[0]] if round_trip and len(ordered) > 1 else []
            )
            route = offline_route(route_stops, profile=profile)
            routing_source = "haversine_offline"
    else:
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
        routing_source=routing_source,
    )
    md_path = str(payload.get("markdown_path", f"{prefix}_summary.md"))
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)

    method = (
        "OSRM + nearest-neighbour + 2-opt"
        if routing_source == "osrm"
        else "haversine offline + nearest-neighbour + 2-opt"
    )
    attribution = (
        "(c) OpenStreetMap contributors | Routing via OSRM"
        if routing_source == "osrm"
        else "Offline estimate (haversine x road factor) | place_lookup / supplied coords"
    )

    result: dict[str, Any] = {
        "title": title,
        "profile": profile,
        "mode": mode,
        "routing_source": routing_source,
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
    p.add_argument("--mode", choices=list(MODES), default=None,
                   help="auto (default) | offline (sandbox) | online")
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
    if args.mode is not None:
        data["mode"] = args.mode
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

    print(result["markdown"])
    print()
    print(f"Mode:     {result.get('mode')} ({result.get('routing_source')})")
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
