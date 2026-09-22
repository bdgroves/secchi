# How a feature disappeared, twice

On 2026-09-22 the catchment layer stopped rendering on the live page. The
cause was not a bug in the layer. It was a file assembled from the wrong
starting point.

## What happened

Bundles in this project are built by copying the previous version of a
file and patching it. The watershed map layer was added to
`transform.py` in one bundle. Two bundles later, the oxygen work copied
`transform.py` from a **stale base** — the version *before* the layer was
added — and patched the oxygen derivation onto that.

The result compiled, passed its tests, and shipped. `write_web_watersheds`
was simply gone. `web/assets/watersheds.geojson` was never generated, so
`snap.watersheds` was undefined and the dashboard's layer loader returned
early without complaint.

The next bundle inherited the deletion.

## Why nothing caught it

Three reasons, all worth noting:

1. **The frontend degrades silently by design.** `addWatershedLayer`
   returns `null` if `snap.watersheds` is missing, so the map still works
   — just without catchments. That's correct behaviour for a missing
   optional layer and exactly wrong for noticing a regression.
2. **Tests covered the new work.** The oxygen derivation was verified
   thoroughly. Nothing checked that everything *else* still existed.
3. **No diff against the previous version.** Each bundle was checked for
   what it added, never for what it removed.

## The check that now exists

Comparing the set of top-level functions between the old and new version
of a file catches this in one line:

```python
import re, pathlib
funcs = lambda p: set(re.findall(r'^def (\w+)', pathlib.Path(p).read_text(), re.M))
lost = funcs(previous) - funcs(new)
```

Run against the two versions, it reported exactly one name:
`write_web_watersheds`. Two seconds to find, once anyone thought to look.

## And a second trap in the fix

Restoring the function needed its import back. The guard was:

```python
if 'WATERSHED_DISPLAY_VARIABLES' not in s:      # wrong
```

That checks the **whole file**, and the name appears in the function body
regardless of whether it's imported. So the guard was False, the import
was never added, and the assertion written to catch that passed for the
same reason.

The fix was to check the import block specifically:

```python
block = re.search(r'from secchi\.config import \((.*?)\)', s, re.DOTALL).group(1)
if 'WATERSHED_DISPLAY_VARIABLES' not in block:  # right
```

## The pattern, for the fifth time

This project's recurring failure is **something reporting success while
doing nothing**:

| | What returned success |
|---|---|
| `statistic_id` comma list | HTTP 200, zero features, every gauge |
| Web Mercator polygons | 0 of 28 stations matched, no error |
| `GITHUB_TOKEN` push | commit landed, no workflow triggered |
| String replace with no match | printed "tasks added" |
| Presence check on the whole file | assertion passed, import missing |

The rule from `silent-failures.md` was *when a query narrows results,
check the count, not the syntax*. This generalises it:

**Check the thing you actually care about, not a proxy that correlates
with it.** A name appearing somewhere in a file is a proxy for it being
imported. Zero rows is a proxy for a working query. A green workflow is a
proxy for a deployed page. Each one held right up until it didn't.
