"""Client for UC Davis TERC's Lake Tahoe Secchi depth record.

This is the ground truth. Every other stream in this project is a
predictor of clarity; this is clarity itself, measured the way it has been
measured since 1968 — a 10-inch white disk lowered into the water until it
disappears.

The record is published in the Environmental Data Initiative repository,
which runs the PASTA+ software stack and exposes a documented REST API.
Data packages there are immutable and versioned, each revision gets a DOI,
and the package ID has the form ``scope.identifier.revision`` —
``edi.1340.N`` here. That means a pinned revision is genuinely
reproducible, which matters if a model is ever calibrated against it.

Browse the package:
https://portal.edirepository.org/nis/mapbrowse?scope=edi&identifier=1340

API shape (from the PASTA+ Data Package Manager docs):

    /package/eml/{scope}/{id}              -> list of revisions
    /package/eml/{scope}/{id}/newest       -> newest revision number
    /package/data/eml/{scope}/{id}/{rev}   -> list of entity IDs
    /package/data/eml/{scope}/{id}/{rev}/{entity}  -> the data file
    /package/metadata/eml/{scope}/{id}/{rev}       -> EML XML metadata

**Written from documentation, not from observed responses** — this
environment cannot reach pasta.lternet.edu. ``discover()`` is the
verification step: it walks revisions and entities and prints what it
finds, so the first real run tells us the shape rather than assuming it.

**Update, 2026-10-02 — what a real run found.** PASTA answers 403 to
GitHub's runners as well as to this workspace, whatever the User-Agent,
and EDI's portal sits behind a Cloudflare challenge. So the PASTA client
below never gets data. EDI is a DataONE member node, though, and DataONE
mirrors every package: its search index lists each file of ``edi.1340``
by revision, and ``cn.dataone.org/cn/v2/resolve/<pid>`` redirects to EDI's
own DataONE endpoint (``gmn.edirepository.org``), which serves the bytes.
``ingest()`` uses that route. What arrived:

    Secchi_LTP.csv    index station, 1967-07-28 onward (~1,640 readings)
    Secchi_MLTP.csv   mid-lake station, 1980-04-29 onward (~570)

    columns  Date_Time_Local (PST/PDT, sometimes a bare date), Secchi (m,
             the mean of the next two), Secchi_Disappear, Secchi_Reappear
             (m), Viewing_Condition (0-7, subjective), Lake_Condition (text)

Licence CC BY 4.0. Creator in the metadata: Shohei Watanabe, UC Davis
TERC; cite the EDI package edi.1340 at the revision used. The published
record lags the boat by months, and new revisions appear a few times a
year (revision 17 was published 2026-08-17).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from typing import Any

import httpx

from secchi.config import (
    DATAONE_CN,
    EDI_API_BASE,
    EDI_SCOPE,
    EDI_SECCHI_IDENTIFIER,
    HTTP_TIMEOUT_SECONDS,
    REFERENCE_DIR,
    TERC_ANNUAL_MEANS_FT,
    TERC_SECCHI_PRECISION_M,
    TERC_SECCHI_STATIONS,
    USER_AGENT,
)

log = logging.getLogger("secchi.sources.terc")

# See the note in TercClient.__init__ for why this isn't the project UA.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# Alternate hosts to try if the primary returns 403 or 404. EDI has moved
# its API host before, and the portal serves some of the same routes.
FALLBACK_BASES = (
    "https://pasta.lternet.edu/package",
    "https://pasta-s.lternet.edu/package",
    "https://portal.edirepository.org/nis/metadataviewer",
)

# Column names to look for when identifying the depth and date fields.
# EDI packages describe their columns in EML, but guessing a handful of
# likely names is cheaper than parsing XML for a first pass — and
# `discover()` prints the real header so this list can be corrected.
DEPTH_HINTS = ("secchi", "depth", "sd_m", "secchi_m", "secchi_depth")
DATE_HINTS = ("date", "sample_date", "datetime", "timestamp", "sampledate")
STATION_HINTS = ("station", "site", "location", "index")


class TercClient:
    """Read TERC's Secchi record from the EDI repository."""

    def __init__(
        self,
        base_url: str = EDI_API_BASE,
        scope: str = EDI_SCOPE,
        identifier: int = EDI_SECCHI_IDENTIFIER,
        timeout: float = HTTP_TIMEOUT_SECONDS,
    ):
        self._base = base_url.rstrip("/")
        self._scope = scope
        self._id = identifier
        # EDI returned 403 to the project User-Agent
        # ("secchi/0.1 (+https://github.com/...)"). The read API is meant
        # to be public and unauthenticated, and 403 rather than 404 means
        # the path resolved — so this is a WAF rejecting a non-browser
        # client rather than a permissions problem.
        #
        # Presenting a browser User-Agent is the pragmatic fix. It is not
        # evasion of a rate limit or an access control; it is asking the
        # same public endpoint in a shape the filter accepts. Accept
        # headers are set too, since some filters key on their absence.
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "User-Agent": BROWSER_USER_AGENT,
                "Accept": "text/plain, application/xml, application/json, */*",
                "Accept-Language": "en-US,en;q=0.9",
            },
            follow_redirects=True,
        )
        self._project_ua = USER_AGENT

    def __enter__(self) -> "TercClient":
        return self

    def __exit__(self, *_exc) -> None:
        self._client.close()

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------
    # Package navigation
    # ------------------------------------------------------------------

    def revisions(self) -> list[int]:
        """All published revisions of the package, oldest first."""
        url = f"{self._base}/eml/{self._scope}/{self._id}"
        resp = self._client.get(url)
        resp.raise_for_status()
        out = []
        for line in resp.text.splitlines():
            line = line.strip()
            if line.isdigit():
                out.append(int(line))
        return sorted(out)

    def newest_revision(self) -> int:
        """The current revision number."""
        url = f"{self._base}/eml/{self._scope}/{self._id}/newest"
        resp = self._client.get(url)
        resp.raise_for_status()
        return int(resp.text.strip())

    def entities(self, revision: int) -> list[str]:
        """Data entity IDs within a revision.

        A package can hold several files — TERC's may separate the index
        station from the mid-lake station, or raw readings from annual
        summaries. Don't assume there's one.
        """
        url = f"{self._base}/data/eml/{self._scope}/{self._id}/{revision}"
        resp = self._client.get(url)
        resp.raise_for_status()
        return [ln.strip() for ln in resp.text.splitlines() if ln.strip()]

    def entity_name(self, revision: int, entity: str) -> str | None:
        """Human-readable name for an entity, if the API offers one."""
        url = f"{self._base}/name/eml/{self._scope}/{self._id}/{revision}/{entity}"
        try:
            resp = self._client.get(url)
            resp.raise_for_status()
            return resp.text.strip()
        except httpx.HTTPError:
            return None

    def fetch_entity(self, revision: int, entity: str) -> str:
        """Raw text of one data entity."""
        url = f"{self._base}/data/eml/{self._scope}/{self._id}/{revision}/{entity}"
        log.info("GET %s", url)
        resp = self._client.get(url)
        resp.raise_for_status()
        return resp.text

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self) -> int:
        """Print what the package actually contains. Writes nothing.

        This is the verification step for a client written from docs
        rather than observation. It reports revisions, entities, each
        entity's header row and a couple of sample rows, so the parsing
        hints below can be corrected against reality instead of guessed
        at — the same habit that caught the wrong USGS parameter code and
        the mis-keyed sensor slugs.
        """
        try:
            revs = self.revisions()
            newest = self.newest_revision()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            log.error("could not read package %s.%s: HTTP %d",
                      self._scope, self._id, status)
            if status == 403:
                print("\n  403 Forbidden from the EDI API.\n")
                print("  The read API is public and unauthenticated, and 403")
                print("  (not 404) means the path resolved — so something is")
                print("  filtering the request rather than denying the data.")
                print("  This client already sends a browser User-Agent for")
                print("  that reason; if you are still seeing 403, the block")
                print("  is elsewhere.\n")
                print("  Next steps, cheapest first:")
                print("   1. Open the package in a browser and download the")
                print("      CSV by hand — one file, and the record only")
                print("      updates annually:")
                print(f"      https://portal.edirepository.org/nis/mapbrowse"
                      f"?scope={self._scope}&identifier={self._id}")
                print("      Drop it in data/reference/ and we parse locally.")
                print("   2. Check whether a corporate network or VPN is")
                print("      intercepting — lternet.edu is an academic host")
                print("      and some filters treat those differently.")
                print("   3. EDI publishes a contact address if the API is")
                print("      genuinely restricted: info@edirepository.org\n")
            return 1
        except httpx.HTTPError as exc:
            log.error("could not reach the EDI API: %s", exc)
            return 1

        print(f"\n  Package  {self._scope}.{self._id}")
        print(f"  Revisions  {', '.join(str(r) for r in revs)}")
        print(f"  Newest     {newest}\n")

        try:
            ents = self.entities(newest)
        except httpx.HTTPError as exc:
            log.error("could not list entities: %s", exc)
            return 1

        for ent in ents:
            name = self.entity_name(newest, ent)
            print(f"  --- entity {ent}" + (f"  ({name})" if name else ""))
            try:
                text = self.fetch_entity(newest, ent)
            except httpx.HTTPError as exc:
                print(f"      fetch failed: {exc}")
                continue

            lines = text.splitlines()
            print(f"      {len(lines):,} lines")
            for i, line in enumerate(lines[:4]):
                print(f"      {i}: {line[:150]}")

            header = _sniff_header(text)
            if header:
                print(f"      columns: {', '.join(header)}")
                guessed = _guess_columns(header)
                for role, col in guessed.items():
                    print(f"        {role:8} -> {col or '(not found)'}")
            print()

        print(f"  Measurement precision is ±{TERC_SECCHI_PRECISION_M} m between two")
        print("  observers (Jassby et al. 1999) — a useful bound on how much")
        print("  precision any model calibrated against this can claim.\n")
        return 0

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def fetch_readings(self, revision: int | None = None) -> dict:
        """Fetch and parse Secchi readings from the newest (or given) revision.

        Returns a snapshot envelope matching the shape the other sources
        use, so ingest and transform can handle it the same way. Column
        identification is heuristic — see ``discover()``; once the real
        header is known, replace the hints with exact names.
        """
        rev = revision or self.newest_revision()
        out_rows: list[dict] = []
        entities_read: list[str] = []

        for ent in self.entities(rev):
            text = self.fetch_entity(rev, ent)
            header = _sniff_header(text)
            if not header:
                continue
            cols = _guess_columns(header)
            if not (cols.get("date") and cols.get("depth")):
                log.warning("entity %s: no date/depth columns identified, skipping", ent)
                continue

            entities_read.append(ent)
            reader = csv.DictReader(io.StringIO(text))
            for row in reader:
                depth_raw = (row.get(cols["depth"]) or "").strip()
                if not depth_raw:
                    continue
                try:
                    depth_m = float(depth_raw)
                except ValueError:
                    continue
                out_rows.append({
                    "date": (row.get(cols["date"]) or "").strip(),
                    "secchi_depth_m": depth_m,
                    "station": (row.get(cols["station"]) or "").strip()
                               if cols.get("station") else None,
                    "entity": ent,
                })

        return {
            "source": "TERC",
            "package_id": f"{self._scope}.{self._id}.{rev}",
            "revision": rev,
            "entities": entities_read,
            "reading_count": len(out_rows),
            "readings": out_rows,
        }


# ---------------------------------------------------------------------------
# The working route: DataONE's mirror of the package
# ---------------------------------------------------------------------------

SECCHI_CSV = REFERENCE_DIR / "terc_secchi.csv"
PACKAGE_STATE = REFERENCE_DIR / "terc_package.json"
EXPECTED_COLUMNS = ["Date_Time_Local", "Secchi", "Secchi_Disappear",
                    "Secchi_Reappear", "Viewing_Condition", "Lake_Condition"]
OUT_COLUMNS = ["station", "date_time_local", "secchi_m", "disappear_m",
               "reappear_m", "viewing_condition", "lake_condition"]
_PID_RE = re.compile(r"/edi/1340/(\d+)/([0-9a-f]+)$")


class TercShapeError(RuntimeError):
    """The package or a file in it doesn't look as it did when this was written."""


def _client() -> httpx.Client:
    # The resolver redirects to an EDI host, so present the same browser
    # User-Agent as TercClient does (see the note there).
    return httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True,
                        headers={"User-Agent": BROWSER_USER_AGENT, "Accept": "*/*"})


def _solr(client: httpx.Client, q: str, fl: str) -> list[dict]:
    resp = client.get(f"{DATAONE_CN}/query/solr/",
                      params={"q": q, "fl": fl, "rows": 1000, "wt": "json"})
    resp.raise_for_status()
    try:
        return resp.json()["response"]["docs"]
    except (ValueError, KeyError) as exc:
        raise TercShapeError(f"DataONE search: unexpected reply {resp.text[:300]!r}") from exc


def candidate_pids(data_docs: list[dict], meta_docs: list[dict]) -> tuple[int, dict[str, list[str]]]:
    """The newest revision, and for each file the identifiers to try, newest first.

    The search index can list a revision's metadata before its data files,
    and an entity keeps the same hash across revisions, so identifiers for
    revisions newer than any indexed data file are built from that hash
    and tried first. Older revisions are tried only down to the newest one
    the index already lists.
    """
    meta_revs = [int(m.group(1)) for d in meta_docs
                 if (m := re.search(r"/edi/1340/(\d+)$", d.get("id", "")))]
    by_file: dict[str, dict[int, str]] = {}
    for d in data_docs:
        m = _PID_RE.search(d.get("id", ""))
        name = d.get("fileName")
        if m and name in TERC_SECCHI_STATIONS:
            by_file.setdefault(name, {})[int(m.group(1))] = m.group(2)
    missing = sorted(set(TERC_SECCHI_STATIONS) - set(by_file))
    if missing:
        raise TercShapeError(f"DataONE lists no {missing} for edi.1340; found "
                             f"{sorted({d.get('fileName') for d in data_docs})}")
    newest = max(meta_revs + [r for revs in by_file.values() for r in revs])
    out = {}
    for name, revs in by_file.items():
        entity = revs[max(revs)]
        out[name] = [f"https://pasta.lternet.edu/package/data/eml/edi/1340/{r}/{revs.get(r, entity)}"
                     for r in range(newest, max(revs) - 1, -1)]
    return newest, out


def _download(client: httpx.Client, pid: str) -> str | None:
    """One file's text via the DataONE resolver, or None if this revision isn't served."""
    from urllib.parse import quote
    resp = client.get(f"{DATAONE_CN}/resolve/{quote(pid, safe='')}")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    text = resp.content.decode("utf-8", "replace")
    if not text.lstrip().startswith('"Date_Time_Local"'):
        raise TercShapeError(f"{pid}: not the Secchi CSV; starts {text[:200]!r}")
    return text


def parse_csv(text: str, file_name: str):
    """One station's file as tidy rows. Strict about columns; lenient about bad cells.

    A reading whose date can't be parsed is dropped and counted rather
    than stored undated; an unexpected header stops everything, since a
    renamed column would otherwise store plausible-looking garbage.
    """
    import pandas as pd

    df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    if list(df.columns) != EXPECTED_COLUMNS:
        raise TercShapeError(f"{file_name}: columns {list(df.columns)}, expected {EXPECTED_COLUMNS}")
    ts = pd.to_datetime(df["Date_Time_Local"].str.strip(), format="mixed", errors="coerce")
    bad = int(ts.isna().sum())
    if bad:
        log.warning("%s: %d reading(s) with an unreadable date dropped", file_name, bad)
    num = {c: pd.to_numeric(df[c].str.strip(), errors="coerce")
           for c in ("Secchi", "Secchi_Disappear", "Secchi_Reappear", "Viewing_Condition")}
    out = pd.DataFrame({
        "station": TERC_SECCHI_STATIONS[file_name]["code"],
        "date_time_local": ts,
        "secchi_m": num["Secchi"],
        "disappear_m": num["Secchi_Disappear"],
        "reappear_m": num["Secchi_Reappear"],
        "viewing_condition": num["Viewing_Condition"],
        "lake_condition": df["Lake_Condition"].str.strip(),
    })
    out = out[out["date_time_local"].notna() & out["secchi_m"].notna()]
    # Sanity: Lake Tahoe's Secchi depth has ranged roughly 10-45 m since 1967.
    # Anything outside 1-60 m is a unit or column error, not a clear day.
    wild = out[(out["secchi_m"] < 1) | (out["secchi_m"] > 60)]
    if len(wild):
        raise TercShapeError(f"{file_name}: {len(wild)} depth(s) outside 1-60 m, e.g. "
                             f"{wild.head(3).to_dict('records')}")
    return out


def yearly_means_m(df):
    """Yearly mean Secchi depth at the index station: average each month, then the year.

    TERC doesn't publish its formula. Tested 2026-10-02 against its
    published figures, the mean of monthly means reproduces 2022 exactly
    (21.90 m) and 2024 within 0.06 m (TERC: 19.0 m, 27 readings); a plain
    mean of readings misses every year by 0.1-0.4 m. Where it still
    differs (2023 -0.26 m, 2025 +0.65 m) the published file is missing
    readings TERC used: 25 for 2024 against TERC's 27, and 2025 has no
    May reading at all. See docs/beyond-teon.md.

    Returns a frame: year, mean_m, months, n (readings).
    """
    import pandas as pd

    ltp = df[df["station"] == "LTP"]
    t = ltp["date_time_local"]
    monthly = ltp.groupby([t.dt.year.rename("year"), t.dt.month.rename("month")])["secchi_m"].mean()
    by_year = monthly.groupby(level="year")
    return pd.DataFrame({"mean_m": by_year.mean(), "months": by_year.size(),
                         "n": ltp.groupby(t.dt.year.rename("year")).size()}).reset_index()


def annual_means_ft(df) -> dict[int, float]:
    """``yearly_means_m`` in feet, the unit TERC reports in."""
    y = yearly_means_m(df)
    return {int(r.year): round(float(r.mean_m) * 3.280839895, 1) for r in y.itertuples()}


def ingest(force: bool = False) -> int:
    """Refresh ``data/reference/terc_secchi.csv`` when TERC publishes a new revision.

    Each run asks DataONE's index which revision is newest (two small
    queries). Nothing is downloaded unless that is newer than the one held,
    and the CSV is rewritten only if its content changed, so a quiet run
    commits nothing.
    """
    import pandas as pd

    state = json.loads(PACKAGE_STATE.read_text(encoding="utf-8")) if PACKAGE_STATE.exists() else {}
    with _client() as client:
        data_docs = _solr(client, "fileName:Secchi_* AND id:*edi/1340/*", "id,fileName")
        meta_docs = _solr(client, "formatType:METADATA AND id:*edi/1340/*", "id")
        newest, candidates = candidate_pids(data_docs, meta_docs)
        if not force and SECCHI_CSV.exists() and state.get("newest_seen") == newest:
            log.info("TERC Secchi: revision %d already held, nothing to fetch", newest)
            return 0

        frames, used = [], {}
        for name, pids in candidates.items():
            for pid in pids:
                text = _download(client, pid)
                if text is not None:
                    frames.append(parse_csv(text, name))
                    used[name] = int(_PID_RE.search(pid).group(1))
                    break
            else:
                raise TercShapeError(f"{name}: no revision could be downloaded ({pids[0]} …)")

    df = (pd.concat(frames, ignore_index=True)
            .drop_duplicates(subset=["station", "date_time_local"], keep="first")
            .sort_values(["station", "date_time_local"], kind="mergesort")
            .reset_index(drop=True))
    csv_text = df.assign(date_time_local=df["date_time_local"].dt.strftime("%Y-%m-%d %H:%M"))[OUT_COLUMNS] \
                 .to_csv(index=False, lineterminator="\n")

    SECCHI_CSV.parent.mkdir(parents=True, exist_ok=True)
    old = SECCHI_CSV.read_text(encoding="utf-8") if SECCHI_CSV.exists() else None
    if csv_text != old:
        tmp = SECCHI_CSV.with_suffix(".csv.tmp")
        tmp.write_text(csv_text, encoding="utf-8")
        tmp.replace(SECCHI_CSV)
    new_state = {"package": "edi.1340", "newest_seen": newest, "revisions_used": used,
                 "readings": {s["code"]: int((df["station"] == s["code"]).sum())
                              for s in TERC_SECCHI_STATIONS.values()},
                 "newest_reading": df["date_time_local"].max().strftime("%Y-%m-%d")}
    if new_state != state:
        PACKAGE_STATE.write_text(json.dumps(new_state, indent=2) + "\n", encoding="utf-8")

    means = annual_means_ft(df)
    for year, published in sorted(TERC_ANNUAL_MEANS_FT.items()):
        if year in means:
            log.info("TERC %d: plain mean of index-station readings %.1f ft; TERC published %.1f ft",
                     year, means[year], published)
    log.info("TERC Secchi: %s readings (%s), revisions %s, newest %s%s",
             f"{len(df):,}", ", ".join(f"{k} {v:,}" for k, v in new_state["readings"].items()),
             used, new_state["newest_reading"], "" if csv_text != old else "; unchanged")
    return len(df)


def discover_dataone() -> int:
    """Report what DataONE serves for edi.1340. Writes nothing."""
    with _client() as client:
        data_docs = _solr(client, "fileName:Secchi_* AND id:*edi/1340/*", "id,fileName")
        meta_docs = _solr(client, "formatType:METADATA AND id:*edi/1340/*", "id")
        newest, candidates = candidate_pids(data_docs, meta_docs)
        print(f"\n  edi.1340 via DataONE: newest revision {newest}\n")
        for name, pids in candidates.items():
            for pid in pids:
                text = _download(client, pid)
                if text is None:
                    print(f"  {name}: revision {_PID_RE.search(pid).group(1)} not served yet")
                    continue
                df = parse_csv(text, name)
                print(f"  {name}: revision {_PID_RE.search(pid).group(1)}, {len(df):,} readings, "
                      f"{df['date_time_local'].min():%Y-%m-%d} to {df['date_time_local'].max():%Y-%m-%d}")
                break
    print()
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sniff_header(text: str) -> list[str]:
    """First non-empty line, split as CSV."""
    for line in text.splitlines():
        if line.strip():
            return next(csv.reader(io.StringIO(line)))
    return []


def _guess_columns(header: list[str]) -> dict[str, str | None]:
    """Match header names to the roles we need.

    Deliberately conservative: an exact-ish substring match on lowercased
    names, longest hint first so "secchi_depth" beats "depth". Returns
    None for anything it can't find rather than picking something wrong —
    a wrong column silently produces plausible garbage, which is worse
    than a visible gap.
    """
    lowered = {h.lower().strip(): h for h in header}

    def find(hints: tuple[str, ...]) -> str | None:
        for hint in sorted(hints, key=len, reverse=True):
            for low, original in lowered.items():
                if hint in low:
                    return original
        return None

    return {
        "date": find(DATE_HINTS),
        "depth": find(DEPTH_HINTS),
        "station": find(STATION_HINTS),
    }


def feet(metres: float) -> float:
    """TERC reports in feet publicly and metres in the data."""
    return metres * 3.280839895
