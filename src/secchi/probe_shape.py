"""Report what a record from each sensor type actually looks like.

This probe should have existed before the backfill. It didn't, and the
consequence was 1,490,116 rows filed under ``year=0000`` because
``to_frames`` reads ``rec.get("TIMESTAMP")`` — a single hardcoded key
that EXO records happen to use and MiniDot and HOBO records apparently
don't.

The failure was silent in the way this project keeps producing: the
backfill reported success, the rows were written, and nothing raised. It
only surfaced because ``year=0000`` exists as a deliberate bucket for
unparseable timestamps rather than dropping them.

So: fetch one record per sensor type and print its keys. Cheap, and it
answers "what is the timestamp field called here" without guessing.
"""

from __future__ import annotations

import logging
from typing import Any

from secchi.config import TEON_TIMESTAMP_FIELDS
from secchi.sources.teon import TeonClient

log = logging.getLogger("secchi.probe.shape")

# Read the same list the transform uses, rather than keeping a second
# copy. A probe whose idea of "known" can drift from the parser's idea is
# worse than no probe — it would report green while ingest failed.
#
# A few extra names the parser doesn't handle yet, so the probe can still
# SPOT them and say so.
TIMESTAMP_CANDIDATES = tuple(TEON_TIMESTAMP_FIELDS) + (
    "Date_Time", "DATE_TIME", "epoch", "unix_time",
)

# Keys that are metadata rather than measurements, so the probe can
# separate the two in its report.
NON_MEASUREMENT_KEYS = frozenset({
    "uuid", "id", "site", "sensor", "sensor_id", "latitude", "longitude",
    "lat", "lng", "elevation", "created_at", "updated_at",
})


def _classify(record: dict) -> dict[str, list[str]]:
    """Split a record's keys into timestamp candidates, metadata, values."""
    ts = [k for k in record if k in TIMESTAMP_CANDIDATES]
    # Anything that isn't a known candidate but looks date-shaped.
    maybe_ts = [
        k for k in record
        if k not in ts
        and isinstance(record[k], str)
        and len(record[k]) >= 10
        and record[k][:4].isdigit()
        and "-" in record[k][:10]
    ]
    meta = [k for k in record if k in NON_MEASUREMENT_KEYS]
    values = [k for k in record
              if k not in ts and k not in maybe_ts and k not in meta]
    return {"timestamp": ts, "maybe_timestamp": maybe_ts,
            "metadata": meta, "values": values}


def probe_record_shapes() -> int:
    """Fetch one record per sensor type and report its field names."""
    with TeonClient() as client:
        try:
            inventory = list(client.iter_inventory())
        except Exception:
            log.exception("could not read the TEON inventory")
            return 1

        # One representative site per sensor type — the one with the most
        # data, EXCLUDING sites TEON has flagged non-public. Picking purely
        # by record count sent the EXO probe to 4H Camp, which is hidden and
        # 404s, and the summary then reported that as a timestamp mismatch.
        try:
            disabled = client.disabled_sites()
        except Exception:
            disabled = set()

        def _slug(site: str) -> str:
            return site.lower().replace(" ", "").replace("_", "")

        by_type: dict[str, dict] = {}
        for row in inventory:
            if _slug(row["site"]) in {_slug(d) for d in disabled}:
                continue
            t = row["_sensor_type"]
            best = by_type.get(t)
            if best is None or (row.get("data_count") or 0) > (best.get("data_count") or 0):
                by_type[t] = row

        print(f"\n  {len(by_type)} sensor type(s) to probe"
              + (f" ({len(disabled)} hidden site(s) skipped)" if disabled else "")
              + "\n")
        problems: list[str] = []
        unreachable: list[str] = []

        for sensor_type, row in sorted(by_type.items()):
            site = row["site"]
            try:
                pages = client.iter_sensor_pages(sensor_type, site, page_size=1)
                batch = next(iter(pages), [])
            except Exception as exc:
                # A fetch failure is NOT a timestamp mismatch. Conflating
                # them is what made the first run report EXO as having the
                # wrong field name when it had simply 404'd.
                print(f"  {sensor_type:34} FETCH FAILED: {str(exc)[:60]}")
                unreachable.append(sensor_type)
                continue

            if not batch:
                print(f"  {sensor_type:34} no records returned")
                continue

            rec = batch[0]
            groups = _classify(rec)
            known = groups["timestamp"]
            maybe = groups["maybe_timestamp"]

            # The question is NOT "does this use the default key" — after
            # config is updated, several types legitimately don't. The
            # question is "can the parser handle whatever it uses".
            handled = [k for k in known if k in TEON_TIMESTAMP_FIELDS]
            if handled:
                status = "ok" if handled == ["TIMESTAMP"] else f"ok ({handled[0]})"
            else:
                status = "UNHANDLED"
                problems.append(sensor_type)

            print(f"  {sensor_type:34} {len(rec):>3} keys   {status}")
            print(f"    site              {site}")
            if known:
                for k in known:
                    print(f"    timestamp field   {k!r} = {rec[k]!r}")
            if maybe:
                for k in maybe:
                    print(f"    date-shaped       {k!r} = {rec[k]!r}")
            if not known and not maybe:
                print(f"    NO TIMESTAMP FIELD FOUND")
                print(f"    all keys          {sorted(rec)}")
            print(f"    measurements      {sorted(groups['values'])[:10]}")
            print()

        print()
        if problems:
            print("  Sensor types whose timestamp field the parser CANNOT read:")
            for t in problems:
                print(f"    {t}")
            print()
            print("  Add the correct key(s) to TEON_TIMESTAMP_FIELDS in config,")
            print("  then re-run the affected backfill. Rows already filed under")
            print("  year=0000 can be dropped first — see docs/backfill.md.")
        else:
            print("  Every reachable sensor type uses a timestamp field the")
            print("  parser can read. Non-default keys are shown in the status")
            print("  column and are fine — they're configured.")

        if unreachable:
            print()
            print("  Could not fetch a record from (separate issue — these are")
            print("  NOT timestamp problems):")
            for t in unreachable:
                print(f"    {t}")
        print()
        return 0
