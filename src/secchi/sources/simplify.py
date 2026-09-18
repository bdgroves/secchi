"""Reduce the catchment polygons to something a browser can load.

The cached file is 7.8 MB — fine on disk, not fine to ship on every page
load. Two reductions get it to a usable size without changing what the
map communicates:

1. **Geometry simplification** via Ramer–Douglas–Peucker. Catchment
   boundaries are traced at survey resolution; a web map at basin zoom
   cannot render metre-level detail, so most vertices are invisible.
2. **Attribute trimming.** Each feature carries 164 properties and the
   map needs about thirty.

Written without shapely deliberately — RDP is a dozen lines, and the
default pixi environment has to solve on three platforms and install on
every CI run. Real geospatial work belongs in the `geo` feature.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("secchi.sources.simplify")


def _perp_distance(pt: list, start: list, end: list) -> float:
    """Perpendicular distance from a point to the line through start-end."""
    x, y = pt[0], pt[1]
    x1, y1 = start[0], start[1]
    x2, y2 = end[0], end[1]
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
    # Twice the triangle area over the base length.
    return abs(dy * x - dx * y + x2 * y1 - y2 * x1) / ((dx * dx + dy * dy) ** 0.5)


def rdp(points: list, epsilon: float) -> list:
    """Ramer–Douglas–Peucker, iterative to avoid recursion limits.

    A catchment ring can hold thousands of vertices; Python's default
    recursion limit is 1000, so the textbook recursive form blows up on
    real data. This keeps an explicit stack instead.
    """
    n = len(points)
    if n < 3:
        return points[:]

    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]

    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        max_dist = -1.0
        index = first
        for i in range(first + 1, last):
            d = _perp_distance(points[i], points[first], points[last])
            if d > max_dist:
                max_dist, index = d, i
        if max_dist > epsilon:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))

    return [p for p, k in zip(points, keep) if k]


def _simplify_ring(ring: list, epsilon: float) -> list | None:
    """Simplify a closed ring, keeping it closed and still a polygon.

    Returns None if simplification would leave fewer than four points
    (three distinct plus the closing repeat), which is no longer a
    polygon — better to drop the ring than emit invalid geometry.
    """
    if len(ring) < 4:
        return ring
    # RDP on the open path, then re-close, so the closing vertex can't be
    # treated as an interior candidate and removed.
    simplified = rdp(ring[:-1], epsilon)
    if len(simplified) < 3:
        return None
    return simplified + [simplified[0]]


def simplify_geometry(geometry: dict, epsilon: float) -> dict | None:
    """Simplify a Polygon or MultiPolygon. Holes are simplified too."""
    gtype = geometry.get("type")
    coords = geometry.get("coordinates") or []

    if gtype == "Polygon":
        rings = [r for r in (_simplify_ring(ring, epsilon) for ring in coords) if r]
        if not rings:
            return None
        return {"type": "Polygon", "coordinates": rings}

    if gtype == "MultiPolygon":
        parts = []
        for part in coords:
            rings = [r for r in (_simplify_ring(ring, epsilon) for ring in part) if r]
            if rings:
                parts.append(rings)
        if not parts:
            return None
        return {"type": "MultiPolygon", "coordinates": parts}

    return geometry


def _count_vertices(geojson: dict) -> int:
    total = 0

    def walk(node: Any) -> None:
        nonlocal total
        if isinstance(node, (list, tuple)):
            if node and isinstance(node[0], (int, float)):
                total += 1
            else:
                for child in node:
                    walk(child)

    for feat in geojson.get("features", []):
        walk((feat.get("geometry") or {}).get("coordinates") or [])
    return total


def simplify_collection(
    geojson: dict,
    keep_properties: list[str],
    epsilon: float = 0.0004,
    round_to: int = 5,
) -> dict:
    """Produce a web-sized copy of the collection.

    ``epsilon`` is in degrees. The default 0.0004° is roughly 35–45 m at
    this latitude, which is below what a basin-scale web map can resolve
    and well inside the accuracy anyone should read off it.

    ``round_to`` trims coordinate precision. Five decimal places is about
    1 m — far more than a simplified boundary justifies, and it cuts the
    payload substantially because most of these coordinates carry twelve.
    """
    out_features = []
    dropped = 0

    for feat in geojson.get("features", []):
        geom = simplify_geometry(feat.get("geometry") or {}, epsilon)
        if geom is None:
            dropped += 1
            continue

        def round_coords(node: Any) -> Any:
            if isinstance(node, (list, tuple)):
                if node and isinstance(node[0], (int, float)):
                    return [round(float(node[0]), round_to), round(float(node[1]), round_to)]
                return [round_coords(c) for c in node]
            return node

        geom["coordinates"] = round_coords(geom["coordinates"])

        props = feat.get("properties") or {}
        kept = {k: props[k] for k in keep_properties if k in props}
        # Name is required for the attribute join and the popup, so keep it
        # even if it isn't in the display list.
        for key in ("Name", "Latitude", "Longitude"):
            if key in props and key not in kept:
                kept[key] = props[key]

        out_features.append({"type": "Feature", "properties": kept, "geometry": geom})

    before = _count_vertices(geojson)
    after = _count_vertices({"features": out_features})
    log.info("simplified %d features: %d -> %d vertices (%.1f%% kept)%s",
             len(out_features), before, after,
             100.0 * after / before if before else 0,
             f", dropped {dropped} degenerate" if dropped else "")

    return {"type": "FeatureCollection", "features": out_features}
