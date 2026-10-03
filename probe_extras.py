"""Throwaway probe, round 5: soils and terrain at each forest station (read-only)."""
import json, math, time, urllib.parse, urllib.request
UA = {"User-Agent": "secchi (+https://github.com/bdgroves/secchi)"}
SITES = {
    "Glenbrook 1": (39.0881, -119.9391), "Glenbrook 2": (39.08588889, -119.9220278),
    "Glenbrook 4": (39.09344, -119.9015), "Glenbrook 5": (39.07463889, -119.89125),
    "Homewood": (39.0752101, -120.1842931), "Blackwood 2": (39.111167, -120.186889),
    "UNR Tahoe Campus": (39.243064, -119.940336),
}
def get(url, data=None, headers=None, timeout=60):
    h = dict(UA); h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")
def elev(lat, lng):
    for _ in range(2):
        try:
            j = json.loads(get(f"https://epqs.nationalmap.gov/v1/json?x={lng}&y={lat}&units=Meters&wkid=4326&includeDate=false", timeout=20))
            return float(j["value"])
        except Exception:
            time.sleep(1)
    return None
from concurrent.futures import ThreadPoolExecutor
POOL = ThreadPoolExecutor(8)
def elevs(points):
    return list(POOL.map(lambda p: elev(*p), points))
def offset(lat, lng, dn, de):
    return lat + dn / 111320, lng + de / (111320 * math.cos(math.radians(lat)))
out = {}
for site, (lat, lng) in SITES.items():
    r = {"lat": lat, "lng": lng}
    # --- terrain: 30 m neighbours for slope/aspect, 150 m and 300 m rings for position
    pts = [(lat, lng)] + [offset(lat, lng, dn, de) for dn, de in ((30, 0), (-30, 0), (0, 30), (0, -30))]
    rings = {rad: [offset(lat, lng, rad * math.cos(a), rad * math.sin(a)) for a in [k * math.pi / 4 for k in range(8)]] for rad in (150, 300)}
    vals = elevs(pts + rings[150] + rings[300])
    z = vals[0]; r["elev_m"] = z
    nb = dict(zip(["n", "s", "e", "w"], vals[1:5]))
    ringvals = {150: vals[5:13], 300: vals[13:21]}
    try:
        dzdx = (nb["e"] - nb["w"]) / 60; dzdy = (nb["n"] - nb["s"]) / 60
        r["slope_deg"] = round(math.degrees(math.atan(math.hypot(dzdx, dzdy))), 1)
        r["aspect_deg"] = round((math.degrees(math.atan2(-dzdx, -dzdy)) + 360) % 360)  # direction it faces
    except Exception as e:
        r["slope_err"] = repr(e)
    for rad in (150, 300):
        ring = [v for v in ringvals[rad] if v is not None]
        if z is not None and ring:
            r[f"tpi_{rad}m"] = round(z - sum(ring) / len(ring), 1)
    # --- soils: NRCS Soil Data Access at the point
    sql = f"""SELECT mu.mukey, mu.muname, ma.drclassdcd, ma.hydgrpdcd, ma.wtdepannmin, ma.brockdepmin,
                     ma.aws0150wta, ma.flodfreqdcd, ma.slopegraddcp
              FROM mapunit mu INNER JOIN muaggatt ma ON ma.mukey = mu.mukey
              WHERE mu.mukey IN (SELECT * FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('point({lng} {lat})'))"""
    try:
        t = get("https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest",
                data=json.dumps({"query": sql, "format": "JSON+COLUMNNAME"}).encode(),
                headers={"Content-Type": "application/json"})
        j = json.loads(t)
        rows = j.get("Table", [])
        r["soil"] = dict(zip(rows[0], rows[1])) if len(rows) > 1 else {"raw": t[:300]}
    except Exception as e:
        r["soil_err"] = repr(e)
    # --- nearest mapped stream: NHD high-res flowlines within 1 km
    try:
        q = {"geometry": f"{lng},{lat}", "geometryType": "esriGeometryPoint", "inSR": "4326",
             "distance": "1000", "units": "esriSRUnit_Meter", "spatialRel": "esriSpatialRelIntersects",
             "outFields": "gnis_name,ftype,fcode", "returnGeometry": "true", "outSR": "4326", "f": "json"}
        t = get("https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/6/query?" + urllib.parse.urlencode(q))
        j = json.loads(t)
        best = None
        for f in j.get("features", []):
            for path in f["geometry"].get("paths", []):
                for (x, y) in path:
                    dn = (y - lat) * 111320; de = (x - lng) * 111320 * math.cos(math.radians(lat))
                    dist = math.hypot(dn, de)
                    if best is None or dist < best[0]:
                        best = (dist, f["attributes"].get("gnis_name"), f["attributes"].get("fcode"))
        r["nearest_stream_m"] = None if best is None else round(best[0])
        r["nearest_stream"] = None if best is None else {"name": best[1], "fcode": best[2]}
        r["streams_within_1km"] = len(j.get("features", []))
    except Exception as e:
        r["nhd_err"] = repr(e)
    out[site] = r
    print(site, json.dumps(r), flush=True)
json.dump(out, open("probe_out/stations_ground.json", "w"), indent=1)
