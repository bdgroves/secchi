# Open questions

Updated 2026-09-18 after running all three probes. Two closed, one
narrowed, one still needs a browser.

---

## 1. Watershed layer — **FOUND AND READ**

Three endpoints, no auth, no tiles:

| Endpoint | Contents |
|---|---|
| `tahoeenvironmentalobservatorynetwork.org/data/TahoeWatersheds.json` | Catchment polygons, static file |
| `rftazkiysf…/api/watersheds/variables` | 168 variable definitions with labels and units |
| `rftazkiysf…/api/watersheds/attributes` | One record per catchment, ~168 fields |

**The rain shadow is 3.03×** — 1,459 mm at Watson Creek on the northwest
shore down to 482 mm at Deadman Point on the east, across about twenty
kilometres. Full findings, the variables worth using, five data-quality
problems and the integration plan are in
[`watershed-layer.md`](watershed-layer.md).

Remaining work is implementation, not discovery.

---

## 2. Camera imagery — **CLOSED: not publicly reachable**

`pixi run camera-probe`, 2026-09-18. All five candidate HTTPS forms fail:

```
403 AccessDenied      virtual-hosted (no region)
403 AccessDenied      virtual-hosted (us-west-2)
403 AccessDenied      path-style     (us-west-2)
301 PermanentRedirect virtual-hosted (us-east-1)
301 PermanentRedirect path-style     (us-east-1)
```

Two things learned. The bucket **is** in us-west-2 — the us-east-1
redirect is S3 telling us we asked the wrong region. And anonymous reads
are blocked by bucket policy.

One caution on interpretation: S3 returns `AccessDenied` for a *missing*
key too when `s3:ListBucket` is denied. So this proves we can't read the
objects, not that they exist.

**Routes left**, in order of effort:
1. A TEON-side proxy their own frontend uses. DevTools on a field-camera
   detail page that actually renders an image would reveal it. Worth
   trying, because their site must resolve these somehow.
2. Ask TEON for public reads or presigned URLs. Sudeep Chandra
   (`sudeep@unr.edu`) is the contact listed on their StoryMap.

Until then the dashboard correctly reports that 3,392 frames exist with
their capture times, and doesn't pretend they're displayable.

---

## 3. Parameter code `70369` — still unanswered, my instructions were wrong

The tool works; the command I gave you didn't. `pixi run usgs-params --
--codes 70369` forwards the literal `--` through to argparse, which
rejects it. Use either:

    pixi run usgs-params --codes 70369
    pixi run python -m secchi.ingest --mode usgs-params --codes 70369

The second form is immune to however pixi handles argument forwarding.

Worth also running it with no arguments, which checks every code already
in `USGS_PARAMETERS` against USGS's own definitions and would catch any
label or unit I got wrong.

---

## 4. The dormant fleet — **three of four unlocked**

`pixi run probe` across all sensor types, live and dormant:

| Type | Slug | Records | Status |
|---|---|---|---|
| Hobo | `/sensors/hobo-sensor` | 214,378 | ✓ resolved |
| Stream Chemistry | `/sensors/stream-chemistry` | 124,620 | ✓ resolved |
| Precipitation Gauge | `/sensors/precipitation-gauge` | 593,507 | ✓ resolved |
| Minidot | — | 265,340 | still missing |

**932,505 records became reachable.** All three are now single-candidate
confirmed entries in `SENSOR_TYPE_SLUGS`.

The precipitation gauge matters most: it's the forcing variable the
transect has been missing, from TEON's own instrument, complementing the
catchment precipitation climatology from the watershed layer.

### MiniDot: I missed the candidate the pattern predicted

Confirmed convention from the resolved slugs is `{name}-sensor` with the
name **not** word-split — `EXO` → `exo-sensor`, `Hobo` → `hobo-sensor`.
The first probe tried `mini-dot`, `minidot` and `mini-dot-sensor` but not
**`minidot-sensor`**, which is exactly what that convention predicts.
Added as the lead candidate; re-run `pixi run probe` to test it.

### A caveat on the 2.6 M headline

The probe reports 2,635,395 records catalogued upstream, but that
**double-counts the shared logger channels** — Air Temperature and Soil
Environmental Conditions both report 463,574 at the same seven stations
because they are projections of one Campbell logger table. True unique
volume is meaningfully lower.

### Pulling it is a separate job

`DEFAULT_INGEST_PAGE_SIZE` caps each sensor at 200 records per run, so
`ingest-history` gets recent history for everything rather than archives.
Nearly a million records needs a deliberate paginated backfill with a
raised cap, run once, off the cron — and given the repo-growth work we
just did, it should write straight into the parquet record rather than
leaving a million rows of raw JSON behind.
