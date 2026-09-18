# Parquet conflicts, and why they happen

## The problem

`data/processed/*.parquet` are committed on purpose — they accumulate
across runs and are the durable record, which is what lets `data/raw/` be
pruned on a 7-day window instead of growing without bound.

But they are **binary**, and git cannot merge binary content. Both the
hourly cron and any local `pixi run transform` write them. So the moment
local and remote history diverge, git hits a conflict it has no mechanism
to resolve:

    warning: Cannot merge binary files: data/processed/usgs_observations.parquet
    CONFLICT (content): Merge conflict in data/processed/usgs_observations.parquet

This bit on 2026-09-18 and took a rebase, a stray cherry-pick and a
detached HEAD to untangle.

## Why it's safe to resolve carelessly

Take either side. It genuinely does not matter, because
`pixi run transform` **accumulates**: it reads the existing parquet, merges
everything currently in `data/raw/`, deduplicates on the source's own
record ids (TEON's per-observation UUID, USGS's feature id), and writes
back. Any rows the chosen side lacked come straight back from the raw
snapshots.

So the fix is always:

    git checkout --theirs data/processed/<file>.parquet
    git add data/processed/<file>.parquet
    git rebase --continue
    # then, once the rebase finishes:
    pixi run transform
    git add -A && git commit -m "reconcile parquet" && git push

Repeat the first three lines for each file git names.

## What's been done to stop it recurring

**`.gitattributes` marks them unmergeable:**

    *.parquet binary -merge -diff

`-merge` makes git stop *attempting* a content merge. It still reports the
conflict, but it won't mangle the file trying to reconcile it, and the
resolution above is then unambiguous. `-diff` keeps `git log -p` from
dumping binary noise.

**The cron no longer merges.** `fetch.yml` now, before regenerating:

    git fetch origin main
    git checkout origin/main -- data/processed/

It takes the remote's parquet, then lets `transform` rebuild from
`data/raw/`. Because transform accumulates, the result holds both sides'
rows regardless of which copy it started from — so the cron never pushes a
parquet that diverged. The push also retries up to three times with
`pull --rebase -X theirs`, and aborts cleanly rather than forcing if that
fails.

## The alternative we didn't take

Stop committing the parquet and keep only `data/raw/`. That removes the
conflict entirely, but it also removes the reason raw can be pruned — the
repo would go back to unbounded growth, roughly 23 MB/day at current
settings. The accumulating parquet is what makes a 7-day raw window safe,
so the conflict is a cost worth paying for it.

A middle option, if this keeps being annoying: write the parquet only in
CI and never locally, with local runs using a gitignored scratch path.
That fully separates the writers. It costs the ability to inspect the real
record locally, which is worth more than the occasional conflict for now.

## If it gets truly tangled

Local commits are recoverable from the bundles, and `data/raw/` lives on
the remote:

    git rebase --abort
    git reset --hard origin/main
    pixi run transform

That discards local commits back to the remote state and rebuilds the
parquet. Costs whatever wasn't pushed; loses nothing irreplaceable.
