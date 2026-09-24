# Why `git pull` no longer stops on parquet

## The problem

The store is committed, and two writers touch the current month's
partition: the hourly job in CI, and a local `transform` or `backfill`.
Git can't merge binary content, so every time both had changed it,
`git pull --rebase` stopped and asked for a hand resolution:

    git checkout --theirs <partition>
    git add <partition>
    git rebase --continue
    pixi run transform

That happened on most pushes.

## Why not just stop committing the current month

It was suggested here once, and it would lose data. The raw buffer only
keeps seven days, so a CI run starting without the current month's
partition could rebuild only the last week of it. Everything older in
that month — including rows a backfill had recovered — would be gone.

## The fix: a merge driver

A partition is an append-only set of readings, keyed by record id, site
and variable. Merging two versions has a well-defined answer, the same
one the hand resolution reached: keep every reading either side has,
once. Git lets a repository define how to merge a file type, so
`src/secchi/merge_parquet.py` does exactly that.

It's a **three-way** merge against the common ancestor, so deliberate
changes survive:

| Situation | Result |
|---|---|
| a reading added on either side | kept |
| a reading removed on one side (a purge) | removed |
| a reading changed on one side (a repair) | the changed version |
| changed on both sides | ours |

Plain union would resurrect purged rows and undo repairs whenever the
other side still held the old copy.

It runs in about 2.5 seconds on a 500,000-row partition: keys and rows
are hashed, and the decisions are set operations on the hashes. A hash
collision would silently merge two different readings, so the driver
checks for one and declines to merge if it finds one — git then reports
an ordinary conflict.

## Setup, once per clone

    pixi run setup-git

This registers the driver in the clone's git config, which is the one
part git doesn't let a repository carry itself. CI runs the same command
before its push-retry rebase.

**A clone without it is no worse off.** Git falls back to reporting the
conflict and leaving a readable file; it does not attempt a text merge
on the binary. That was tested before relying on it.

## Checking it's active

    git check-attr merge -- data/processed/observations/source=teon/year=2026/month=09/part.parquet

should print `merge: parquet-union`. The order of lines in
`.gitattributes` decides this: later lines win, and `binary` expands to
"don't merge", so the general `*.parquet binary` rule must come before
the store rule. An earlier draft had them the other way round and the
driver never ran. `test_parquet_merge_driver_is_wired` asks git the same
question, so that mistake can't come back unnoticed.

## What it can't handle

A partition **deleted** on one side and changed on the other is a
modify/delete conflict, which git resolves before any driver runs. That
would take a `drop-undated` on one side racing an hourly write to the
same undated partition — unlikely, and resolved the old way if it
happens.
