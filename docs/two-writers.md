# One file, two writers

Three times now this project has produced a git conflict because two
things wrote the same file. The pattern is worth naming, because the
right fix is different each time.

| File | Writers | Fix |
|---|---|---|
| `data/processed/*.parquet` | CI cron + local `transform` | Mark unmergeable; CI takes the remote copy and regenerates |
| `web/assets/latest.json` | CI deploy + local `transform` | Gitignore it — generated at deploy time, never committed |
| `data/reference/watch_baseline.json` | CI `watch` + local `watch` | **Remove the second writer** |

## Why the baseline is different

For the parquet, both writers genuinely need to write: CI maintains the
record, and a local `transform` has to produce something to serve. So the
fix was to make the conflict harmless — mark the file unmergeable, take
either side, regenerate.

The watch baseline isn't like that. A local run is **diagnostic** — you
want to see what changed. It has no reason to advance a shared baseline,
and doing so causes a second, worse problem.

## The bug hiding underneath the conflict

If a local run advances the baseline, it **consumes the change**.

Run `pixi run watch` locally. It reports that Meeks has received an
upload. It writes the new baseline. CI runs six hours later, compares
against that new baseline, finds nothing, and never opens the issue.

You would have destroyed the notification by looking for it.

That's worse than the conflict, and it would have been very hard to
diagnose — the system would simply never tell you about the one event it
exists to catch.

## The fix

```
pixi run watch          read-only. Reports the diff, writes nothing.
pixi run watch-update   advances the baseline. CI only.
```

`run_watch(write_baseline=False)` is now the default, and the CLI flag
`--update-baseline` opts in. `watch.yml` calls `watch-update`; nothing
else should.

The first run is an exception — with no baseline there's nothing to
consume and nothing to conflict with, so it always writes one.

When a read-only run does find changes it says so explicitly:

```
(baseline not advanced — this was a read-only check. CI updates it.)
```

Without that line, someone would reasonably assume the change had been
acknowledged.

## The general rule

**Before committing a generated file, ask who else writes it.**

If the answer is "more than one thing", there are only three good
outcomes:

1. Remove a writer (the baseline).
2. Make the conflict harmless and documented (the parquet).
3. Don't commit it at all (`latest.json`).

Picking none of those is how you get a conflict at the least convenient
moment, which is how all three of these were discovered.
