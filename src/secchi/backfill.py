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

from secchi.config import PROCESSED_DIR
from secchi.sources.teon import TeonClient

log = logging.getLogger("secchi.backfill")

# Rows held in memory before flushing. Raised from 20,000 now that a
# flush is a plain append rather than a whole-partition rewrite —
# 100,000 rows is a few tens of MB, and fewer, larger part files make
# compaction cheaper too.
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
        note="History for the sensors that ARE reporting. ~427k records. "
             "The hourly cron only ever fetched the newest 200 per sensor, "
             "so everything before we started running is missing — including "
             "both live lake sondes and the entire transect.",
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


def _slugify_site(site: str) -> str:
    """Match TEON's own site-slug form, used by /site-visibility/disabled."""
    return (site or "").lower().replace(" ", "").replace("_", "").replace("-", "")


def select_targets(client: TeonClient,
                   stage: BackfillStage,
                   site: str | None = None) -> list[dict]:
    """Inventory rows this stage should pull.

    Excludes sites TEON has flagged non-public. That check was missing
    from the first version and a `live` backfill pulled 52,875 records
    from 4H Camp — a site the observatory explicitly asked not be
    published. The dashboard honoured the flag; the backfill didn't even
    look at it.

    Also collapses shared loggers. At every terrestrial station the soil,
    air-temperature, tree-stress and stream-level endpoints are
    projections of ONE Campbell table with identical record ids and
    identical row counts. Fetching all of them means fetching the same
    logger two or three times: 684,366 of 1,410,562 records in the first
    live run — 49 % — were duplicates of each other. The store deduped
    them correctly; the cost was in requests and time.
    """
    try:
        disabled = {_slugify_site(d) for d in client.disabled_sites()}
    except Exception:
        log.warning("could not read the site-visibility list; "
                    "refusing to backfill rather than risk a hidden site")
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

    # Collapse shared loggers: at one site, endpoints reporting an
    # identical record count are the same table under different names.
    # Keep one, and note which endpoints it stands in for.
    by_site: dict[str, list[dict]] = {}
    for t in targets:
        by_site.setdefault(t["site"], []).append(t)

    collapsed: list[dict] = []
    for site_name, rows in by_site.items():
        by_count: dict[int, list[dict]] = {}
        for r in rows:
            by_count.setdefault(r.get("data_count") or 0, []).append(r)
        for count, group in by_count.items():
            if len(group) > 1 and count > 0:
                keep = group[0]
                keep = {**keep, "_shares_logger_with":
                        [g["_sensor_type"] for g in group[1:]]}
                log.info("%s: %s share one logger (%s records); fetching once",
                         site_name,
                         " / ".join(g["_sensor_type"] for g in group),
                         f"{count:,}")
                collapsed.append(keep)
            else:
                collapsed.extend(group)

    saved = sum(t.get("data_count") or 0 for t in targets) \
        - sum(t.get("data_count") or 0 for t in collapsed)
    if saved:
        log.info("shared-logger collapse avoids re-fetching %s records", f"{saved:,}")

    return collapsed


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
                 dry_run: bool = False) -> int:
    """Run one backfill stage."""
    from secchi.store import append_partitions, compact_partitions

    stage = STAGES.get(stage_name)
    if not stage:
        log.error("unknown stage %r — choose from %s",
                  stage_name, ", ".join(STAGES))
        return 1

    root = PROCESSED_DIR / "observations"

    def writer(df: pd.DataFrame) -> None:
        """Append without reading. Compaction happens once, at the end.

        The read-merge-write path is quadratic for a backfill: the
        precipitation gauge's 1,780,521 observations meant 89 flushes
        each rewriting everything accumulated, 80 million row writes for
        1.8 million rows, and it filled the disk mid-run.
        """
        if df is None or df.empty:
            return
        append_partitions(df, root, "teon")

    print(f"\n  stage: {stage.name}")
    print(f"  {stage.note}\n")

    with TeonClient() as client:
        targets = select_targets(client, stage, site)
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

    # One compaction pass for everything the run appended. Merges the
    # part files per partition and deduplicates, so the store ends in
    # the same shape the hourly cron expects.
    print("\n  compacting...")
    out = compact_partitions(root, ["uuid", "site", "variable"])
    if out.get("compacted"):
        print(f"  merged {out['compacted']} partition(s), "
              f"{out['rows']:,} rows, removed {out['files_removed']} part file(s)")

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
