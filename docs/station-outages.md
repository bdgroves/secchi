# When a station goes dark

Two of TEON's seven terrestrial stations have stopped: **Blackwood 2**
in June, **Glenbrook 2** on 2026-09-22. Both took every channel with
them — air, soil, tree stress, stream level, camera — so the cause is
something shared, not a sensor fault.

## Neither was a flat battery

Campbell loggers report `BattV_Avg`, their own supply voltage. The real
record says:

| Station | Final mean | Final min | Floor | 30-day trend |
|---|---|---|---|---|
| Blackwood 2 | 12.98 V | 12.91 V | 12.84 V | −0.039 V/wk |
| Glenbrook 2 | 12.75 V | 12.72 V | 12.58 V | −0.039 V/wk |

**Both were on healthy power right up to the moment they stopped.** A
12 V lead-acid supply at 12.8–13.0 V is mid-range on charge, nowhere
near the ~11.5 V where a logger stops writing.

So the cause is **telemetry, the logger, or physical damage** — not
power. Which is the better outcome, because a logger that lost only its
radio has been recording the whole time. Blackwood 2 alone would be
three months of west-shore precipitation we currently treat as lost.

You'll know which when it comes back: if the gap backfills, it was
telemetry.

## The first version of this got it wrong

It reported *"Blackwood 2: power failure with a visible run-up"* on a
battery sitting at 12.84 V. It triggered on the **trend** while ignoring
the **level**.

At −0.039 V/week from 12.84 V, the battery had **eight months** of
headroom. That slope is drift or seasonal variation, not a failure in
progress. The same slope at 11.6 V would matter a great deal.

Fixed two ways:

- **Level before trend.** A healthy final voltage rules out power
  whatever the slope was doing.
- **Trend expressed as headroom.** `weeks_to_floor` projects the
  current floor forward at the observed slope, and the flag fires when
  that is under twelve weeks. A slope with no headroom behind it means
  nothing.

## And three false alarms

The same run flagged Glenbrook 1, 2 and 5 as at risk. All three were
artifacts:

**Glenbrook 5** — flagged on a 30-day floor of 10.90 V while trending
**up** at +0.276 V/week. It had a bad spell and recovered; the flag was
reading history as a forecast. The "current floor" is now the last
**seven** days.

**Glenbrook 2** — "declining" at a 12.58 V floor. The same level-blind
trigger.

**Glenbrook 1** — mean, minimum and 30-day floor all exactly **11.45 V**.
Three statistics landing on one number means there is essentially one
distinct value in the window. That's a data question, not a battery
question, and it's still open.

## Reading the output

The **overnight floor** carries more signal than the mean: a battery
recovering to 13 V each afternoon but touching 11.6 V at dawn is in
more trouble than one sitting flat at 12.2 V. The floor is what stops
the logger.

Thresholds are advisory — comfortable above 12.0 V, at risk below
11.5 V — with the real figures depending on chemistry, temperature and
shared load.

## Will it happen to others?

On the corrected logic, **no station currently shows a declining supply
with limited headroom**. Whatever took Blackwood 2 and Glenbrook 2 is
not visible in the remaining five.

That's a more useful answer than the alarm the first version raised, and
it took being wrong to get there.
