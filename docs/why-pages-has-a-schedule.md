# Why pages.yml has its own schedule

## The bug

`pages.yml` triggers on pushes touching `data/processed/**`, and
`fetch.yml` commits exactly that every hour. So the chain should be:
fetch ingests → commits → pages redeploys.

It never fired once.

**GitHub Actions does not create new workflow runs from events triggered
by the repository's default `GITHUB_TOKEN`.** The rule exists to prevent
recursive workflow loops — a workflow that commits, triggering itself,
forever. The consequence here is that `secchi-bot`'s hourly data commit
fires nothing at all.

The page only ever updated when a human pushed from their own credentials,
or dispatched `pages` by hand. Which is exactly the symptom: fresh data in
the repo, stale numbers on the site.

## Three ways to fix it

**1. A personal access token in fetch.yml.** Pushes made with a PAT *do*
trigger workflows. But it means a credential to create, store and rotate,
and it grants the bot's commits the ability to trigger any workflow in the
repo. For a public repo publishing a dashboard, that's more attack surface
than the problem justifies.

**2. Have fetch.yml dispatch pages explicitly** via the Actions API.
Works, and keeps the chain explicit — but it couples the two workflows,
and a failure in the dispatch step is a silent staleness bug of exactly
the kind this project has already been bitten by twice.

**3. Give pages.yml its own schedule.** Chosen.

## Why the schedule is the right answer here

`pages.yml` already runs its own `ingest-all` and `usgs` before
transforming. It was written that way so a manual push would publish
current data rather than whatever was last committed. Which means it
needs **nothing** from `fetch.yml` — the two workflows are already
independent:

| Workflow | Responsibility | Commits? |
|---|---|---|
| `fetch` | maintain the durable parquet record, prune the raw buffer | yes |
| `pages` | publish a current view of it | no |

So scheduling `pages` isn't a workaround. It makes an existing
independence explicit, and removes a coupling that was never actually
working.

## Timing

```
:00   fetch    ingest both agencies -> transform -> prune -> commit
:25   pages    ingest both agencies -> transform -> deploy
```

Twenty-five minutes past. Long enough that fetch has finished — runs have
been comfortably under a minute — so the checkout already carries the
freshest parquet and the ingest only tops it up. Short enough that the
published page is never more than about 35 minutes behind the newest
observation.

The `push` trigger stays, so a code change still deploys immediately.

## The cost

Double-ingesting each hour:

| | per run | per hour | limit |
|---|---|---|---|
| TEON | 23 requests | 46 | none documented |
| USGS | ~11 requests | 22 | 1,000/hr keyed |

USGS use goes to **2.2 %** of the budget. TEON publishes no limit; 46
requests an hour against an AWS App Runner container is negligible, and
the client sends a descriptive User-Agent so the traffic is attributable
if they ever want to ask about it.

If that ever needs trimming, the cheap option is dropping the ingest steps
from `pages.yml` and building `latest.json` from the committed parquet —
fetch keeps it current, so the page would be at most an hour stale rather
than 35 minutes. Not worth doing at current volumes.

## The general lesson

This is the third silent-staleness failure in the project, after the
`statistic_id` comma list and the Web Mercator polygons. All three shared
a shape: **something returned success while doing nothing.** The fetch
workflow was green every hour. The commits were real. The data was
current. And the published page was hours old, because the link between
them was never connected.

Green does not mean working. It means nothing crashed.
