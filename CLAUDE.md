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
