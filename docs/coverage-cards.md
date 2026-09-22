# Knowing where you've been

The hand-collected sondes hold the best records in the network and, until
now, the dashboard showed them as a single sentence. After a backfill
their data would have sat in the parquet **invisible** — `LIVE_EXO_SITES`
gates the lake cards, so Blackwood 3 and Meeks were never going to appear
there.

Which is correct, and not enough.

## Why they don't get a normal card

A live card answers *what is the lake doing right now*. That question has
no good answer for an instrument read once a month: its newest reading
can be twelve weeks old and still be perfectly good data.

Putting a July reading in the same idiom as an hour-old one implies a
currency it doesn't have. So these cards answer two different questions:

- **Where have we been** — the period of record actually held.
- **What's outstanding** — records published upstream but not pulled, and
  therefore what the next collection extends from.

The period of record is the headline. The reading is context.

## What a card shows

```
Meeks                                    west shore · collected by hand

  record from   27 Jan 2026
  through        9 Jul 2026
  span           163.5 days
  held           15,441 of 15,441 records
  ████████████████████████████████████████  100%

  8.4 °C   9.6 mg/L   88.0 %   102.1 %   0.5 µg/L   0.3 FNU
  Temp     DO         DO sat   DO (local) Chl-a      Turbidity

  Up to date with everything published. The next collection will
  extend this record from 9 Jul 2026.
```

And before a backfill:

```
  held           nothing yet
  upstream       15,441 records
  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  0%

  15,441 records published that we haven't pulled.
  Run `pixi run backfill --stage manual` to bring them in.
```

The bar makes the gap legible at a glance in a way two numbers don't.

## Details that matter

**Dashed card border.** These records are complete up to a point and then
simply stop until someone dives. Borrowing the visual language of live
data would be misleading, so they don't.

**Calendar dates, not elapsed time.** Everywhere else the dashboard says
"3 h ago", because for live data that's the useful framing. A period of
record has *edges*, and "3 months ago" is the wrong idiom for one. So
coverage bounds render as `9 Jul 2026`.

**The readings carry the same derived oxygen treatment** as the live
cards — including `DO sat (local)`, since the sea-level referencing issue
applies to these sondes too.

**Sparklines span the held record**, not the last 48 hours, because that
is what there is.

## What this answers about the next run

The card states the last date held and the outstanding count, so the
question *what will the next collection give us* has a number attached
rather than being a guess. After the sondes are next retrieved and
uploaded:

1. `watch` opens an issue — record count jumped on a non-live sensor.
2. The card's `unpulled` count becomes non-zero and the bar drops below
   100 %.
3. The map pin turns ember.
4. A backfill closes the gap and the coverage window extends.
