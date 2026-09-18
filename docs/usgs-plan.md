# USGS integration

Implemented in `src/secchi/sources/usgs.py`. **Built from USGS's published
documentation, not from observed responses** — this environment can't reach
the host (robots.txt blocks automated fetches, and the sandbox egress
allowlist blocks it too), so the first real run is the verification step.

Start with `pixi run usgs-probe`. See *Verifying it* below.

## The headline: the API we were going to use is being retired

Every USGS example you'll find online (and my own earlier suggestion in
this project) points at `waterservices.usgs.gov/nwis/iv/`. USGS is migrating
off it to a modern OGC API - Features service at `api.waterdata.usgs.gov`.
Building against the legacy endpoint now means rewriting later.

Official migration guide: https://api.waterdata.usgs.gov/docs/ogcapi/migration/

| If you'd use… | Use instead |
|---|---|
| `waterservices.usgs.gov/nwis/iv/` (latest) | `api.waterdata.usgs.gov/ogcapi/v0/collections/latest-continuous` |
| `waterservices.usgs.gov/nwis/iv/` (history) | `api.waterdata.usgs.gov/ogcapi/v0/collections/continuous` |
| `waterservices.usgs.gov/nwis/dv/` | `api.waterdata.usgs.gov/ogcapi/v0/collections/daily` |
| `waterservices.usgs.gov/nwis/site/` | `api.waterdata.usgs.gov/ogcapi/v0/collections/monitoring-locations` |
| period-of-record lookup | `api.waterdata.usgs.gov/ogcapi/v0/collections/time-series-metadata` |

## API key and rate limits

Confirmed numbers from USGS:

| | Requests per hour |
|---|---|
| No key | **50** per IP address |
| With key | **1,000** |

The key goes in an `X-Api-Key` header (an `api_key` query parameter also
works; the header keeps it out of logs and out of `data/raw` filenames, so
that's what the client uses). Every response carries `X-RateLimit-Limit`
and `X-RateLimit-Remaining`; exceeding the cap returns `429`.

`UsgsClient` reads the key from the `USGS_API_KEY` environment variable,
logs a warning when it's missing, and raises `UsgsRateLimited` on a 429 so
the caller can stop rather than keep hammering. Every probe and ingest run
logs the remaining quota.

**Local use.** Set it in your shell before running:

    $env:USGS_API_KEY = "your-key-here"        # PowerShell, current session
    pixi run usgs-probe

To persist it for your user account:

    [Environment]::SetEnvironmentVariable("USGS_API_KEY", "your-key", "User")

**CI.** Add it as a repository secret named `USGS_API_KEY`:

    GitHub → Settings → Secrets and variables → Actions → New repository secret

Both `fetch.yml` and `pages.yml` pass it through to the USGS step. Don't
commit the key — it belongs in the secret and the environment, nowhere else.

## Shape differences that matter for our transform

- **One feature per observation.** The legacy service grouped values into a
  `timeSeries` block. `/continuous` returns a flat feature list, which is
  actually closer to the long format `secchi` already stores — less
  reshaping, not more.
- **Timestamps are UTC with offsets included.** No naive-timestamp guessing,
  unlike TEON. Nice.
- **Missing data is `null`**, not a sentinel value. Our existing null-drop
  in `to_frames` handles this correctly already.
- **Three-year query ceiling** on `/continuous`. Fine for live ingest;
  matters if we ever backfill Blackwood's full record, which needs
  chunking one year at a time.
- **Site metadata lives in a different collection.** Coordinates, drainage
  area and datum come from `/monitoring-locations`, not the data endpoint.
  Worth pulling once and caching rather than per-ingest.
- **Approval status is explicit.** `approval_status` is `Provisional` or
  `Approved`. We should carry this through and surface it — TEON has no
  equivalent and it's genuinely useful metadata.

## Gauges worth ingesting

Confirmed active from earlier searching. **Parameter availability per gauge
is unverified** — the `/time-series-metadata` collection is the correct way
to check what each site actually reports, and should be the module's first
call rather than an assumption.

| Site no. | Name | Relevance |
|---|---|---|
| `10336660` | Blackwood Creek nr Tahoe City, CA | West-shore, record to **1961**. Reports discharge, gage height *and* turbidity. Covers TEON's Blackwood 2 station, offline since June. |
| `10336730` | Glenbrook Creek at Glenbrook, NV | East-shore. Pairs with TEON's Glenbrook 2 stream gauge. |
| `10336725` | Glenbrook Creek at Old Hwy 50 nr Glenbrook, NV | Second Glenbrook point. |
| `10337000` | Lake Tahoe at Tahoe City, CA | Lake surface elevation. Long record. |
| `390519119563501` | Lake Tahoe sample point at Glenbrook Bay, NV | Nearshore water quality. |

Parameter codes we care about:

| Code | Measure |
|---|---|
| `00060` | Discharge, ft³/s |
| `00065` | Gage height, ft |
| `00010` | Water temperature, °C |
| `63680` | Turbidity, FNU |
| `00095` | Specific conductance |
| `00300` | Dissolved oxygen |

## Why this is worth doing

**Blackwood's 1961 record is the thing TEON structurally cannot give us.**
TEON is two days old. Any claim about whether current conditions are
unusual needs a baseline, and a 65-year continuous discharge and turbidity
series at a west-shore Tahoe tributary is exactly that baseline.

It also closes a real gap: TEON's own Blackwood 2 station has been dark
since 2026-06-18, so the west side of the basin currently has no TEON
stream data at all. USGS covers it.

And USGS turbidity at `63680` is directly comparable to the EXO sondes'
turbidity — same measure, same units — which gives us a cross-check on the
Sunnyside calibration problem from a completely independent instrument.

## Proposed module shape

```
src/secchi/sources/usgs.py

    class UsgsClient
        list_time_series(site)      -> /time-series-metadata, what's actually available
        fetch_latest(sites, params) -> /latest-continuous
        fetch_history(site, param, start, end) -> /continuous, chunked by year

src/secchi/config.py
    USGS_API_BASE   = "https://api.waterdata.usgs.gov/ogcapi/v0"
    USGS_GAUGES     = {...}     # the table above
    USGS_PARAMETERS = {...}     # code -> label, units

data/raw/usgs/{site}/{param}/YYYY/MM/DD/HHMMSS.json
```

`transform.py` needs a `source` column on the long frame to keep TEON and
USGS observations distinguishable — currently everything is implicitly
TEON.

## Verifying it

**Run the probe first.** Not the ingest.

    pixi run usgs-probe

It calls `/time-series-metadata` for each configured gauge and prints every
(parameter, statistic) series with its period of record:

      10336660 — Blackwood Creek nr Tahoe City, CA
        code   parameter                              stat   units      begin       end
        ------------------------------------------------------------------------------
        00060  Discharge                              00011  ft3/s      1961-01-01  2026-09-17
        ...
        NOTE: configured but not reported: 63680

That last line is the point. `USGS_GAUGES` in config lists the parameters
we *want*; the probe reports what each gauge actually serves, and flags the
difference both ways — configured-but-absent, and available-but-unconfigured.
Reconcile config against the probe output before trusting the gauge cards.

Then pull data and rebuild:

    pixi run usgs
    pixi run transform
    pixi run serve

## Verified against the live API, 2026-09-17

`pixi run usgs-probe` returned real series metadata for all four gauges.
Findings, and the config changes each drove:

### The statistic collision was real

A parameter code alone does **not** identify a series. Blackwood publishes
water temperature under five statistics simultaneously:

| Parameter | Statistic | Meaning | Period |
|---|---|---|---|
| `00010` | `00001` | daily max | 1980-12-06 → 2025-09-29 |
| `00010` | `00002` | daily min | 1980-12-06 → 2025-09-29 |
| `00010` | `00003` | daily mean | 2016-10-01 → 2025-09-29 |
| `00010` | `00008` | daily median | 2015-01-20 → 2016-09-30 |
| `00010` | `00011` | **instantaneous** | 2015-01-20 → 2026-09-17 |

Turbidity has four. Keying `variable` on the parameter code alone would
have collapsed max, min, mean and instantaneous into one number, silently
keeping whichever arrived first.

Fixed three ways: the client pins `statistic_id=00011` on both fetchers,
the transform carries `statistic_id` through and includes it in the dedupe
key, and the card builder groups on `(parameter, statistic)` and labels
anything that isn't instantaneous — so `Water temp (max)` can never be
mistaken for a live reading.

### Blackwood turbidity confirmed

`63680`, instantaneous, **2015-01-20 → present**. This is the independent
cross-check on Sunnyside's −1.98 FNU offset: same measure, same units,
different instrument, different agency.

Glenbrook has **no** turbidity series at all — that measure is west-shore
only.

### The discharge record is longer than advertised

| Series | Period |
|---|---|
| `00060` daily mean | **1960-10-01** → 2026-09-15 |
| `00060` instantaneous | 1987-10-02 → 2026-09-17 |

Nearly 66 years of daily mean discharge. Note the README's "record to 1961"
is slightly off; daily mean begins 1960-10-01.

### Three parameters we didn't know to ask for

- **`00300` dissolved oxygen**, instantaneous and current at *both*
  Blackwood (from 2024-10-11) and Glenbrook (from 2024-11-12). Directly
  comparable to the EXO sondes' `Do_mgL`. Added.
- **`63160` stream level, NAVD88**, Blackwood, 2023-09-11 → present.
  Referenced to a surveyed vertical datum, unlike TEON's
  `Uncalibrated_water_depth`. Absolute rather than relative. Added.
- **`80154` / `80155` suspended sediment** concentration and discharge,
  Blackwood, 1974-10-01 → 1992-09-29, daily mean. Historical only, so not
  ingested — but suspended sediment is the direct physical driver of Tahoe
  clarity loss, which makes an 18-year record of it genuinely valuable for
  baseline work via `/daily`. Catalogued in `USGS_PARAMETERS`.

### One gauge dropped

**`10336725`** (Glenbrook Creek at Old Hwy 50) has only two series,
discharge and gage height, both ending **2000-05-01**, with no
instantaneous data at all. It's a historical station. Moved to
`USGS_HISTORICAL_GAUGES` so the archival value is recorded, and removed
from the hourly ingest.

### Two parameters removed from config

- `00010` at `10337000` (Lake Tahoe at Tahoe City) — no water temperature
  series exists there. Gage height only. Its `32400` daily series does run
  back to **1957-10-01**.
- `00095` specific conductance at Glenbrook — the instantaneous series
  ended 2024-11-07. At Blackwood it only ever covered 1980–1983.

### Units differ from my guesses

The API returns `ft^3/s`, `_FNU` (leading underscore), `uS/cm`, `mg/l`,
`degC`. `USGS_PARAMETERS` keeps our own display units; the raw string
arrives on every observation as `unit_of_measure` and is used as a fallback.

### Rate limit confirmed working

`X-RateLimit-Remaining` came back as 999 then 998 across four probe
requests, against the keyed cap of 1000/hour. Authenticated requests are
being counted correctly.

## Still unverified

- **Comma-separated `parameter_code` filtering.** The probe didn't exercise
  it; only `fetch_continuous` and `fetch_latest` do. If the gauge cards come
  back with parameters we didn't request, drop the filter and screen
  client-side instead.
- **`monitoring-locations` filtering by `id`.** `monitoring_location()` uses
  `id=USGS-<num>`; it may want `monitoring_location_number`. Affects only
  the optional site-metadata lookup, not ingest.
- **The `/continuous` endpoint itself.** The probe exercised
  `/time-series-metadata` only. `pixi run usgs` is the first real call to
  `/continuous`.
