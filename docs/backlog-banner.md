# Knowing when to run a backfill

The banner above the map appears whenever TEON has published records
this store hasn't pulled, and says which command pulls them. It
disappears when there's nothing to do.

## Two cases

**Hand-collected sites.** Someone has been out to the instruments and
uploaded. The coverage cards already tracked this; the banner puts it at
the top of the page.

**Returning stations.** A telemetered station comes back after an
outage and its logger uploads the backlog it kept while the radio was
down. The hourly job only fetches each sensor's newest 200 records —
about 50 hours at 15-minute readings — so anything older is never
reached. Without the banner, that backlog would sit upstream with
nothing on the page saying so.

| Station | Outage | Backlog | What happens |
|---|---|---|---|
| Glenbrook 2 | ~1 day | ~100 readings | Hourly job closes it; no banner |
| Blackwood 2 | ~97 days | ~9,300 readings | Hourly job gets 200; **banner** |

## How the gap is measured

Upstream record count against records held — but per **device**, not
per endpoint.

A forest station's soil, air and tree endpoints are one logger, reporting
identical counts. The backfill fetched only one of them and stored the
whole history under that name, so in the store:

    soil    full history + recent rows
    air     recent rows only
    tree    recent rows only

Comparing air's upstream count with air's held rows would report a
40,000-record backlog at every forest station. So sensors are grouped by
upstream count — the same way the backfill identified shared loggers —
and "held" is the largest held count in each group.

Excluded, because each would false-alarm permanently:

- **hidden sites** — never ingested, so their gap is their whole record
- **cameras** — their records live in the assets table, not observations
- **hand-collected sensors** — they have their own banner entry

A gap under 100 records is treated as noise; the hourly job would already
have closed anything real that small.

## The command is derived, not hardcoded

The first version of the banner said `pixi run backfill --stage manual`
for every hand-collected site. Six of the eight are MiniDOT and HOBO
sites, which live in the `nearshore` stage — that command would have
fetched nothing for them.

Each entry now asks `backfill.STAGES` which stage covers its sensors, so
tallac_lake says `--stage nearshore`, Meeks says `--stage manual`, and a
returning Blackwood 2 would say `--stage live` (plus `--stage blackwood`
if its rain gauge had a backlog too).

## Verified

Against a store built to reproduce the real storage quirk:

    everything current                   no banner
    Blackwood 2 back, 9,300 backlog      9,100 records   --stage live
    Glenbrook 2 back, ~100 backlog       no banner
    tallac_lake and Meeks uploaded       --stage nearshore / --stage manual

The first row is the important one: no false alarm at the forest
stations, the hidden site or the cameras.
