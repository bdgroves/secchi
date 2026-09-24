"""Merge two versions of a store partition, so git never has to ask.

Git calls this during a merge or rebase when both sides have changed the
same parquet file — in practice the current month, which the hourly job
and a local transform or backfill both write. Without it, git can only
report a conflict, because it can't merge binary content, and every
``git pull --rebase`` stopped for a hand resolution.

A partition is an append-only set of readings, each identified by the
source's record id plus site and variable. So merging two versions has a
well-defined answer, the same one the hand resolution reached by taking
one side and re-running the transform: keep every reading either side
has, once.

This does a proper **three-way** merge against the common ancestor, so
deliberate changes on one side survive:

    added on either side          kept
    removed on one side           removed   (a purge, a drop-undated)
    changed on one side           the changed version  (a repair)
    changed on both sides         ours

Plain union would resurrect a purged row or undo a repair whenever the
other side still held the old copy. Comparing with the ancestor tells
the two apart.

Installed per clone by ``pixi run setup-git``. A clone without it falls
back to git's old behaviour — the conflict is reported and the file left
readable — so an unconfigured machine is no worse off than before.

Git invokes it as::

    python -m secchi.merge_parquet %O %A %B %P

and expects the merged result written to %A, exit 0 for a clean merge.
Any failure exits non-zero, which makes git report an ordinary conflict
rather than accept a bad merge.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Keys, by table, matching what the store deduplicates on. Chosen from
# the path git passes as %P.
KEYS = {
    "usgs_observations": ["uuid", "variable", "statistic_id"],
    "assets": ["uuid", "site", "kind"],
    "observations": ["uuid", "site", "variable"],
}


def _key_for(path: str) -> list[str]:
    for table in ("usgs_observations", "assets", "observations"):
        if f"/{table}/" in path.replace("\\", "/") or path.startswith(f"{table}/"):
            return KEYS[table]
    return KEYS["observations"]


def _read(path: str):
    import pandas as pd
    p = Path(path)
    # Git passes an empty file for the ancestor when the partition didn't
    # exist at the merge base (a month both sides created).
    if not p.exists() or p.stat().st_size == 0:
        return None
    return pd.read_parquet(p)


def merge_frames(base, ours, theirs, key: list[str]):
    """Three-way merge of partitions, as sets of rows identified by ``key``.

    Vectorised: a partition can hold hundreds of thousands of readings,
    and this runs inside ``git pull``. Each row is reduced to a key string
    and a hash of its other columns; the decisions are set operations on
    those, not a Python loop over rows.
    """
    import pandas as pd

    if ours is None and theirs is None:
        return None
    if ours is None:
        return theirs
    if theirs is None:
        return ours

    for k in key:
        if k not in ours.columns or k not in theirs.columns:
            raise ValueError(f"key column {k!r} missing — refusing to guess")

    # Line the columns up: a newer partition may carry a column (USGS
    # `approval`, say) that the other lacks.
    extra = list(base.columns) if base is not None else []
    cols = list(dict.fromkeys(list(ours.columns) + list(theirs.columns) + extra))
    values = [c for c in cols if c not in key]

    present = [f for f in (ours, theirs, base) if f is not None]

    # Value columns whose dtype differs between versions are compared as
    # text, so a float64 column in one side and an object column in the
    # other don't register as a change. Everything else is hashed in its
    # native dtype, which is ~15x faster than converting to text.
    def _dtype(f, c):
        return str(f[c].dtype) if c in f.columns else None
    as_text = [c for c in values
               if len({_dtype(f, c) for f in present if c in f.columns}) > 1]

    def prep(df):
        df = df.reindex(columns=cols)
        # Keys are hashed to integers: membership tests on 500k text keys
        # took 3.5 s each, on integers they're effectively free.
        kh = pd.util.hash_pandas_object(df[key].astype(str), index=False)
        df = df.assign(_k=kh.to_numpy(dtype="uint64"))
        df = df.drop_duplicates(subset="_k", keep="first")
        vv = df[values].copy()
        for c in as_text:
            vv[c] = vv[c].astype(str)
        # Nullable UInt64 keeps full precision through the left merge
        # below; a plain uint64 would silently become float64 where a key
        # is missing, and two different hashes could then compare equal.
        h = pd.util.hash_pandas_object(vv, index=False)
        return df.assign(_h=h.astype("UInt64").to_numpy()).reset_index(drop=True)

    o, t = prep(ours), prep(theirs)
    b = prep(base) if base is not None and len(base) else None

    # A 64-bit collision among ~1M keys is roughly a 1-in-30-million
    # event, but if one happened two different readings would silently
    # become one. Check, and refuse — git then reports an ordinary
    # conflict, which is safe.
    frames_k = [f[key + ["_k"]] for f in (o, t, b) if f is not None]
    allk = pd.concat(frames_k, ignore_index=True).drop_duplicates()
    if allk["_k"].nunique() != len(allk):
        raise ValueError("key hash collision — declining to merge")
    base_keys = b["_k"] if b is not None else pd.Series([], dtype=object)

    # Added on one side only: not on the other side, and not in the base.
    added_o = o[~o["_k"].isin(t["_k"]) & ~o["_k"].isin(base_keys)]
    added_t = t[~t["_k"].isin(o["_k"]) & ~t["_k"].isin(base_keys)]

    # On both sides: take theirs only when ours is unchanged from the base
    # and theirs has changed it. Everything else keeps ours.
    both = o[["_k", "_h"]].merge(t[["_k", "_h"]], on="_k", suffixes=("_o", "_t"))
    if b is not None:
        both = both.merge(b[["_k", "_h"]].rename(columns={"_h": "_h_b"}),
                          on="_k", how="left")
        ours_unchanged = (both["_h_o"] == both["_h_b"]).fillna(False)
        theirs_changed = (both["_h_t"] != both["_h_b"]).fillna(False)
        theirs_wins = (ours_unchanged & theirs_changed).astype(bool)
    else:
        theirs_wins = pd.Series(False, index=both.index)

    keep_t = set(both.loc[theirs_wins, "_k"])
    keep_o = set(both.loc[~theirs_wins, "_k"])

    merged = pd.concat([
        o[o["_k"].isin(keep_o)],
        t[t["_k"].isin(keep_t)],
        added_o,
        added_t,
    ], ignore_index=True)[cols]

    if "timestamp" in merged.columns:
        merged = merged.sort_values("timestamp", kind="stable")
    return merged.reset_index(drop=True)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 3:
        print("usage: merge_parquet BASE OURS THEIRS [PATH]", file=sys.stderr)
        return 2
    base_p, ours_p, theirs_p = argv[:3]
    path = argv[3] if len(argv) > 3 else ours_p

    try:
        merged = merge_frames(_read(base_p), _read(ours_p), _read(theirs_p),
                              _key_for(path))
        if merged is None:
            return 1
        # Write to a temp file and replace, so a failure part-way leaves
        # git's copy of ours intact rather than truncated.
        out = Path(ours_p)
        tmp = out.with_name(out.name + ".merging")
        merged.to_parquet(tmp, index=False)
        tmp.replace(out)
    except Exception as exc:                       # any doubt: let git conflict
        print(f"merge_parquet: {path}: {exc}", file=sys.stderr)
        return 1

    print(f"merge_parquet: {path}: merged {len(merged):,} rows", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
