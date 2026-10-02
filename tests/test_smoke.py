"""HMS smoke: parsing both density formats, the lake test, and missing days."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

import secchi.sources.smoke as H

# A square around the whole lake, and one well away from it.
OVER = "-120.30,38.80 -119.80,38.80 -119.80,39.40 -120.30,39.40 -120.30,38.80"
AWAY = "-118.00,36.00 -117.50,36.00 -117.50,36.50 -118.00,36.50 -118.00,36.00"


def kml(*folders):
    body = ""
    for name, placemarks in folders:
        pms = "".join(f"""<Placemark><description>{d}</description><Polygon>
            <outerBoundaryIs><LinearRing><coordinates>{c}</coordinates></LinearRing></outerBoundaryIs>
            {f'<innerBoundaryIs><LinearRing><coordinates>{h}</coordinates></LinearRing></innerBoundaryIs>' if h else ''}
            </Polygon></Placemark>""" for d, c, h in placemarks)
        body += f"<Folder><name>{name}</name>{pms}</Folder>"
    return f'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>{body}</Document></kml>'


def test_density_is_read_from_the_folder_and_the_densest_wins():
    text = kml(("Smoke (Light)", [("Density: Light", OVER, None)]),
               ("Smoke (Heavy)", [("Density: Heavy", OVER, None)]))
    assert H.density_over_lake(H.parse_kml(text)) == 3


def test_older_numeric_densities_are_understood():
    text = kml(("Smoke", [("Smoke Attributes: Density: 16 Satellite: GOES-WEST", OVER, None)]))
    assert H.density_over_lake(H.parse_kml(text)) == 2


def test_smoke_elsewhere_is_not_smoke_over_the_lake():
    text = kml(("Smoke (Heavy)", [("Density: Heavy", AWAY, None)]))
    assert H.density_over_lake(H.parse_kml(text)) == 0


def test_a_hole_over_the_lake_leaves_it_clear():
    hole = "-120.20,38.90 -119.90,38.90 -119.90,39.30 -120.20,39.30 -120.20,38.90"
    text = kml(("Smoke (Medium)", [("Density: Medium", OVER, hole)]))
    assert H.density_over_lake(H.parse_kml(text)) == 0


def test_a_polygon_without_any_density_fails_loudly():
    text = kml(("Smoke", [("no attributes here", OVER, None)]))
    with pytest.raises(H.SmokeShapeError):
        H.parse_kml(text)


def test_an_empty_day_is_a_clear_day():
    assert H.density_over_lake(H.parse_kml(kml())) == 0


def test_a_missing_file_is_recorded_as_not_analysed(tmp_path, monkeypatch):
    import httpx
    files = {H.url_for(date(2025, 8, 1)): kml(("Smoke (Light)", [("Density: Light", OVER, None)])),
             # A server's "not found" page served with HTTP 200 must not become a clear day.
             H.url_for(date(2025, 8, 3)): "<html><body><h1>Not Found</h1></body></html>"}

    class Resp:
        def __init__(self, text):
            self.text, self.status_code = text, 200 if text is not None else 404
        def raise_for_status(self): pass

    class Client:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url): return Resp(files.get(url))

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr(H, "PROCESSED_DIR", tmp_path)
    H.ingest(since=date(2025, 8, 1), until=date(2025, 8, 3), pause=0)

    from secchi.store import read_partitions
    df = read_partitions(tmp_path / "smoke_observations")
    got = {(r.timestamp.strftime("%m-%d"), r.variable): r.value for r in df.itertuples()}
    assert got[("08-01", "hms_analysed")] == 1 and got[("08-01", "smoke_density")] == 1
    assert got[("08-02", "hms_analysed")] == 0
    assert ("08-02", "smoke_density") not in got          # unknown, not "clear"
    assert got[("08-01", "hms_polygons")] == 1
    assert got[("08-03", "hms_analysed")] == 0            # an HTML page is not an analysis
    assert ("08-03", "smoke_density") not in got
