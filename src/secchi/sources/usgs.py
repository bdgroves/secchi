"""Client for the USGS Water Data OGC APIs.

These are the *modernized* USGS endpoints at ``api.waterdata.usgs.gov``,
which replace the legacy ``waterservices.usgs.gov/nwis/iv/`` service USGS
is retiring. See ``docs/usgs-plan.md`` for the endpoint mapping.

Everything here is built from USGS's published documentation rather than
observed responses — this environment can't reach the host, so the first
real run is the verification step. ``probe`` is the command for that: it
asks each gauge what it actually measures instead of assuming.

Key facts the implementation depends on:

- Collections live at ``/ogcapi/v0/collections/{name}/items``.
- Responses are GeoJSON FeatureCollections; observations are in
  ``features[]``, each with ``properties`` and ``geometry``.
- Monitoring locations are addressed as ``USGS-<site_number>``.
- Auth is optional but sharply rate-limited without it: **50 requests per
  hour per IP unauthenticated, 1000 with a key.** The key goes in the
  ``X-Api-Key`` header.
- Remaining quota comes back in ``X-RateLimit-Remaining``; exceeding it
  returns ``429``.
- Pagination is cursor-based: follow ``links[rel="next"].href``.
  ``limit`` defaults to 10 and caps at 10000.
- ``/continuous`` only serves three years per query.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Iterator

import httpx

from secchi.config import (
    HTTP_TIMEOUT_SECONDS,
    USER_AGENT,
    USGS_API_BASE,
    USGS_API_KEY_ENV,
    USGS_COLLECTIONS,
    USGS_GAUGES,
    USGS_PAGE_LIMIT,
    USGS_STATISTIC_INSTANTANEOUS,
)

log = logging.getLogger("secchi.sources.usgs")


class UsgsRateLimited(RuntimeError):
    """Raised on HTTP 429 so callers can back off rather than hammer."""


class UsgsClient:
    """Thin wrapper around the USGS OGC API - Features endpoints."""

    def __init__(
        self,
        base_url: str = USGS_API_BASE,
        api_key: str | None = None,
        timeout: float = HTTP_TIMEOUT_SECONDS,
    ):
        self._base = base_url.rstrip("/")
        # An explicit key wins; otherwise read the environment. Absent a
        # key we still work, just against the 50/hour unauthenticated cap.
        self._api_key = api_key or os.environ.get(USGS_API_KEY_ENV)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if self._api_key:
            headers["X-Api-Key"] = self._api_key
        else:
            log.warning(
                "no %s set — USGS limits unauthenticated use to 50 requests/hour "
                "per IP. Get a free key at https://api.waterdata.usgs.gov/signup/",
                USGS_API_KEY_ENV,
            )
        self._client = httpx.Client(timeout=timeout, headers=headers, follow_redirects=True)
        self._rate_remaining: int | None = None

    def __enter__(self) -> "UsgsClient":
        return self

    def __exit__(self, *_exc) -> None:
        self._client.close()

    def close(self) -> None:
        self._client.close()

    @property
    def authenticated(self) -> bool:
        return bool(self._api_key)

    @property
    def rate_remaining(self) -> int | None:
        """Requests left this hour, as of the last response."""
        return self._rate_remaining

    # ------------------------------------------------------------------
    # Core request
    # ------------------------------------------------------------------

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict:
        resp = self._client.get(url, params=params)

        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            try:
                self._rate_remaining = int(remaining)
            except ValueError:
                pass

        if resp.status_code == 429:
            raise UsgsRateLimited(
                f"USGS rate limit exhausted (limit "
                f"{resp.headers.get('X-RateLimit-Limit', '?')}/hour). "
                + ("Key is set; wait for the window to reset."
                   if self._api_key else
                   f"No key set — set {USGS_API_KEY_ENV} to raise the cap to 1000/hour.")
            )
        resp.raise_for_status()
        return resp.json()

    def _items(
        self,
        collection: str,
        params: dict[str, Any],
        max_features: int | None = None,
    ) -> list[dict]:
        """Fetch features from a collection, following pagination cursors.

        USGS paginates with an opaque cursor in ``links[rel="next"].href``
        rather than page numbers, so we follow the link the server gives us
        instead of constructing offsets.
        """
        url = f"{self._base}/collections/{collection}/items"
        query = {"f": "json", "limit": min(USGS_PAGE_LIMIT, max_features or USGS_PAGE_LIMIT),
                 **params}

        features: list[dict] = []
        next_url: str | None = None
        page = 0

        while True:
            page += 1
            payload = self._get(next_url or url, None if next_url else query)
            batch = payload.get("features", [])
            features.extend(batch)

            if max_features is not None and len(features) >= max_features:
                features = features[:max_features]
                break

            next_url = _next_link(payload)
            if not next_url or not batch:
                break
            if page >= 50:
                log.warning("stopped paginating %s after %d pages", collection, page)
                break

        log.info("%s → %d features in %d page(s)%s",
                 collection, len(features), page,
                 f", {self._rate_remaining} requests left this hour"
                 if self._rate_remaining is not None else "")
        return features

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def list_time_series(self, site_number: str) -> list[dict]:
        """What does this gauge actually measure?

        Hits ``/time-series-metadata``, which reports one row per
        (parameter, statistic) series at a location, including its period of
        record. This is the call that replaces guessing which parameter
        codes a gauge supports.
        """
        return self._items(
            USGS_COLLECTIONS["time_series_metadata"],
            {"monitoring_location_id": _loc_id(site_number)},
        )

    def describe_gauges(self, site_numbers: list[str] | None = None) -> dict[str, list[dict]]:
        """Run :func:`list_time_series` across the configured gauges.

        Returns ``{site_number: [series, ...]}``. Failures are logged and
        recorded as an empty list rather than aborting the sweep, so one
        bad gauge doesn't hide the others.
        """
        sites = site_numbers or list(USGS_GAUGES)
        out: dict[str, list[dict]] = {}
        for site in sites:
            try:
                out[site] = self.list_time_series(site)
            except UsgsRateLimited:
                raise
            except httpx.HTTPError as exc:
                log.warning("time-series lookup failed for %s: %s", site, exc)
                out[site] = []
        return out

    def monitoring_location(self, site_number: str) -> dict | None:
        """Site metadata: name, coordinates, drainage area, datum."""
        features = self._items(
            USGS_COLLECTIONS["monitoring_locations"],
            {"id": _loc_id(site_number)},
            max_features=1,
        )
        return features[0] if features else None

    # ------------------------------------------------------------------
    # Observations
    # ------------------------------------------------------------------

    def fetch_latest(
        self,
        site_number: str,
        parameter_codes: list[str] | None = None,
        statistic_id: str | None = USGS_STATISTIC_INSTANTANEOUS,
    ) -> dict:
        """Most recent value for each time series at a gauge.

        One request per gauge regardless of how many parameters it carries,
        which matters against a 1000/hour budget.

        ``statistic_id`` defaults to instantaneous. Leaving it unset would
        return daily max, min, mean and median alongside the instantaneous
        value for the same parameter code — Blackwood publishes water
        temperature under five statistics — and the transform would have no
        way to tell them apart from the parameter code alone.
        """
        params: dict[str, Any] = {"monitoring_location_id": _loc_id(site_number)}
        if parameter_codes:
            # The API accepts repeated/comma values for parameter_code.
            params["parameter_code"] = ",".join(parameter_codes)
        if statistic_id:
            params["statistic_id"] = statistic_id

        features = self._items(USGS_COLLECTIONS["latest_continuous"], params)
        return _snapshot(site_number, "latest-continuous", features,
                         parameter_codes=parameter_codes, statistic_id=statistic_id)

    def fetch_continuous(
        self,
        site_number: str,
        parameter_codes: list[str] | None = None,
        period: str = "P2D",
        max_features: int = 5000,
        statistic_id: str | None = USGS_STATISTIC_INSTANTANEOUS,
    ) -> dict:
        """Continuous history for a gauge.

        ``period`` is an ISO 8601 duration the API understands directly —
        ``"P2D"`` for two days, ``"PT36H"`` for 36 hours. For explicit
        windows pass an RFC 3339 interval instead.

        Note ``/continuous`` serves at most three years per query, so a
        full backfill of a long record has to be chunked by year.
        """
        params: dict[str, Any] = {
            "monitoring_location_id": _loc_id(site_number),
            "time": period,
        }
        if parameter_codes:
            params["parameter_code"] = ",".join(parameter_codes)
        if statistic_id:
            params["statistic_id"] = statistic_id

        features = self._items(USGS_COLLECTIONS["continuous"], params,
                               max_features=max_features)
        return _snapshot(site_number, "continuous", features,
                         parameter_codes=parameter_codes, period=period,
                         statistic_id=statistic_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _loc_id(site_number: str) -> str:
    """USGS addresses locations as ``USGS-<number>``; accept either form."""
    s = str(site_number)
    return s if s.startswith("USGS-") else f"USGS-{s}"


def _next_link(payload: dict) -> str | None:
    for link in payload.get("links", []) or []:
        if link.get("rel") == "next" and link.get("href"):
            return link["href"]
    return None


def _snapshot(site_number: str, collection: str, features: list[dict], **extra) -> dict:
    """Wrap features in the same envelope shape the TEON snapshots use, so
    the ingest and transform layers can treat both sources alike."""
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "USGS",
        "collection": collection,
        "site_number": str(site_number),
        "site_name": USGS_GAUGES.get(str(site_number), {}).get("name"),
        "feature_count": len(features),
        **{k: v for k, v in extra.items() if v is not None},
        "features": features,
    }


def flatten_features(snapshot: dict) -> Iterator[dict]:
    """Yield one flat observation dict per GeoJSON feature.

    USGS returns a feature per observation (unlike the legacy service,
    which nested values inside a time-series block), so this is a straight
    unwrap of ``properties`` plus the point geometry.
    """
    site_number = snapshot.get("site_number")
    site_name = snapshot.get("site_name")
    for feat in snapshot.get("features", []):
        props = feat.get("properties") or {}
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") or [None, None]
        yield {
            "feature_id": feat.get("id"),
            "site_number": site_number,
            "site_name": site_name,
            "monitoring_location_id": props.get("monitoring_location_id"),
            "time_series_id": props.get("time_series_id"),
            "parameter_code": props.get("parameter_code"),
            "statistic_id": props.get("statistic_id"),
            "time": props.get("time"),
            "value": props.get("value"),
            "unit_of_measure": props.get("unit_of_measure"),
            # USGS is explicit about whether a value is Director-approved or
            # still provisional. TEON has no equivalent; worth carrying.
            "approval_status": props.get("approval_status"),
            "qualifier": props.get("qualifier"),
            "lng": coords[0] if len(coords) > 0 else None,
            "lat": coords[1] if len(coords) > 1 else None,
        }
