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

## What discovery found, 2026-09-18

First real run: **1,277 stations** in the bounding box, **28 with data in
the last 30 days**. Two of my assumptions were wrong.

### The bounding box was too wide

A rectangle cannot express a watershed. The original box
`(-120.35, 38.80, -119.80, 39.35)` caught fourteen active stations that
drain the wrong way:

- **Carson Range east slope → Carson River basin:** Kings Canyon, Ash
  Canyon, Franktown (×2), Ophir, Davis, Winters, Little Washoe Lake,
  Steamboat.
- **Downstream of the Tahoe outlet → Truckee basin:** Truckee R nr
  Truckee, Donner Lake, Donner Ck (×2), NF Washeshu Ck.

Tightened to `(-120.25, 38.90, -119.90, 39.28)`, which excludes those
while keeping every real Tahoe site. Each exclusion is now recorded in
`USGS_OUT_OF_BASIN` with its reason, and `usgs-discover` marks them `x`
rather than reporting them as unconfigured — so an omission stays a
decision rather than becoming an oversight.

**The proper fix is the catchment polygon layer.** Station membership
should be point-in-polygon against real basin boundaries, not a rectangle.
See [`watershed-layer.md`](watershed-layer.md).

### Five tributaries carry real-time turbidity — we had one

This is the find. Turbidity (`63680`) is the direct clarity measure and the
same one TEON's sondes report, so these are independent cross-checks on
readings like Sunnyside's −2.11 FNU offset.

| Site | Gauge | Shore | Turbidity |
|---|---|---|---|
| `10336610` | Upper Truckee R at South Lake Tahoe — **largest tributary** | south | ✓ |
| `10336780` | Trout Ck nr Tahoe Valley | south | ✓ |
| `10336645` | General C nr Meeks Bay — largely undeveloped catchment | west | ✓ |
| `10336660` | Blackwood C nr Tahoe City | west | ✓ |
| `10336676` | Ward C at Hwy 89 | west | ✓ |
| `10336698` | Third Ck nr Crystal Bay | north | — |
| `10336700` | Incline Ck nr Crystal Bay | north | — |
| `10336730` | Glenbrook Ck at Glenbrook | east | — |

All eight are now configured. Note the geography: turbidity exists on the
west and south shores, not the east or north. Sediment delivery to Tahoe
is concentrated where the precipitation is.

### 131 years of outflow

`10337500` daily mean discharge: **1895-07-01 → present**. Every drop
leaving Lake Tahoe, measured for 131 years.

### And 63158 was the wrong code

I configured `63158` for the outlet's NAVD88 elevation. The probe replied
*"configured but not reported: 63158"* and *"available and not configured:
63160"*. It uses `63160`, same as Blackwood. Corrected.

### One parameter still unidentified

`70369` appears at `10336610` and I don't know what it is. Resolve against
`/collections/parameter-codes/items` before configuring it rather than
guessing.

## Discovering gauges instead of guessing

`pixi run usgs-discover` queries `/monitoring-locations` against a bounding
box for the Tahoe basin (`USGS_BBOX`), then `/time-series-metadata`
filtered to series with data in the last 30 days, and prints every station
with its live parameters — flagging which are active but **not** yet in
`USGS_GAUGES`.

This exists because hand-listing site numbers is unreliable. USGS runs
**seven major Tahoe drainages** under the Lake Tahoe Interagency Monitoring
Program, monitored since the late 1980s for discharge, sediment and water
quality, with real-time turbidity added recently. Blackwood is one of the
seven. Picking gauges from memory is how you quietly omit the other six.

## Lake Tahoe's datum

`10337000` publishes gage height on a local datum, and the raw number is
meaningless without it. Per the USGS station description, the water-stage
recorder's **datum is 6,220.00 ft** above the U.S. Bureau of Reclamation
reference (6,218.86 ft above NGVD 1929), installed **1 October 1957** —
which is exactly when the `32400` daily series begins.

So a 7.07 ft reading is a lake surface at **6,227.07 ft**, and that number
can be compared to things that matter:

| Reference | Elevation | Meaning |
|---|---|---|
| Natural rim | 6,223.00 ft | Below this the lake stops flowing to the Truckee |
| Legal maximum | 6,229.10 ft | Regulatory ceiling |
| Record high | 6,231.26 ft | July 1907 |
| Record low | 6,220.26 ft | 30 November 1992 |

`USGS_DATUMS` in config holds the offset and these reference levels;
`transform.py` emits a derived `Lake elevation` reading with a `vs` block
giving the difference to each, and the dashboard shows them under the
value. At 6,227.07 ft the lake sits 4 ft above its rim and 2 ft below the
legal ceiling.

Contrast `63158 Stream elevation` at the outlet gauge, which USGS already
publishes referenced to **NAVD 1988** — no local datum to apply, directly
comparable between sites.

## Gauges worth ingesting

Confirmed active from earlier searching. **Parameter availability per gauge
is unverified** — the `/time-series-metadata` collection is the correct way
to check what each site actually reports, and should be the module's first
call rather than an assumption.

| Site no. | Name | Relevance |
|---|---|---|
| `10336660` | Blackwood Creek nr Tahoe City, CA | West-shore, record to **1961**. Reports discharge, gage height *and* turbidity. Covers TEON's Blackwood 2 station, offline since June. |
| `10336730` | Glenbrook Creek at Glenbrook, NV | East-shore. Pairs with TEON's Glenbrook 2 stream gauge. |
| `10337500` | **Truckee River at Tahoe City, CA** | **The lake outlet** — 510 ft downstream of the outlet dam, the single channel every drop leaving Lake Tahoe passes through. Flow is completely regulated by that dam, so discharge here is a management decision as much as a hydrologic one. Pairs with `10337000` immediately upstream: lake level, and the rate it is being released. |
| `10336725` | Glenbrook Creek at Old Hwy 50 nr Glenbrook, NV | Historical only — see below. |
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
