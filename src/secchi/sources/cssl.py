"""The Central Sierra Snow Laboratory's snowfall climatology, 1879 onward.

UC Berkeley's Snow Lab sits at Donner Summit, about 2,100 m, on the wet
side of the Sierra crest some 30 km northwest of Homewood. It is outside
the Tahoe basin, so on this site it is context ("Beyond TEON"), never one
of the transect's shores.

Two things come from it:

* **Daily SNOTEL data** (precipitation, temperature, snow water equivalent,
  snow depth) from NRCS SNOTEL site 428, which NRCS lists as "Css Lab".
  That rides along with the other SNOTEL stations in ``sources/snotel.py``.
* **The snowfall climatology**: total snowfall and peak snow depth for
  every water year since 1879, one of the longest snow records anywhere.
  That is this module.

Where the climatology lives, found 2026-10-02 by reading the lab's data
page: the page is a Next.js app whose "Download Data" button reads the
table ``CSSL_SnowClimo`` from a Supabase project with the public,
browser-side key embedded in the page's script. That key is Supabase's
"anon" role — the same read-only access every visitor's browser gets — so
this module fetches the page's scripts, takes the key from them at run
time rather than copying it into the repo (it will rotate), and refuses
any key whose role isn't ``anon``.

The table is 147 rows and changes once a year, so CI asks for it once a
day and the CSV is rewritten only when its content changes. The lab's
minute-by-minute instrument tables (``S2S1_Met_data`` and friends) are
deliberately not read: they are raw logger output only months old (one
temperature probe reads -80 °C), and SNOTEL 428 carries the quality-
controlled daily equivalents.

Daily records 1971-2025 are also on Dryad (doi:10.6078/D1941T, CC0), but
Dryad now refuses anonymous file downloads (401 from its API, a bot
challenge on the web route), so they are not fetched automatically.
"""

from __future__ import annotations

import base64
import json
import logging
import re

import httpx

from secchi.config import HTTP_TIMEOUT_SECONDS, REFERENCE_DIR

log = logging.getLogger("secchi.sources.cssl")

PAGE = "https://cssl.berkeley.edu/data"
TABLE = "CSSL_SnowClimo"
CLIMO_CSV = REFERENCE_DIR / "cssl_snow_climatology.csv"
COLUMNS = {
    "Water Year": "water_year",
    "Snowfall (cm)": "snowfall_cm",
    "Max snow depth (cm)": "max_depth_cm",
    "Snowfall - HS": "snowfall_hs_cm",
}
# NRCS SNOTEL site at the lab (also in sources/snotel.py as "Css Lab").
SNOTEL_NAME = "Css Lab"
LOCATION = {"lat": 39.32565, "lng": -120.36807, "elevation_ft": 6890}

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class CsslShapeError(RuntimeError):
    """The page, the key or the table isn't what it was when this was written."""


def _jwt_role(token: str) -> str | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("role")
    except Exception:  # noqa: BLE001 - anything unreadable is "not anon"
        return None


def find_project(client: httpx.Client) -> tuple[str, str]:
    """(Supabase URL, public anon key) as the lab's own page uses them."""
    page = client.get(PAGE)
    page.raise_for_status()
    scripts = sorted(set(re.findall(r'/_next/static/[^"\']+\.js', page.text)))
    for path in scripts:
        js = client.get("https://cssl.berkeley.edu" + path).text
        url = re.search(r'"(https://[a-z0-9]+\.supabase\.co)"', js)
        if not url:
            continue
        keys = re.findall(r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}', js)
        anon = [k for k in keys if _jwt_role(k) == "anon"]
        if not anon:
            raise CsslShapeError(f"found {url.group(1)} but no anon-role key beside it "
                                 f"({len(keys)} other token(s) ignored)")
        return url.group(1), anon[0]
    raise CsslShapeError(f"no Supabase project in the {len(scripts)} scripts of {PAGE}")


def parse(rows: list[dict]):
    """The table as a tidy frame. Strict about columns and years."""
    import pandas as pd

    if not isinstance(rows, list) or not rows:
        raise CsslShapeError(f"{TABLE}: expected a list of rows, got {str(rows)[:200]}")
    missing = set(COLUMNS) - set(rows[0])
    if missing:
        raise CsslShapeError(f"{TABLE}: columns {sorted(rows[0])}, missing {sorted(missing)}")
    df = pd.DataFrame(rows)[list(COLUMNS)].rename(columns=COLUMNS)
    for c in ("snowfall_cm", "max_depth_cm", "snowfall_hs_cm"):
        # "NA" means not measured (water year 2020 has no snowfall total).
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["water_year"] = pd.to_numeric(df["water_year"], errors="coerce").astype("Int64")
    if df["water_year"].isna().any() or df["water_year"].duplicated().any():
        raise CsslShapeError(f"{TABLE}: missing or repeated water years")
    df = df.sort_values("water_year").reset_index(drop=True)
    if df["water_year"].iloc[0] > 1900 or df["water_year"].iloc[-1] < 2020:
        raise CsslShapeError(f"{TABLE}: spans {df['water_year'].iloc[0]}-"
                             f"{df['water_year'].iloc[-1]}, expected 1879 to recent")
    wild = df[(df["snowfall_cm"] < 0) | (df["snowfall_cm"] > 3000)]
    if len(wild):
        raise CsslShapeError(f"{TABLE}: implausible snowfall {wild.head(3).to_dict('records')}")
    return df


def ingest() -> int:
    """Refresh ``data/reference/cssl_snow_climatology.csv``."""
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True,
                      headers={"User-Agent": BROWSER_USER_AGENT}) as client:
        base, key = find_project(client)
        resp = client.get(f"{base}/rest/v1/{TABLE}", params={"select": "*"},
                          headers={"apikey": key, "Authorization": f"Bearer {key}"})
        resp.raise_for_status()
        df = parse(resp.json())

    text = df.to_csv(index=False, lineterminator="\n")
    old = CLIMO_CSV.read_text(encoding="utf-8") if CLIMO_CSV.exists() else None
    if text != old:
        CLIMO_CSV.parent.mkdir(parents=True, exist_ok=True)
        tmp = CLIMO_CSV.with_suffix(".csv.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(CLIMO_CSV)
    log.info("CSSL climatology: %d water years, %d-%d%s", len(df),
             df["water_year"].iloc[0], df["water_year"].iloc[-1],
             "" if text != old else "; unchanged")
    return len(df)
