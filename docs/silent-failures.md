# Silent failures, and the two that got through

Two bugs in this project produced **HTTP 200 and no data**. Neither raised
an error; both were found by noticing an absence rather than a failure.
Worth recording, because they share a shape.

## 1. `statistic_id` does not accept a comma list

`parameter_code` does:

    parameter_code=00060,00065,00010        -> works, returns all three

`statistic_id` does not:

    statistic_id=00011                      -> 1,145 features
    statistic_id=00011,00006                ->     0 features, HTTP 200

No error. No warning. Just an empty `features` array. This broke **every**
USGS gauge for several hours on 2026-09-18.

**Fix:** parameters are grouped by their statistic and fetched in separate
requests (`group_by_statistic` in `sources/usgs.py`). Only a gauge
carrying a non-default parameter pays the extra call — currently just
Upper Truckee, for `70372`.

**What made it worse:** the evidence was misread. Seeing
`statistic_id=00011%2C00006` in the request URL was taken as confirmation
the API accepted it. That confirmed the URL was *formed* correctly and
nothing else. The feature count was the thing to check, and it was right
there in the same log line.

## 2. `statistic_id=00011` alone hides some parameters

The mirror image, and the bug the above was meant to fix. `70372` is
published under statistic `00006` (Sum), because a load is a daily total.
Pinning `00011` silently omitted the single most regulation-relevant
series at the lake's largest tributary.

Found by reading `usgs-probe` output rather than by anything failing.

## The pattern

Both are **filters that removed more than intended**. Both returned
success. Both were caught by comparing what came back against what should
have.

The same shape appeared twice more in this project:

- `USGS_BBOX` as a rectangle standing in for a watershed pulled in
  fourteen stations draining to the Carson River.
- Web Mercator polygons tested against WGS84 points matched 0 of 28
  stations — no error, just nothing inside anything.

So the operating rule for this codebase: **when a query narrows results,
check the count, not the syntax.** A request that returns zero rows is
indistinguishable from a request that was never going to match, and
neither raises.

## What's been added to catch the next one

- **`sources/usgs.py`** logs a warning when a gauge returns zero features
  for a specific statistic and parameter set, naming both.
- **`fetch.yml`** now surfaces a failed USGS step as a GitHub Actions
  warning annotation and writes a diagnostic block to the run summary.
  `continue-on-error` is still correct — a USGS outage should not cost the
  time-sensitive TEON snapshot — but failing softly is not the same as
  failing invisibly, and previously it was both.
- **The probe modes** (`usgs-probe`, `usgs-discover`, `probe`,
  `reference-inspect`, `camera-probe`) exist for exactly this. They have
  now caught six real bugs between them. Their cost is a few dozen lines
  each; the alternative is plausible-looking wrong data.
