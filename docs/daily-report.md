# The daily report

`pixi run daily` prints a morning summary: what changed in the last 24 hours,
what's still open, then the numbers (lake, forest stations, lake level and
outflow, Beyond TEON). Run `pixi run transform` first; it reads the page
snapshot and the store. Code: `src/secchi/daily.py`; tests: `tests/test_daily.py`.

## What counts as a change

| Change | Rule |
|---|---|
| Anything in the page's "What's new" list | its time falls in the last 24 h |
| Pipeline stalled | no CI fetch in 12 h, or no new reading from any station in 18 h |
| Battery crossed the floor | overnight low went from ≥ 11.5 V to below it |
| Frost | any forest station's air at or below 0 °C |
| Lake swing | a sonde's 24 h mean water temperature moved ≥ 1 °C from the day before |
| Turbidity jump | 24 h max ≥ 1 FNU and ≥ 2× the 30-day median (so 4H Camp's −5.6 offset isn't news) |
| Rain | an airport recorded precipitation this week, in the last two days of data |

"Still open" repeats the known issues (stations quiet more than 36 h,
batteries not charging or below 11.5 V) so a quiet day still shows them.

Each station's "last 24 h" ends at its own newest reading, because stations
upload in 12-hour batches (`upload-cadence.md`). A section that couldn't be
checked is listed under "Couldn't check", never shown as fine.

## The scheduled run

A Claude scheduled task runs it every morning around 7 am Pacific and sends
the result as a push notification and email. It works from what CI last
committed (the cloud workspace can't reach TEON), in a fresh environment:

```bash
git clone --depth 1 https://github.com/bdgroves/secchi && cd secchi
python3 -m venv /tmp/v && /tmp/v/bin/pip install -q pandas pyarrow duckdb httpx python-dateutil pydantic tzdata
export PYTHONPATH=src TZ=America/Los_Angeles
/tmp/v/bin/python -m secchi.transform      # about a minute
/tmp/v/bin/python -m secchi.daily --out daily.md
```

Locally, in PowerShell: `git pull`, then `pixi run transform`, then `pixi run daily`.
