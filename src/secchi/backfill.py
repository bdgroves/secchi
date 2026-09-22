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

# Rows held in memory before flushing to disk. 20,000 observations is a
# few MB — small enough to be safe on any machine, large enough that a
# 600,000-record sensor doesn't cause thousands of tiny writes.
CHUNK_ROWS = 20_000

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
    return "Manual" in (sensor.get("id") or "")


def select_targets(client: TeonClient,
                   stage: BackfillStage,
                   site: str | None = None) -> list[dict]:
    """Inventory rows this stage should pull."""
    targets = []
    for s in client.iter_inventory():
        if s["_sensor_type"] not in stage.sensor_types:
            continue
        if stage.manual_only and not _is_manual(s):
            continue
        if site and s["site"] != site:
            continue
        targets.append(s)
    return targets


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
    from secchi.store import write_partitions

    stage = STAGES.get(stage_name)
    if not stage:
        log.error("unknown stage %r — choose from %s",
                  stage_name, ", ".join(STAGES))
        return 1

    root = PROCESSED_DIR / "observations"

    def writer(df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        write_partitions(df, root, "teon", ["uuid", "site", "variable"])

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
