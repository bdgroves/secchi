# How the map shows a hand-collected sonde

Two of the five lake sondes are self-logging. They store to internal
memory and are read by hand, roughly monthly, by boat and snorkel.

Until now the map rendered them **grey — "dormant"**, identical to a
failed sensor. That was wrong in a way worth fixing: these instruments
work perfectly. They're on a different clock.

## Three states, not one

| State | Meaning | Colour |
|---|---|---|
| `pin-manual` | Collected by hand, and we hold everything published | hollow ring, blue outline |
| `pin-manual-pending` | TEON has published records we haven't ingested | solid ember |
| *(never dormant)* | — | — |

A hand-collected sonde is never shown as dormant regardless of how old
its newest record is, because age isn't the right signal for an
instrument that reports monthly by design.

## The bug this also fixes

The pin colour comes from TEON's **inventory**, which reports what exists
upstream. The data comes from **ingest**, which only targets live sensors.

Those disagree badly at exactly the moment that matters. When a sonde is
retrieved and uploaded, the inventory jumps by thousands of records and
`last_update` moves to the retrieval date — so the old logic would have
turned the pin **green** while we held none of that data.

A green pin with nothing behind it is worse than a grey one, because it
looks answered.

So `build_map_points` now carries both numbers: `records` from the
inventory, and `held_records` counted from the parquet. Their difference
is `unpulled_records`, and that's what drives the pending state.

The popup says so plainly — *"6,739 records published, not yet pulled"*
rather than a colour you have to interpret.

There's precedent for this in the camera rows, which have always shown
`697 frames (345 held)`. Same idea, applied where it actually changes a
decision.

## The hardcoded sentence

The dashboard used to say:

> Two more EXO sites — *Blackwood 3* and *Meeks*, both west-shore — are
> manual sondes and stopped reporting on 2026-07-09 pending physical
> retrieval.

Every part of that was baked into the HTML: the count, the names, the
shore, the date. It would have gone on saying **2026-07-09** forever,
including after a retrieval made it false — which is the one moment
anybody would be reading it.

It's now built from the inventory via `build_manual_sondes()`, and it
changes its own wording when there's unpulled data:

> Two EXO sites are **self-logging**: they store to memory and are read by
> hand, roughly monthly, by boat and snorkel. Not broken — just on a
> different clock. *Meeks* (west shore) — **6,739 records published that
> we haven't pulled yet**. *Blackwood 3* (west shore) — newest record 3
> months ago. A backfill is needed to bring that history in.

## What happens when they upload, end to end

1. Within an hour, the inventory shows the new `data_count` and
   `last_update`.
2. Within six hours, `watch` notices the record count jumped on a
   non-live sensor and **opens a GitHub issue**.
3. At the next deploy the pin turns **ember** and the text says how many
   records are outstanding.
4. Someone runs the backfill.
5. The pin returns to the hollow hand-collected ring and the count
   reconciles.

Steps 1–3 are automatic. Step 4 is deliberate, because a retrieval
delivers thousands of records at once and that needs pagination and a
partitioned parquet — see [`storage.md`](storage.md).
