"""Client for the TEON REST API.

Two calls matter:

- :func:`list_sensors` hits ``/sensors/locations`` and returns the full
  sensor inventory (site, coordinates, first/last update, data count).
- :func:`fetch_sensor` hits ``/sensors/{slug}`` for one (sensor-type, site)
  pair and walks pagination until it either exhausts the query or hits a
  record cap.

The endpoint schemas were reverse-engineered from browser DevTools traffic
on 2026-09-17; if TEON publishes a formal spec, prefer that.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Iterator
from zoneinfo import ZoneInfo

import httpx

from secchi.config import (
    DEFAULT_INGEST_PAGE_SIZE,
    HTTP_TIMEOUT_SECONDS,
    LIVE_WINDOW_HOURS,
    SENSOR_TYPE_CANONICAL,
    SENSOR_TYPE_SLUGS,
    TEON_API_BASE,
    TEON_ENDPOINTS,
    TEON_TIMEZONE,
    USER_AGENT,
)

log = logging.getLogger("secchi.sources.teon")


class VisibilityUnavailable(RuntimeError):
    """The site-visibility list could not be read.

    Its own type so callers can fail closed on this specifically,
    rather than catching everything and hoping.
    """


class TeonClient:
    """Thin wrapper around ``httpx.Client`` scoped to the TEON backend."""

    def __init__(self, base_url: str = TEON_API_BASE, timeout: float = HTTP_TIMEOUT_SECONDS):
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
        )
        # sensor_type display name → confirmed working slug, populated by
        # resolve_slug so we probe each type at most once per session.
        self._slug_cache: dict[str, str] = {}

    def __enter__(self) -> "TeonClient":
        return self

    def __exit__(self, *_exc) -> None:
        self._client.close()

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def list_sensors(self) -> dict[str, Any]:
        """Return the raw ``/sensors/locations`` payload."""
        url = f"{self._base}{TEON_ENDPOINTS['locations']}"
        resp = self._client.get(url)
        resp.raise_for_status()
        return resp.json()

    def iter_inventory(self) -> Iterator[dict[str, Any]]:
        """Flatten :func:`list_sensors` into (category, sensor_type, record) rows.

        Yields dicts with the sensor record plus ``_category`` (lake/stream/
        terrestrial) and ``_sensor_type`` (ExoSensor, MiniDotSensor, ...).
        """
        payload = self.list_sensors()
        locations = payload.get("locations", {})
        for category, sensor_types in locations.items():
            for sensor_type, sensors in sensor_types.items():
                for sensor in sensors:
                    yield {**sensor, "_category": category, "_sensor_type": sensor_type}

    def live_sensors(self, window_hours: int = LIVE_WINDOW_HOURS) -> list[dict[str, Any]]:
        """Return every sensor whose ``last_update`` is within ``window_hours``."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        live: list[dict[str, Any]] = []
        for sensor in self.iter_inventory():
            last = _parse_teon_ts(sensor.get("last_update"))
            if last is not None and last >= cutoff:
                live.append(sensor)
        return live

    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------

    def disabled_sites(self, strict: bool = True) -> set[str]:
        """Site slugs TEON asks the frontend to hide.

        The upstream payload is a list of ``"{site_slug}|{category}|
        {display_type}"`` strings — ``"4hcamp|lake|EXO"``. Only the site
        slugs are surfaced; callers match them after slugifying.

        **RAISES by default when the list can't be fetched.**

        This used to swallow the error and return an empty set, logging
        "assuming nothing hidden". That defeated a fail-closed guard in
        the backfill: the `except` block never fired, because there was
        no exception — only a cheerful report that nothing was hidden.
        A TEON timeout on 2026-09-22 produced exactly that, and the
        hourly ingest had the same hole.

        "I could not determine what is hidden" is not "nothing is
        hidden". The cost of failing closed is a skipped run; the cost
        of failing open is ingesting data the observatory asked us not
        to hold. Not symmetric, so the default isn't either.

        ``strict=False`` is available for a caller where an empty set is
        genuinely safe. Nothing in this project currently qualifies.
        """
        url = f"{self._base}{TEON_ENDPOINTS['site_visibility']}"
        try:
            resp = self._client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            if strict:
                raise VisibilityUnavailable(
                    f"could not fetch the site-visibility list: {exc}"
                ) from exc
            log.warning("could not fetch visibility list, and strict=False "
                        "— proceeding as though nothing is hidden: %s", exc)
            return set()

        entries = resp.json().get("disabled", [])
        slugs: set[str] = set()
        for entry in entries:
            parts = entry.split("|", 2)
            if parts:
                slugs.add(parts[0])
        return slugs

    def resolve_slug(self, sensor_type: str, site: str) -> str | None:
        """Find the working URL slug for a sensor type, trying candidates.

        :data:`SENSOR_TYPE_SLUGS` maps each display name to a tuple of
        candidate slugs. We try them in order against ``site`` and cache
        the first that returns 200, so subsequent sites of the same type
        cost no extra probes. Returns ``None`` if every candidate 404s.

        Note a 404 here is ambiguous: it can mean "wrong slug" or "right
        slug, no data for this site" (TEON returns 404 rather than an empty
        list for hidden sites). We therefore only cache *successes*, never
        negative results, so a later site of the same type gets a fresh try.
        """
        if sensor_type in self._slug_cache:
            return self._slug_cache[sensor_type]

        candidates = SENSOR_TYPE_SLUGS.get(sensor_type)
        if not candidates:
            log.warning("no slug candidates configured for sensor type %r", sensor_type)
            return None

        for slug in candidates:
            url = f"{self._base}/sensors/{slug}"
            try:
                resp = self._client.get(url, params={"site": site, "page": 1, "page_size": 1})
            except httpx.HTTPError as exc:
                log.warning("probe %s failed: %s", slug, exc)
                continue
            if resp.status_code == 200:
                log.info("resolved %r → /sensors/%s", sensor_type, slug)
                self._slug_cache[sensor_type] = slug
                return slug
            log.debug("probe %s → %s", slug, resp.status_code)

        log.warning("no candidate slug worked for %r (tried: %s)",
                    sensor_type, ", ".join(candidates))
        return None

    def fetch_sensor(
        self,
        sensor_type: str,
        site: str,
        max_records: int = DEFAULT_INGEST_PAGE_SIZE,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Fetch recent observations for one (sensor_type, site) pair.

        ``sensor_type`` is the display name from :func:`list_sensors` (e.g.
        ``"Soil Environmental Conditions"``); the URL slug is resolved via
        :func:`resolve_slug`. Records come back newest-first; we walk
        pagination until we've collected ``max_records`` or run out.
        """
        slug = self.resolve_slug(sensor_type, site)
        if slug is None:
            raise KeyError(f"could not resolve a URL slug for sensor type {sensor_type!r}")

        url = f"{self._base}/sensors/{slug}"
        records: list[dict[str, Any]] = []
        page = 1
        total: int | None = None
        total_pages: int | None = None

        while len(records) < max_records:
            resp = self._client.get(
                url, params={"site": site, "page": page, "page_size": page_size}
            )
            if resp.status_code == 404:
                log.warning("%s @ %s → 404 (site may be hidden or have no data)", slug, site)
                break
            resp.raise_for_status()
            payload = resp.json()

            batch = payload.get("data", [])
            if not batch:
                break

            records.extend(batch)
            total = payload.get("total", total)
            total_pages = payload.get("total_pages", total_pages)

            if page >= (total_pages or page):
                break
            page += 1

        return {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": "TEON",
            "sensor_type": SENSOR_TYPE_CANONICAL.get(sensor_type, sensor_type),
            "sensor_type_display": sensor_type,
            "sensor_slug": slug,
            "site": site,
            "record_count": len(records),
            "total_available": total,
            "records": records[:max_records],
        }

    def iter_sensor_pages(
        self,
        sensor_type: str,
        site: str,
        page_size: int = 200,
        max_pages: int = 5000,
    ) -> Iterator[list[dict[str, Any]]]:
        """Yield pages of records for one sensor, oldest request first.

        :meth:`fetch_sensor` accumulates every record in memory before
        returning, which is right for the ~200 the hourly cron wants and
        wrong for a backfill: Blackwood 2's precipitation gauge holds
        593,507 records, and holding those as Python dicts would need
        hundreds of megabytes.

        This yields each page instead, so a caller can flush to disk and
        keep memory flat regardless of how much history a sensor has.

        Stops on an empty page, on the API's own ``total_pages``, or on
        ``max_pages`` as a runaway guard.
        """
        slug = self.resolve_slug(sensor_type, site)
        if slug is None:
            raise KeyError(f"could not resolve a URL slug for sensor type {sensor_type!r}")

        url = f"{self._base}/sensors/{slug}"
        page = 1
        total_pages: int | None = None

        while page <= max_pages:
            resp = self._client.get(
                url, params={"site": site, "page": page, "page_size": page_size}
            )
            if resp.status_code == 404:
                log.warning("%s @ %s → 404 on page %d", slug, site, page)
                return
            resp.raise_for_status()
            payload = resp.json()

            batch = payload.get("data", [])
            if not batch:
                return
            yield batch

            total_pages = payload.get("total_pages", total_pages)
            if total_pages is not None and page >= total_pages:
                return
            if len(batch) < page_size:
                return          # short page means the end
            page += 1

        log.warning("%s @ %s hit the %d-page guard; history may be incomplete",
                    slug, site, max_pages)

    def fetch_many(
        self,
        targets: Iterable[tuple[str, str]],
        **kwargs,
    ) -> Iterator[dict[str, Any]]:
        """Fetch a batch of (sensor_type, site) pairs, logging failures."""
        for sensor_type, site in targets:
            try:
                yield self.fetch_sensor(sensor_type, site, **kwargs)
            except (httpx.HTTPError, KeyError) as exc:
                log.warning("fetch %s @ %s failed: %s", sensor_type, site, exc)


def _parse_teon_ts(value: Any) -> datetime | None:
    """Parse a naive TEON timestamp as :data:`TEON_TIMEZONE`-local.

    TEON returns wall-clock times with no offset. See the reasoning beside
    ``TEON_TIMEZONE`` in config for why Pacific local rather than UTC.
    Falls back to UTC if the zone database is unavailable, which keeps
    freshness comparisons working (just offset) rather than crashing.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        naive = datetime.fromisoformat(value)
    except ValueError:
        return None
    if naive.tzinfo is not None:
        return naive
    try:
        return naive.replace(tzinfo=ZoneInfo(TEON_TIMEZONE))
    except Exception:
        log.debug("zone %s unavailable; treating timestamps as UTC", TEON_TIMEZONE)
        return naive.replace(tzinfo=timezone.utc)


def slugify_site(site: str) -> str:
    """Match TEON's site-slug convention: lowercase, spaces removed.

    Derived from the ``/site-visibility/disabled`` payload which shipped
    ``"4hcamp|lake|EXO"`` for the site whose display name is ``"4H Camp"``.
    """
    return site.lower().replace(" ", "")
