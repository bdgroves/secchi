"""Pull a sensor's full history, straight into the partitioned store.

The hourly cron fetches the newest ~200 records per sensor and writes
them to ``data/raw/`` as JSON snapshots. That is right for keeping a
dashboard current and wrong for history, in two ways:

**It can't reach far enough.** `DEFAULT_INGEST_PAGE_SIZE` caps each
sensor at 200 records. Blackwood 3 holds 15,451.

**It would flood the raw buffer.** An EXO record is roughly 2 KB of JSON.
Routing the full dormant fleet through ``data/raw/`` would dump around
**2 GB** into a directory designed to hold seven days of hourly
snapshots. So this writes directly to parquet and never touches raw.

Three stages, in descending value per megabyte:

1. **Manual EXO sondes** — Blackwood 3 and Meeks, ~31k records, ~10 MB.
   The best records in the network at 98.8 % complete, on the wet side
   of the 3× precipitation gradient where telemetered coverage is
   thinnest.
2. **Nearshore MiniDot and HOBO** — ~480k records, ~34 MB. Six shared
   sites; this is the nearshore programme TEON's StoryMap describes.
3. **Blackwood 2 precipitation and stream chemistry** — ~718k records,
   ~103 MB. Precipitation is the forcing variable the transect has never
   had.

Stages 2 and 3 require the partitioned store. Stage 1 would survive
without it, but there is no reason to run it twice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from secchi.config import PROCESSED_DIR, SENSOR_TYPE_CANONICAL
from secchi.sources.teon import TeonClient, VisibilityUnavailable

log = logging.getLogger("secchi.backfill")

# Rows held in memory before flushing to disk. 20,000 observations is a
# few MB — small enough to be safe on any machine, large enough that a
# 600,000-record sensor doesn't cause thousands of tiny writes.
CHUNK_ROWS = 100_000

# Page size to request. TEON has served 50 reliably; larger values are
# untested, so the default stays conservative and `--page-size` allows
# raising it once someone has checked.
DEFAULT_BACKFILL_PAGE_SIZE = 200

# Stop after this many pages for one sensor, as a runaway guard. At 200
# per page that's 600,000 records — above the largest sensor in the
# network (Blackwood 2 precipitation, 593,507).
MAX_PAGES = 3000


@dataclass
class BackfillStage:
    """A named group of sensor types to pull together."""
    name: str
    sensor_types: tuple[str, ...]
    note: str
    manual_only: bool = False
    # The mirror of manual_only. The `live` stage wants exactly the
    # sensors the hand-collected stages exclude: telemetered instruments
    # whose history we've never pulled because the cron only ever takes
    # the newest 200 records.
    telemetered_only: bool = False


STAGES: dict[str, BackfillStage] = {
    "manual": BackfillStage(
        name="manual",
        sensor_types=("EXO",),
        manual_only=True,
        note="Blackwood 3 and Meeks — the hand-collected lake sondes. "
             "~31k records. Best completeness in the network.",
    ),
    "nearshore": BackfillStage(
        name="nearshore",
        sensor_types=("Minidot", "Hobo"),
        note="Six shared nearshore sites, paired DO and temperature. "
             "~480k records. Needs the partitioned store.",
    ),
    "live": BackfillStage(
        name="live",
        sensor_types=("EXO", "Air Temperature & Relative Humidity",
                      "Soil Environmental Conditions", "Tree stress and growth",
                      "Stream Level"),
        telemetered_only=True,
        note="History for the telemetered sensors. Each endpoint returns "
             "different columns, so every one is fetched: soil, air "
             "temperature and humidity, tree stress, stream level, and the "
             "live lake sondes. Sensors already complete are skipped.",
    ),
    "blackwood": BackfillStage(
        name="blackwood",
        sensor_types=("Precipitation Gauge", "Stream Chemistry"),
        note="The forcing variable and creek chemistry. ~718k records. "
             "Needs the partitioned store.",
    ),
}


@dataclass
class SensorResult:
    sensor_type: str
    site: str
    pages: int = 0
    records: int = 0
    expected: int = 0
    error: str | None = None
    fields: set = field(default_factory=set)


def _is_manual(sensor: dict) -> bool:
    """Hand-collected, by TEON's label or by instrument class.

    Mirrors the transform's rule so the two can't disagree about which
    fleet a sensor belongs to — a `live` stage that accidentally included
    MiniDot would re-fetch half a million records for nothing.
    """
    from secchi.config import MANUAL_COLLECTION_TYPES
    return ("Manual" in (sensor.get("id") or "")
            or sensor.get("_sensor_type") in MANUAL_COLLECTION_TYPES)


# A sensor within this many records of its upstream count is complete.
# Matches the backlog banner's threshold: anything smaller is unparseable
# or duplicate records (up to ~14 seen), not missing history.
COMPLETE_TOLERANCE = 100


def held_counts() -> dict[tuple[str, str], int]:
    """Unique record ids held per (site, stored sensor type).

    Counted with DuckDB, which reads only the three columns it needs.
    Loading the store into pandas to answer this would cost ~650 MB.
    Returns an empty dict if DuckDB or the store is unavailable — in
    which case nothing is skipped, which is the safe direction.
    """
    try:
        import duckdb
        from secchi.query import _glob
    except ImportError:
        log.warning("DuckDB unavailable; can't tell what's already held, "
                    "so nothing will be skipped")
        return {}
    g = _glob("observations")
    if g is None:
        return {}
    rows = duckdb.sql(
        f"SELECT site, sensor_type, count(DISTINCT uuid) "
        f"FROM read_parquet('{g}', hive_partitioning = true, "
        f"union_by_name = true) GROUP BY site, sensor_type").fetchall()
    return {(site, stype): int(n) for site, stype, n in rows}


def _slugify_site(site: str) -> str:
    """Match TEON's own site-slug form, used by /site-visibility/disabled."""
    return (site or "").lower().replace(" ", "").replace("_", "").replace("-", "")


def select_targets(client: TeonClient,
                   stage: BackfillStage,
                   site: str | None = None,
                   held: dict[tuple[str, str], int] | None = None,
                   refetch_complete: bool = False) -> list[dict]:
    """Inventory rows this stage should pull.

    Excludes sites TEON has flagged non-public. That check was missing
    from the first version and a `live` backfill pulled 52,875 records
    from 4H Camp — a site the observatory explicitly asked not be
    published. The dashboard honoured the flag; the backfill didn't even
    look at it.

    Skips sensors already complete in the store, so re-running a stage
    fetches only what's missing. Pass ``refetch_complete=True`` to fetch
    everything regardless.

    It no longer collapses "shared loggers", and the reason is worth
    keeping. At each forest station the soil, air, tree and stream-level
    endpoints share record ids, row counts and battery voltage, so an
    earlier version fetched one and marked it as standing in for the
    rest. But they are PROJECTIONS of one logger table: each endpoint
    returns different measurement columns. Soil gives Soil_VWC, Soil_T
    and Soil_EC; air gives Air_Temp and RH; tree gives the dendrometers.
    Fetching only soil silently discarded every forest station's air
    temperature, humidity and tree-stress history — found on 2026-09-23
    when a query showed Air_Temp at Homewood covering 7.5 days while
    soil covered a year. Shared ids mean shared rows, not shared columns.
    """
    try:
        disabled = {_slugify_site(d) for d in client.disabled_sites()}
    except VisibilityUnavailable as exc:
        # Fail closed. This guard existed before and never fired, because
        # disabled_sites() swallowed the error and returned an empty set —
        # "nothing is hidden" rather than "I don't know". It raises now.
        log.error("%s", exc)
        log.error("REFUSING to backfill: without the visibility list we "
                  "cannot tell which sites TEON has asked us not to "
                  "publish. Re-run when the endpoint responds.")
        return []

    targets = []
    skipped_hidden: list[str] = []
    for s in client.iter_inventory():
        if _slugify_site(s["site"]) in disabled:
            skipped_hidden.append(f"{s['_sensor_type']} @ {s['site']}")
            continue
        if s["_sensor_type"] not in stage.sensor_types:
            continue
        if stage.manual_only and not _is_manual(s):
            continue
        if stage.telemetered_only and _is_manual(s):
            continue
        if site and s["site"] != site:
            continue
        targets.append(s)

    if skipped_hidden:
        log.info("skipping %d sensor(s) at site(s) TEON has flagged "
                 "non-public: %s", len(skipped_hidden),
                 ", ".join(sorted(skipped_hidden)))

    # Skip what's already complete. Compares upstream count with the
    # records held under the same stored sensor type — deliberately per
    # endpoint, because endpoints that share record ids still carry
    # different columns (see the docstring).
    if held is None or refetch_complete:
        return targets

    needed: list[dict] = []
    complete: list[str] = []
    for t in targets:
        raw = t["_sensor_type"]
        canon = SENSOR_TYPE_CANONICAL.get(raw, raw)
        have = held.get((t["site"], canon), 0)
        want = t.get("data_count") or 0
        if want and have >= want - COMPLETE_TOLERANCE:
            complete.append(f"{raw} @ {t['site']}")
            continue
        needed.append(t)

    if complete:
        log.info("skipping %d sensor(s) already complete in the store: %s",
                 len(complete), ", ".join(sorted(complete)))
    return needed


def backfill_sensor(client: TeonClient,
                    sensor_type: str,
                    site: str,
                    expected: int,
                    page_size: int,
                    store_writer,
                    dry_run: bool = False) -> SensorResult:
    """Page through one sensor's entire history, flushing as it goes.

    Uses :meth:`TeonClient.iter_sensor_pages` rather than
    ``fetch_sensor``: the latter accumulates every record in memory
    before returning, which is fine for the 200 the hourly cron wants
    and not fine for 593,507.

    Flushes every ``CHUNK_ROWS`` observations so memory stays flat
    whatever the sensor holds. Because the store deduplicates on record
    id, an interrupted run is simply re-run — there is no resume state
    that can be corrupted.
    """
    from secchi.transform import to_frames

    result = SensorResult(sensor_type=sensor_type, site=site, expected=expected)

    if dry_run:
        pages = -(-expected // page_size) if expected else 0
        log.info("  %s / %s: would fetch ~%d records in ~%d page(s)",
                 sensor_type, site, expected, pages)
        result.pages, result.records = pages, expected
        return result

    buffer: list[dict] = []
    seen: set = set()

    def flush() -> None:
        """Turn buffered records into long rows and hand them to the store."""
        if not buffer:
            return
        snapshot = {
            "source": "TEON",
            "sensor_type": sensor_type,
            "sensor_type_display": sensor_type,
            "site": site,
            "record_count": len(buffer),
            "records": buffer,
        }
        obs, _assets = to_frames([snapshot])
        store_writer(obs)
        buffer.clear()

    try:
        for batch in client.iter_sensor_pages(sensor_type, site,
                                              page_size=page_size):
            # Guard against an API that ignores `page` and re-serves the
            # same rows — that would otherwise spin to the page limit.
            fresh = [r for r in batch if r.get("uuid") not in seen]
            if not fresh:
                log.warning("  %s / %s: page %d repeated earlier records; "
                            "stopping rather than looping",
                            sensor_type, site, result.pages + 1)
                break
            seen.update(r.get("uuid") for r in fresh)

            buffer.extend(fresh)
            result.pages += 1
            result.records += len(fresh)

            if len(buffer) >= CHUNK_ROWS:
                flush()
            if result.pages % 20 == 0:
                pct = (100.0 * result.records / expected) if expected else 0
                log.info("  %s / %s: %d records (%.0f%%)",
                         sensor_type, site, result.records, pct)
    except Exception as exc:
        result.error = str(exc)[:120]
        log.warning("  %s / %s stopped after %d page(s): %s",
                    sensor_type, site, result.pages, exc)

    flush()          # whatever is left, even after an error
    return result


def run_backfill(stage_name: str,
                 site: str | None = None,
                 page_size: int = DEFAULT_BACKFILL_PAGE_SIZE,
                 dry_run: bool = False,
                 refetch_complete: bool = False) -> int:
    """Run one backfill stage.

    Sensors already complete in the store are skipped unless
    ``refetch_complete`` is set (``--force`` on the command line).
    """
    from secchi.store import append_partitions, compact_partitions

    stage = STAGES.get(stage_name)
    if not stage:
        log.error("unknown stage %r — choose from %s",
                  stage_name, ", ".join(STAGES))
        return 1

    root = PROCESSED_DIR / "observations"

    def writer(df: pd.DataFrame) -> None:
        """Append without reading; compaction happens once at the end.

        Read-merge-write is quadratic for a bulk load. The precipitation
        gauge's 1.78 M observations meant 89 flushes each rewriting all
        accumulated rows — 80 M row writes, ~2 GB to store 45 MB — and it
        filled the disk mid-run on 2026-09-22.

        This function was reverted to read-merge-write once already, by a
        later bundle rebuilt from an older copy of this file. It is now
        covered by tests/test_capabilities_persist.py.
        """
        if df is None or df.empty:
            return
        append_partitions(df, root, "teon")

    print(f"\n  stage: {stage.name}")
    print(f"  {stage.note}\n")

    # What's already held, so the stage fetches only what's missing.
    held = {} if refetch_complete else held_counts()

    with TeonClient() as client:
        targets = select_targets(client, stage, site, held=held,
                                 refetch_complete=refetch_complete)
        if not targets:
            log.error("no sensors matched stage %r%s", stage_name,
                      f" at site {site!r}" if site else "")
            return 1

        total_expected = sum(t.get("data_count") or 0 for t in targets)
        print(f"  {len(targets)} sensor(s), ~{total_expected:,} records upstream")
        if dry_run:
            print("  DRY RUN — nothing will be fetched or written\n")
        else:
            print(f"  page size {page_size}, writing straight to "
                  f"{root.relative_to(root.parents[2])}\n")

        results = []
        for t in targets:
            results.append(backfill_sensor(
                client, t["_sensor_type"], t["site"],
                t.get("data_count") or 0, page_size, writer, dry_run))

    # One compaction pass for everything this run appended: merge each
    # partition's part files and deduplicate, leaving the store in the
    # shape the hourly job expects. Skipped on a dry run, which writes
    # nothing.
    if not dry_run:
        print("\n  compacting...")
        out = compact_partitions(root, ["uuid", "site", "variable"])
        if out.get("compacted"):
            print(f"  merged {out['compacted']} partition(s), "
                  f"{out['rows']:,} rows, removed {out['files_removed']} "
                  f"part file(s)")
        if out.get("skipped"):
            print(f"  {out['skipped']} partition(s) left uncompacted — see the "
                  f"errors above")

    print()
    print(f"  {'sensor':22}{'site':20}{'pages':>7}{'records':>10}"
          f"{'expected':>10}  status")
    print("  " + "-" * 78)
    got = 0
    for r in results:
        got += r.records
        status = r.error or ("ok" if not r.expected or r.records >= r.expected * 0.95
                             else "SHORT")
        print(f"  {r.sensor_type[:21]:22}{r.site[:19]:20}{r.pages:>7}"
              f"{r.records:>10,}{r.expected:>10,}  {status}")

    print(f"\n  {got:,} of ~{total_expected:,} records "
          f"({100.0*got/total_expected if total_expected else 0:.1f}%)\n")

    short = [r for r in results if r.error]
    if short:
        print("  Some sensors did not complete. Re-running is safe — the "
              "store deduplicates\n  on record id, so nothing is doubled "
              "and finished sensors cost only their\n  first page.\n")
        return 1
    if not dry_run:
        print("  Run `pixi run transform` to rebuild the dashboard from the "
              "expanded record.\n")
    return 0
