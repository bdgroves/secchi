# secchi — notes for Claude

Read `HANDOFF.md` before starting: setup, current state, open questions and
next steps are there. `README.md` is the public write-up.

## Rules for this repo

- Run `pixi run -e dev test` before committing. All tests must pass. The
  capability test lists what must exist; if it fails, something was deleted.
- Edit files in place. Never recreate a file from an older copy of it —
  features have been silently lost that way at least five times.
- Before replacing text in a file, confirm the target text appears exactly
  once. A replace that matches nothing fails silently.
- Verify against the real thing: query the store, run `git check-attr`, count
  rows. Don't trust a log line or a proxy.
- Honour TEON's `/site-visibility/disabled` list. If it can't be read, fail
  closed: skip, don't guess.
- Backfills append then compact; never read-merge-write the store. Store
  writes are atomic.
- The store is committed to git, so every rewritten byte is kept forever.
  From October 2026 each month is one file per day; rows are written in a
  fixed order and unchanged files are never rewritten. Keep it that way —
  `tests/test_store_layout.py` checks it.
- Forest-station endpoints share record IDs but return different columns.
  Fetch each one; never let one stand in for another.
- `Soil_VWC` is stored as a fraction (display ×100). Timestamps are naive
  Pacific local. Detect events on daily means — soil moisture has a daily cycle.

## Working with the user

- Brooks works in Windows PowerShell. Give PowerShell commands.
- SQL goes at the `secchi>` prompt from `pixi run query`, not in PowerShell.
- Say plainly when something was wrong, including earlier conclusions. Several
  findings here were corrected after the first real run, and the corrections
  are part of the record.
- In the page, calendar days ("2025-09-02") go through `fmtDay`, never
  `new Date(...)`: a date-only string parses as midnight UTC, which shows
  the previous day anywhere west of Greenwich. Test the page with
  `TZ=America/Los_Angeles`.
- The rain panel's numbers come from `transect_rain.summary()`, the same
  code `pixi run transect-rain` prints. Change the analysis there, not in
  the page.
- New page sections render inside their own `try`. `load()` treats any
  error from `render()` as a failed fetch and blanks the page.
- SNOTEL (and anything else not TEON) is context: it lives under
  "Beyond TEON", collapsed, and its map layer sits beneath TEON's pins.

