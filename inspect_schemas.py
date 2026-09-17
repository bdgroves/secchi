"""Print the field schema of the most recent snapshot for each sensor slug.

Run from the repo root:

    pixi run python inspect_schemas.py

Diagnostic only — writes nothing. Used to design variable metadata for
sensor types we've just gained access to.
"""

from __future__ import annotations

import json
import pathlib
from collections import Counter


def main() -> None:
    root = pathlib.Path("data/raw/teon")
    if not root.exists():
        print("no data/raw/teon — run an ingest first")
        return

    for slug_dir in sorted(root.iterdir()):
        if not slug_dir.is_dir():
            continue
        files = sorted(slug_dir.rglob("*.json"))
        if not files:
            continue

        snap = json.loads(files[-1].read_text(encoding="utf-8"))
        recs = snap.get("records", [])
        if not recs:
            print(f"\n{slug_dir.name}: snapshot present but no records\n")
            continue

        print("=" * 76)
        print(f"{slug_dir.name}")
        print(f"  site={snap.get('site')}  records={len(recs)}  "
              f"total_available={snap.get('total_available')}")
        print("=" * 76)

        # Field types across the whole batch, not just the first record —
        # a field can be null in record 0 and populated later.
        types: dict[str, Counter] = {}
        examples: dict[str, object] = {}
        for rec in recs:
            for k, v in rec.items():
                types.setdefault(k, Counter())[type(v).__name__] += 1
                if k not in examples and v is not None:
                    examples[k] = v

        for field in types:
            tc = types[field]
            type_str = ", ".join(f"{t}×{n}" for t, n in tc.most_common())
            ex = repr(examples.get(field, None))
            if len(ex) > 52:
                ex = ex[:49] + "..."
            print(f"  {field:30} {type_str:22} e.g. {ex}")

        # Timestamp range, to sanity-check cadence and the timezone question.
        stamps = sorted(r.get("TIMESTAMP") for r in recs if r.get("TIMESTAMP"))
        if stamps:
            print(f"\n  TIMESTAMP range: {stamps[0]} → {stamps[-1]}")
        print()


if __name__ == "__main__":
    main()
