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

import httpx

from secchi.config import (
    DEFAULT_INGEST_PAGE_SIZE,
    HTTP_TIMEOUT_SECONDS,
    LIVE_WINDOW_HOURS,
    SENSOR_TYPE_SLUGS,
    TEON_API_BASE,
    TEON_ENDPOINTS,
    USER_AGENT,
)

log = logging.getLogger("secchi.sources.teon")


class TeonClient:
    """Thin wrapper around ``httpx.Client`` scoped to the TEON backend."""

    def __init__(self, base_url: str = TEON_API_BASE, timeout: float = HTTP_TIMEOUT_SECONDS):
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
        )

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
    # Time series
    # ------------------------------------------------------------------

    def fetch_sensor(
        self,
        sensor_type: str,
        site: str,
        max_records: int = DEFAULT_INGEST_PAGE_SIZE,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Fetch recent observations for one (sensor_type, site) pair.

        ``sensor_type`` is the payload key from :func:`list_sensors` (e.g.
        ``"ExoSensor"``); it gets translated to a URL slug via
        :data:`SENSOR_TYPE_SLUGS`. Records come back newest-first; we walk
        pagination until we've collected ``max_records`` or run out.

        Returns a snapshot dict with metadata and the flattened records.
        """
        slug = SENSOR_TYPE_SLUGS.get(sensor_type)
        if slug is None:
            raise KeyError(f"no URL slug configured for sensor type {sensor_type!r}")

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
                log.warning("slug %s returned 404 — sensor type may not be a valid endpoint", slug)
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
            "sensor_type": sensor_type,
            "sensor_slug": slug,
            "site": site,
            "record_count": len(records),
            "total_available": total,
            "records": records[:max_records],
        }

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
    """TEON emits naive ISO 8601 in what looks like local time; treat as UTC.

    (We can't verify the timezone without documentation, so UTC is the safe
    default for freshness comparisons and it stays consistent across sites.)
    """
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
