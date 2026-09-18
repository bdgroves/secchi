"""Fetch and cache slow-changing reference data, and join stations to it.

Two datasets live here, both fetched deliberately rather than on the cron:

- **Catchment polygons** — a static GeoJSON on TEON's own domain.
  Watershed boundaries don't change, so this is a one-time fetch.
- **Catchment attributes** — ~168 climate and landscape variables per
  catchment, plus the variable dictionary that labels them.

The point of caching them is the **join**: assigning each sensor station
and stream gauge to the catchment containing it, so every reading carries
its catchment's characteristics. That is what converts an observation into
an explanation — "Glenbrook 2 reads 42 % soil moisture" becomes "Glenbrook 2
is a valley-bottom site with a shallow water table and meadow cover, in a
catchment receiving 689 mm a year".

Point-in-polygon is implemented here directly rather than via shapely or
geopandas. Those are the right tools for real geospatial work and belong
in the `geo` pixi feature, but a ray-casting test on a few dozen polygons
needs no dependency and keeps the default environment light.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Iterable

import httpx

from secchi.config import (
    HTTP_TIMEOUT_SECONDS,
    REFERENCE_DIR,
    USER_AGENT,
    WATERSHED_ATTRIBUTES_URL,
    WATERSHED_DISPLAY_VARIABLES,
    WATERSHED_EXCLUDE,
    WATERSHED_POLYGON_URL,
    WATERSHED_VARIABLES_URL,
)

log = logging.getLogger("secchi.sources.reference")

POLYGON_FILE = "tahoe_watersheds.geojson"
ATTRIBUTES_FILE = "watershed_attributes.json"
VARIABLES_FILE = "watershed_variables.json"


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_reference(out_dir: Path = REFERENCE_DIR, force: bool = False) -> int:
    """Download the watershed polygons, attributes and dictionary.

    Skips anything already cached unless ``force``. These are static
    datasets; re-fetching on a schedule would be pointless traffic.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    targets = [
        (WATERSHED_POLYGON_URL, POLYGON_FILE, "catchment polygons"),
        (WATERSHED_ATTRIBUTES_URL, ATTRIBUTES_FILE, "catchment attributes"),
        (WATERSHED_VARIABLES_URL, VARIABLES_FILE, "variable dictionary"),
    ]

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    ok = 0
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, headers=headers,
                      follow_redirects=True) as client:
        for url, filename, label in targets:
            dest = out_dir / filename
            if dest.exists() and not force:
                log.info("%s already cached (%s, %.1f KB) — use --force to refetch",
                         label, filename, dest.stat().st_size / 1024)
                ok += 1
                continue
            try:
                log.info("fetching %s from %s", label, url)
                resp = client.get(url)
                resp.raise_for_status()
                # Validate it parses before writing, so a cached file is
                # never half-written or an HTML error page.
                payload = resp.json()
                dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                log.info("wrote %s (%.1f KB)", dest, dest.stat().st_size / 1024)
                ok += 1
            except json.JSONDecodeError:
                log.error("%s did not return JSON — got %d bytes of something else",
                          label, len(resp.content))
            except httpx.HTTPError as exc:
                log.error("could not fetch %s: %s", label, exc)

    return 0 if ok == len(targets) else 1


def load_reference(out_dir: Path = REFERENCE_DIR) -> dict:
    """Load whatever reference data is cached."""
    out = {}
    for key, filename in (("polygons", POLYGON_FILE),
                          ("attributes", ATTRIBUTES_FILE),
                          ("variables", VARIABLES_FILE)):
        path = out_dir / filename
        if path.exists():
            try:
                out[key] = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                log.warning("cached %s is malformed: %s", filename, exc)
    return out


# ---------------------------------------------------------------------------
# Coordinate systems
# ---------------------------------------------------------------------------
# GeoJSON is *supposed* to be WGS84 longitude/latitude (RFC 7946), but
# exports from desktop GIS frequently aren't — an ArcGIS-derived file is
# often Web Mercator (EPSG:3857) with coordinates in metres, and state
# plane or UTM also turn up. A projected file makes every point-in-polygon
# test fail silently, which is exactly the 0-of-28 result the first
# catchment-join produced.
#
# Rather than assume, detect: degrees are bounded by ±180 / ±90, so
# anything larger is projected. Web Mercator is handled here because it's
# by far the most common case and its inverse is closed-form — no pyproj
# dependency for the default environment. Anything else is reported
# rather than silently mangled.

WEB_MERCATOR_R = 20037508.342789244


def _invert_web_mercator(x: float, y: float) -> tuple[float, float]:
    """EPSG:3857 metres -> (lng, lat) degrees."""
    lng = x / WEB_MERCATOR_R * 180.0
    lat = math.degrees(2 * math.atan(math.exp(y / WEB_MERCATOR_R * math.pi)) - math.pi / 2)
    return lng, lat


# Generous box around the Lake Tahoe basin, used to sanity-check a
# reprojection. Wider than USGS_BBOX because catchment polygons extend
# past the gauge network, and because a correct guess should land
# comfortably inside rather than scraping the edge.
TAHOE_SANITY_BOX = (-120.6, 38.6, -119.5, 39.6)


def _near_tahoe(lng: float, lat: float) -> bool:
    minx, miny, maxx, maxy = TAHOE_SANITY_BOX
    return minx <= lng <= maxx and miny <= lat <= maxy


def _coord_extent(geojson: dict) -> tuple[float, float, float, float] | None:
    """Overall coordinate range across every feature."""
    xs: list[float] = []
    ys: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, (list, tuple)):
            if node and isinstance(node[0], (int, float)) and len(node) >= 2:
                xs.append(float(node[0]))
                ys.append(float(node[1]))
            else:
                for child in node:
                    walk(child)

    for feat in geojson.get("features", []):
        walk((feat.get("geometry") or {}).get("coordinates") or [])
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def detect_crs(geojson: dict) -> str:
    """Guess the coordinate system from the values themselves.

    Returns "wgs84", "web-mercator", or "projected-unknown". An explicit
    GeoJSON `crs` member is honoured when present, though RFC 7946
    removed it so most modern files omit it.
    """
    named = ((geojson.get("crs") or {}).get("properties") or {}).get("name", "")
    if named:
        low = named.lower()
        if "4326" in low or "crs84" in low:
            return "wgs84"
        if "3857" in low or "900913" in low or "mercator" in low:
            return "web-mercator"

    extent = _coord_extent(geojson)
    if extent is None:
        return "wgs84"
    minx, miny, maxx, maxy = extent
    if abs(minx) <= 180 and abs(maxx) <= 180 and abs(miny) <= 90 and abs(maxy) <= 90:
        return "wgs84"

    # A magnitude check is not enough. UTM eastings/northings (e.g.
    # 750000, 4320000) fall inside Web Mercator's ±20,037,508 range, and a
    # generic plausibility test on the inversion doesn't help either — UTM
    # inverts to roughly (6.7°E, 36.1°N), which is a perfectly valid
    # coordinate somewhere in the Mediterranean. Treating UTM as Mercator
    # would silently mangle the geometry into believable nonsense, which is
    # the worst kind of failure.
    #
    # The discriminator that actually works: we know where this data is.
    # Invert as Web Mercator and check the result lands on Lake Tahoe. If
    # it does, the hypothesis was right. If it lands in the Mediterranean,
    # the file is in some other projection and we should say so rather
    # than guess.
    inverted = [_invert_web_mercator(x, y) for x, y in ((minx, miny), (maxx, maxy))]
    if all(_near_tahoe(lng, lat) for lng, lat in inverted):
        return "web-mercator"
    log.warning("coordinates are projected but do not invert to Lake Tahoe as "
                "Web Mercator (got %s) — unrecognised CRS",
                [(round(lng, 3), round(lat, 3)) for lng, lat in inverted])
    return "projected-unknown"


def reproject_geojson(geojson: dict) -> dict:
    """Return the collection in WGS84, converting from Web Mercator if needed.

    Mutates nothing — the cached file stays exactly as fetched, so a wrong
    guess here is recoverable without re-downloading 7.8 MB.
    """
    crs = detect_crs(geojson)
    if crs == "wgs84":
        return geojson
    if crs == "projected-unknown":
        log.error("polygon file is in an unrecognised projected CRS; "
                  "coordinate extent is %s. Point-in-polygon will not work — "
                  "reproject to EPSG:4326 (the `geo` pixi feature has pyproj).",
                  _coord_extent(geojson))
        return geojson

    log.info("polygon file is Web Mercator; converting to WGS84 for the join")

    def convert(node: Any) -> Any:
        if isinstance(node, (list, tuple)):
            if node and isinstance(node[0], (int, float)) and len(node) >= 2:
                lng, lat = _invert_web_mercator(float(node[0]), float(node[1]))
                return [lng, lat]
            return [convert(child) for child in node]
        return node

    out = dict(geojson)
    out["features"] = [
        {**feat, "geometry": {**(feat.get("geometry") or {}),
                              "coordinates": convert(
                                  (feat.get("geometry") or {}).get("coordinates") or [])}}
        for feat in geojson.get("features", [])
    ]
    return out


def _polygon_centroid(geometry: dict) -> tuple[float, float] | None:
    """Area-weighted centroid of a Polygon or MultiPolygon.

    Uses the shoelace formula on each outer ring and weights by signed
    area, so a MultiPolygon's centroid is pulled toward its larger parts
    rather than sitting midway between them. Interior rings are ignored —
    for a validation check against a stated centroid that's close enough,
    and holes in these catchments are small.
    """
    gtype = geometry.get("type")
    coords = geometry.get("coordinates") or []
    rings = []
    if gtype == "Polygon" and coords:
        rings = [coords[0]]
    elif gtype == "MultiPolygon":
        rings = [part[0] for part in coords if part]
    if not rings:
        return None

    total_area = 0.0
    cx_sum = 0.0
    cy_sum = 0.0
    for ring in rings:
        area = 0.0
        cx = 0.0
        cy = 0.0
        n = len(ring)
        for i in range(n):
            x1, y1 = ring[i][0], ring[i][1]
            x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
            cross = x1 * y2 - x2 * y1
            area += cross
            cx += (x1 + x2) * cross
            cy += (y1 + y2) * cross
        area *= 0.5
        if area == 0:
            continue
        cx /= (6.0 * area)
        cy /= (6.0 * area)
        total_area += area
        cx_sum += cx * area
        cy_sum += cy * area

    if total_area == 0:
        return None
    return (cx_sum / total_area, cy_sum / total_area)


def validate_reprojection(polygons: dict, tolerance_km: float = 3.0) -> dict:
    """Check the reprojection against the file's own stated centroids.

    The polygon file is a gift here: it stores geometry in Web Mercator
    metres but *also* records each catchment's ``Latitude``/``Longitude``
    in WGS84 degrees. So the conversion can be verified against ground
    truth shipped in the same file, rather than against my assumption that
    the inverse formula is right.

    The tolerance is generous because a stated centroid may be computed
    differently — a bounding-box centre, or a pole of inaccessibility
    rather than an area-weighted centroid — and for these catchments those
    can differ by a kilometre or two without anything being wrong.
    """
    wgs = reproject_geojson(polygons)
    checked = 0
    failures: list[tuple[str, float]] = []
    worst = 0.0
    worst_name = ""

    for feat in wgs.get("features", []):
        props = feat.get("properties") or {}
        stated_lat, stated_lng = props.get("Latitude"), props.get("Longitude")
        if stated_lat is None or stated_lng is None:
            continue
        centroid = _polygon_centroid(feat.get("geometry") or {})
        if centroid is None:
            continue
        lng, lat = centroid
        # Rough local scaling: 1 deg lat ~111.32 km, 1 deg lng ~111.32*cos(lat)
        dy = (lat - float(stated_lat)) * 111.32
        dx = (lng - float(stated_lng)) * 111.32 * math.cos(math.radians(float(stated_lat)))
        dist = math.hypot(dx, dy)
        checked += 1
        if dist > worst:
            worst, worst_name = dist, _feature_name(feat) or "?"
        if dist > tolerance_km:
            failures.append((_feature_name(feat) or "?", dist))

    return {"checked": checked, "failures": failures,
            "worst_km": worst, "worst_name": worst_name,
            "tolerance_km": tolerance_km}


def inspect_reference(out_dir: Path = REFERENCE_DIR) -> int:
    """Report what the cached reference files actually contain.

    Written after the first catchment-join matched 0 of 28 stations. A
    systematic miss like that is a coordinate-system or property-name
    mismatch, not a geometry bug, and both are visible from the file.
    """
    ref = load_reference(out_dir)
    polygons = ref.get("polygons")
    if not polygons:
        log.error("no cached polygons — run `pixi run reference` first")
        return 1

    feats = polygons.get("features", [])
    print(f"\n  type      {polygons.get('type')}")
    print(f"  features  {len(feats)}")

    crs = detect_crs(polygons)
    extent = _coord_extent(polygons)
    print(f"  detected CRS  {crs}")
    if extent:
        print(f"  x range   {extent[0]:,.4f} .. {extent[2]:,.4f}")
        print(f"  y range   {extent[1]:,.4f} .. {extent[3]:,.4f}")
    if crs == "web-mercator":
        wgs = reproject_geojson(polygons)
        e = _coord_extent(wgs)
        if e:
            print(f"  -> as WGS84  lng {e[0]:.4f} .. {e[2]:.4f}"
                  f"   lat {e[1]:.4f} .. {e[3]:.4f}")
            print("     (Lake Tahoe is about lng -120.25..-119.90, lat 38.90..39.28)")

    # Geometry types present — a GeometryCollection would need handling.
    kinds: dict[str, int] = {}
    for f in feats:
        k = (f.get("geometry") or {}).get("type") or "None"
        kinds[k] = kinds.get(k, 0) + 1
    print(f"  geometry types  {kinds}")

    # Property keys, so the name lookup can be corrected if needed.
    if feats:
        props = feats[0].get("properties") or {}
        print(f"\n  properties on feature 0 ({len(props)} keys):")
        for k, v in list(props.items())[:25]:
            sv = repr(v)
            print(f"    {k:22} {sv[:60]}")
        named = _feature_name(feats[0])
        print(f"\n  name resolved by _feature_name: {named!r}")
        if named is None:
            print("    -> no recognised name key. Add the right one to")
            print("       _feature_name so attributes can be matched.")

    # Verify the reprojection against the centroids the file itself
    # states in degrees — ground truth shipped alongside the geometry.
    if crs == "web-mercator":
        v = validate_reprojection(polygons)
        if v["checked"]:
            print(f"\n  reprojection check: {v['checked']} catchments have a "
                  f"stated centroid")
            print(f"    worst offset  {v['worst_km']:.2f} km  ({v['worst_name']})")
            if v["failures"]:
                print(f"    {len(v['failures'])} beyond {v['tolerance_km']} km:")
                for name, d in v["failures"][:5]:
                    print(f"      {name:44} {d:.2f} km")
                print("    -> the inverse transform may be wrong, or these")
                print("       centroids are computed differently upstream.")
            else:
                print(f"    all within {v['tolerance_km']} km — the Web Mercator")
                print("       inversion is verified against the file's own")
                print("       stated coordinates, not just assumed correct.")

    # Do the polygon names line up with the attribute records?
    attr_names = set(_attribute_index(ref.get("attributes") or {}))
    poly_names = {n for n in (_feature_name(f) for f in feats) if n}
    print(f"\n  polygon names   {len(poly_names)}")
    print(f"  attribute names {len(attr_names)}")
    both = poly_names & attr_names
    print(f"  matching        {len(both)}")
    if poly_names and attr_names and not both:
        print("    -> names don't overlap at all. Sample of each:")
        print(f"       polygon:   {sorted(poly_names)[:3]}")
        print(f"       attribute: {sorted(attr_names)[:3]}")
    print()
    return 0


# ---------------------------------------------------------------------------
# Point in polygon
# ---------------------------------------------------------------------------

def _ring_contains(lng: float, lat: float, ring: list) -> bool:
    """Ray-casting test for a point inside a single linear ring.

    Counts how many times a ray east from the point crosses the ring's
    edges; odd means inside. The ``(y1 > lat) != (y2 > lat)`` guard also
    handles the case where the ray passes exactly through a vertex, which
    a naive implementation double-counts.
    """
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > lat) != (y2 > lat):
            # Longitude where edge crosses this latitude.
            x_cross = x1 + (lat - y1) / (y2 - y1) * (x2 - x1)
            if lng < x_cross:
                inside = not inside
    return inside


def _geometry_contains(lng: float, lat: float, geometry: dict) -> bool:
    """Point-in-polygon for GeoJSON Polygon and MultiPolygon.

    Interior rings (holes) are honoured: a point inside an outer ring but
    also inside a hole is outside the polygon.
    """
    gtype = geometry.get("type")
    coords = geometry.get("coordinates") or []

    def in_polygon(rings: list) -> bool:
        if not rings:
            return False
        if not _ring_contains(lng, lat, rings[0]):
            return False
        return not any(_ring_contains(lng, lat, hole) for hole in rings[1:])

    if gtype == "Polygon":
        return in_polygon(coords)
    if gtype == "MultiPolygon":
        return any(in_polygon(part) for part in coords)
    return False


def _bbox(geometry: dict) -> tuple[float, float, float, float] | None:
    """Bounding box of a geometry, for a cheap pre-filter."""
    coords = geometry.get("coordinates") or []
    xs: list[float] = []
    ys: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, (list, tuple)):
            if node and isinstance(node[0], (int, float)) and len(node) >= 2:
                xs.append(float(node[0]))
                ys.append(float(node[1]))
            else:
                for child in node:
                    walk(child)

    walk(coords)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def catchment_for_point(lat: float, lng: float, polygons: dict) -> dict | None:
    """The catchment feature containing a point, or None.

    Bounding boxes are checked first because most candidates are ruled out
    that way, and the ring test is the expensive part. If a point falls in
    more than one catchment — possible with overlapping or nested
    boundaries — the smallest by bounding-box area wins, on the assumption
    that the more specific catchment is the intended one.
    """
    matches: list[tuple[float, dict]] = []
    for feat in polygons.get("features", []):
        geom = feat.get("geometry") or {}
        box = _bbox(geom)
        if not box:
            continue
        minx, miny, maxx, maxy = box
        if not (minx <= lng <= maxx and miny <= lat <= maxy):
            continue
        if _geometry_contains(lng, lat, geom):
            area = (maxx - minx) * (maxy - miny)
            matches.append((area, feat))
    if not matches:
        return None
    matches.sort(key=lambda m: m[0])
    return matches[0][1]


# ---------------------------------------------------------------------------
# The join
# ---------------------------------------------------------------------------

def _attribute_index(attributes: dict) -> dict[str, dict]:
    """Index catchment attribute records by name."""
    out: dict[str, dict] = {}
    for rec in (attributes or {}).get("watersheds", []):
        name = rec.get("Name")
        if name:
            out[name] = rec
    return out


def _feature_name(feature: dict) -> str | None:
    props = feature.get("properties") or {}
    for key in ("Name", "name", "NAME", "Watershed", "watershed"):
        if props.get(key):
            return str(props[key])
    return None


def join_stations(
    stations: Iterable[dict],
    reference: dict,
    variables: Iterable[str] = WATERSHED_DISPLAY_VARIABLES,
) -> dict[str, dict]:
    """Attach catchment attributes to stations by containment.

    ``stations`` is any iterable of dicts carrying ``site``, ``lat`` and
    ``lng`` — the map-point records from the transform layer fit directly.

    Returns ``{site: {catchment, attributes, excluded_reason}}``. Sites
    that fall outside every catchment are reported rather than dropped,
    because that is usually informative: a station outside the basin
    boundary is either mislocated or genuinely out of basin, and both are
    worth knowing.
    """
    polygons = reproject_geojson(reference.get("polygons") or {})
    attr_index = _attribute_index(reference.get("attributes") or {})
    wanted = list(variables)

    out: dict[str, dict] = {}
    for st in stations:
        site = st.get("site")
        lat, lng = st.get("lat"), st.get("lng")
        if not site or lat is None or lng is None:
            continue

        feat = catchment_for_point(float(lat), float(lng), polygons)
        if feat is None:
            out[site] = {"catchment": None,
                         "note": "falls outside every catchment polygon"}
            continue

        name = _feature_name(feat)
        # The polygon file carries all 164 attributes in its own
        # properties, so the separate attributes endpoint is a convenience
        # rather than a requirement. Prefer it when present (it's the
        # canonical flat table) and fall back to the embedded properties,
        # which means the join works from the GeoJSON alone.
        rec = attr_index.get(name or "") or (feat.get("properties") or {})
        attrs = {k: rec[k] for k in wanted if k in rec}

        entry: dict = {"catchment": name, "attributes": attrs}
        if name in WATERSHED_EXCLUDE:
            entry["excluded_reason"] = WATERSHED_EXCLUDE[name]
        out[site] = entry

    return out


def report_join(stations: Iterable[dict], reference: dict) -> int:
    """Print the station-to-catchment join. Writes nothing.

    The verification step: confirms the polygons parse, that stations land
    where they should, and shows the attributes that would be attached.
    """
    polygons = reference.get("polygons") or {}
    n_features = len(polygons.get("features", []))
    if not n_features:
        log.error("no catchment polygons cached — run `pixi run reference` first")
        return 1

    joined = join_stations(stations, reference)
    print(f"\n  {n_features} catchment polygons, {len(joined)} stations joined\n")

    unmatched = []
    for site, info in sorted(joined.items()):
        if not info.get("catchment"):
            unmatched.append(site)
            continue
        attrs = info["attributes"]
        precip = attrs.get("PrecipAvg")
        twi = attrs.get("TWID8Avg")
        meadow = attrs.get("Meadow")
        dwater = attrs.get("DWaterAvg")
        print(f"  {site:20} -> {info['catchment']}")
        bits = []
        if precip is not None:
            bits.append(f"precip {precip:.0f} mm")
        if twi is not None:
            bits.append(f"TWI {twi:.1f}")
        if meadow is not None:
            bits.append(f"meadow {meadow*100:.1f}%")
        if dwater is not None:
            bits.append(f"water table {dwater:.0f} cm")
        if bits:
            print(f"  {'':20}    {' · '.join(bits)}")
        if info.get("excluded_reason"):
            print(f"  {'':20}    NOTE: {info['excluded_reason']}")

    if unmatched:
        print(f"\n  {len(unmatched)} station(s) outside every catchment:")
        for site in unmatched:
            print(f"    {site}")
        if len(unmatched) == len(joined):
            print("\n  EVERY station missed. That is systematic, not a set of")
            print("  genuinely out-of-basin stations — almost certainly a")
            print("  coordinate-system or property-name mismatch.")
            print("  Run `pixi run reference-inspect` to see which.")
        else:
            print("\n  Either a mislocated station or a genuinely out-of-basin")
            print("  one. Both worth checking.")
    print()
    return 0
