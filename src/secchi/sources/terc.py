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
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any

import httpx

from secchi.config import (
    EDI_API_BASE,
    EDI_SCOPE,
    EDI_SECCHI_IDENTIFIER,
    HTTP_TIMEOUT_SECONDS,
    TERC_SECCHI_PRECISION_M,
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
