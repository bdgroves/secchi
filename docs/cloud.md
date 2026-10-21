# secchi in SQL Server and Azure SQL Database

*Started 2026-10-21.* A practice migration: copy the whole store into SQL
Server (local, in CI, then Azure SQL Database) and prove the copy matches,
month by month. The parquet store stays the system of record; nothing here
writes to it.

It's the same job as moving a county's SQL Server geodatabases to Azure, at
a size one person can finish: about 12 million rows in 9 tables, plus
lookups and polygons.

## The four steps

| Step | Command | What it does |
|---|---|---|
| 1. Inventory | `pixi run cloud-inventory` | Lists every table and reference file: rows, months, time span, size, what feeds it, what reads it, and whether it moves. Writes [docs/cloud/inventory.md](cloud/inventory.md) and a fingerprint of every table-month to `cloud/out/fingerprints.json`. |
| 2. Export | `pixi run cloud-export` | Writes one bcp-ready text file per table to `cloud/out/bcp/` (about 870 MB). |
| 3. Load | `pixi run cloud-load` | Runs `cloud/sql/01_schema.sql` (drops and recreates the tables), bulk-copies each file with `bcp`, then `02_after_load.sql` (indexes and spatial columns). Stops if any file's row count comes out wrong. |
| 4. Reconcile | `pixi run cloud-reconcile` | Has SQL Server compute the same fingerprints from what it holds and compares them with the store's. Also checks which catchment each TEON site falls in, SQL Server's `STContains` against secchi's own Python join. Exit code 1 on any difference. |

Steps 1 and 2 need nothing beyond pixi. Steps 3 and 4 need Microsoft's
`sqlcmd` and `bcp` (mssql-tools18) on PATH, and the connection in the
environment:

| Variable | Example |
|---|---|
| `SECCHI_SQL_SERVER` | `localhost`, or `yourserver.database.windows.net` |
| `SECCHI_SQL_DATABASE` | `secchi` |
| `SECCHI_SQL_USER` | a SQL login |
| `SECCHI_SQL_PASSWORD` | its password; never put it in a file in the repo |
| `SECCHI_SQL_TRUST_CERT` | `1` only for a local server with a self-signed certificate |

## What "matches" means

For every table and every month, both sides compute:

- the number of rows, and the number of distinct keys;
- a key checksum: the first 4 bytes of each row key's MD5, as an unsigned
  number, summed. A missing row, an extra row, or a row attached to the
  wrong site or variable changes it;
- the earliest and latest time;
- the smallest and largest value, compared exactly (floats travel as text
  with full precision, and SQL Server prints them with 17 digits);
- the sum of floor(value × 1000), which catches any change of 0.001 or more (values above 10^12 left out:
  TEON has a few impossible readings around 10^26, and those are
  checked by the min and max instead);
- the number of missing values.

Everything must be equal, to the last digit.

**A correction (2026-10-21).** The first version summed values rounded to 4
places and allowed 0.0001 per row for rounding. The sync's sabotage test
changed one USGS reading by 1 and the check let it through: over a month of
60,000 readings the allowance added up to 6. Removing the allowance showed
something worse: the 4-place sums had never matched exactly, off by a few
thousandths in 21 TEON months, because DuckDB and SQL Server round a binary
float to a decimal differently in rare edge cases. The first "131 of 131"
results were real for rows, keys, times and min/max, but the value sums were
passing on the allowance. The check is now the sum of floor(value × 1000):
multiplying is bit-identical in both engines and floor has no edge cases, so
it is exact, and it catches any change of 0.001 or more.

## How the tables are laid out

- `secchi.obs`, the 12 million TEON readings, stores numbers instead of
  names: lookup tables `secchi.site` (with a geography point per station),
  `secchi.sensor_type` and `secchi.variable`. It gets a clustered
  columnstore index after the load, which compresses it and suits
  month-by-month scans.
- Every other table moves as it is in the store, same columns and names.
  Reshaping comes later, as its own change: copy first, fix later.
- `secchi.watershed` holds the 60 catchments as `geometry` in SRID 4326.
  The source file is Web Mercator metres; it's converted with the same
  function the catchment join uses (`reproject_geojson`), so the Python
  and SQL Server checks look at one geometry.
- Times: TEON's are naive logger time (see [data-dictionary.md](data-dictionary.md));
  USGS's are `datetimeoffset`, stored in UTC, and counted by UTC month.

## Things the inventory turned up

- **One TERC note isn't plain text.** The 2024-06-19 Secchi reading's
  note reads "doesnâ€™t": a curly apostrophe garbled before it reached
  secchi (worth checking whether it's in TERC's file or in our download).
  It moves as it is: that column uses a UTF-8 collation, and the note is part
  of the row key, so the reconcile proves it arrived byte for byte.
- **Two pairs of names differ only by capital letters.** MiniDOT reports
  `Temperature` and HOBO reports `temperature`; the stream stations report
  `Specific_Conductivity` and the EXO sondes `specific_conductivity`. SQL
  Server's default collation ignores case, so the first load stopped with a
  duplicate-key error on the variable lookup. The lookup names and the
  fingerprint keys now use a binary (case-sensitive) collation.
- **The catchment file is in metres, not degrees.** No CRS is declared in
  it; secchi detects Web Mercator by checking where the inverted
  coordinates land.
- **Not everything moves.** `watch_baseline.json`, `news_log.json` and
  `terc_package.json` are the app's own bookkeeping; copying them would
  only make a second copy to keep in step. Three more reference files wait
  until a report needs them. The inventory says which is which.

## Where it runs

**GitHub Actions** (`.github/workflows/cloud.yml`): a throwaway SQL Server
2022 container, all four steps, the reconcile report as the job summary.
Runs when this code changes, Mondays, and by hand.

**Azure SQL Database**, from your PC: below. And every other morning from
GitHub Actions (`.github/workflows/cloud-azure.yml`): see "Keeping Azure
current" below.

## Keeping a copy current: `cloud-sync`

`pixi run cloud-sync` is the command a schedule runs. It:

1. fingerprints the store and asks the database for its fingerprints;
2. reloads **only the table-months that differ**: delete that month, bulk
   copy that month. Because the reconcile decides the work, revised history
   (TEON and USGS both revise old readings) is caught as well as new days.
   More than 24 differing table-months, or a changed catchment file, means
   a full reload instead. An empty database gets a full load;
3. keeps the lookup ids stable: a new site or variable gets the next id on
   the server, and existing ids never move;
4. refreshes the quality views and runs the full reconcile;
5. checks the quality flags (below);
6. writes a row to `secchi.run_log`: what it reloaded, rows, months
   matched, flags matched, seconds, the commit, and (from the workflow) the
   free compute left this month.

CI tests it the way the Handoff tested its port: after a full load it
deletes a month of TEON readings and changes one USGS value, and the sync
must find and reload exactly those two table-months and match again.

## Quality views

`secchi.obs_flagged` and `secchi.obs_clean` are generated
(`secchi.cloud.quality`) from the same rules the export and the watcher use
(`IMPOSSIBLE`, `OPTICAL_CHANNELS`), not rewritten by hand. The raw table is
never changed; `obs_clean` leaves out readings flagged `impossible_range`,
`channel_scramble` or `ph_zero`, and keeps the notes (`exo_sat_sea_level`,
the negative optical readings). Every sync checks that SQL Server flags
exactly as many readings as DuckDB, flag by flag: on 2026-10-21, 11,481
impossible readings, 54,150 scrambled-channel readings, 173,392 sea-level
saturation notes, 125,010 negative turbidity, 83,811 negative chlorophyll,
32,597 negative phycocyanin and 46,841 dead pH channels, the same in both.
Not ported yet: `repeated_hour` and the duplicate flags.

```sql
-- in SSMS, on the secchi database
SELECT site, YEAR(ts) AS yr, MONTH(ts) AS mo, ROUND(AVG(value), 2) AS temp_c
FROM secchi.obs_clean
WHERE sensor_type = 'ExoSensor' AND variable = 'Temp'
GROUP BY site, YEAR(ts), MONTH(ts) ORDER BY site, yr, mo;
```

## Map views

Also generated in `secchi.cloud.quality` (`MAP_VIEWS`) and recreated on
every sync, so a GIS client (QGIS, ArcGIS Pro) reads them with the
read-only `secchi_reader` login. Each has a stable integer key and a
`location` geography (SRID 4326).

| View | One row per | Shows |
|---|---|---|
| `site_map` | TEON site (18) | kind (lake sonde, nearshore logger, forest and stream station), its instruments, catchment, readings, first and last, camera frames |
| `lake_temp_month` | EXO sonde and month | mean water temperature from `obs_clean`; month start and end for a time slider |
| `usgs_gauge_map` | USGS gauge (10) | inflow, lake level or outflow; latest discharge, gage height, water temperature (UTC) |
| `usgs_flow_day` | gauge and day | daily mean discharge, Pacific standard days, for a time slider |
| `context_station_map` | SNOTEL or airport station (6) | network, latest snow depth. Beyond TEON: context only |

Checked against DuckDB on 2026-10-21: 2,738 camera frames, the latest flows
(Trout Creek 12.5 cfs, Truckee River outflow 78.7 cfs) and 322 gauge-days
are the same in both.

## Keeping Azure current (GitHub Actions)

`cloud-azure.yml`, every other morning at 6:52 AM Pacific, and by hand:

- **Signs in without a stored Azure password.** GitHub issues the run a
  short-lived OpenID Connect token; Microsoft Entra trusts tokens from this
  repo's `main` branch only (app registration `secchi-github-actions`, two
  federated credentials, one per GitHub subject format). The app's only
  role is SQL Server Contributor on `secchi-brooks`.
- **Opens the firewall for its own address only**, for the length of the
  job, and closes it in an always-run step.
- **Logs in to the database as `secchi_loader`**, a user that exists only in
  the `secchi` database (db_owner there, nothing on the server). Its
  password is a GitHub encrypted secret: the one stored credential.
- **Records the free compute left** after each run (Azure Monitor metric
  `free_amount_remaining`) in `secchi.run_log`.
- **Opens a GitHub issue** if a run fails.

Settings, in the repo (Settings → Secrets and variables → Actions): variables
`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`,
`SECCHI_SQL_SERVER`; secrets `SECCHI_SQL_USER`, `SECCHI_SQL_PASSWORD`.

### What it costs

A serverless database bills its minimum every second it's awake, and stays
awake for the auto-pause delay after the last connection. On the free offer
that delay is **fixed at 60 minutes**: changing it fails with *"Only default
value for auto pause delay is allowed for Free Limit database with auto
pause exhaustion behavior."* Measured on 2026-10-21, the database burns
about 25 vCore-seconds a minute awake, so a run plus its idle hour costs
about 1,800 of the 100,000 free a month. Nightly would be about 55,000;
every other day, about 27,000, which leaves room for working in SSMS. With
"auto-pause until next month" set, running out stops the database instead
of billing.

Disconnect SSMS from Azure when done: an open connection keeps the database
awake.

## Azure SQL Database, step by step

### 1. Make the database (Azure portal, once)

1. Sign in at portal.azure.com. Search **Azure SQL**, choose
   **SQL databases → Create**.
2. At the top, take the **free offer** ("Apply offer"). It gives one
   serverless database with 32 GB and 100,000 vCore-seconds a month.
3. Resource group: new, `secchi-rg`. Database name: `secchi`.
4. Server: **Create new**. Name it something unique, like `secchi-brooks`
   (it becomes `secchi-brooks.database.windows.net`). Location: West US 3.
   (On 2026-10-21 a new subscription was refused West US 2: "Your
   subscription does not have access to create a server in the selected
   region." West US 3 worked.) Authentication: **Use both SQL and Microsoft
   Entra authentication**: SQL login `secchiadmin` for the loader, your
   Microsoft account as Entra admin for the portal. Keep the password in
   1Password.
5. **Behavior when free limit reached: Auto-pause the database until
   next month.** This is the setting that makes it free. The other choice
   bills you.
6. Networking: **Public endpoint**, and **Add current client IP address:
   Yes**. Leave "Allow Azure services" off.
7. Create. It takes a few minutes.

### 2. Set a budget alert (once)

Search **Budgets** → Add. Scope: your subscription. Amount: $5 a month.
Alerts at 50% and 100% to your email. With the free offer set to
auto-pause, this should never fire; it's there in case something else gets
created by mistake.

### 3. Run it from WSL (Ubuntu)

Use WSL rather than Windows PowerShell: pixi environments work there without
Smart App Control blocking them, and Microsoft's Linux tools install with
apt. In the Ubuntu window:

```bash
# once: sqlcmd and bcp. Microsoft's setup package installs the right signing
# key for your Ubuntu (26.04 needs the 2025 key; the old microsoft.asc fails
# with NO_PUBKEY EE4D7792F748182B)
source /etc/os-release
curl -sSL -O https://packages.microsoft.com/config/$ID/$VERSION_ID/packages-microsoft-prod.deb
sudo apt install -y ./packages-microsoft-prod.deb && rm packages-microsoft-prod.deb
sudo apt-get update && sudo ACCEPT_EULA=Y apt-get install -y mssql-tools18 unixodbc
echo 'export PATH="$PATH:/opt/mssql-tools18/bin"' >> ~/.bashrc && source ~/.bashrc

# each session
cd ~/secchi && git pull
export SECCHI_SQL_SERVER=secchi-brooks.database.windows.net
export SECCHI_SQL_DATABASE=secchi
export SECCHI_SQL_USER=secchiadmin
read -s -p "password: " SECCHI_SQL_PASSWORD; export SECCHI_SQL_PASSWORD; echo

pixi run cloud-inventory
pixi run cloud-export
pixi run cloud-load        # uploads about 870 MB; minutes on home internet
pixi run cloud-reconcile
```

If the load can't connect, the usual cause is the firewall: your home IP
changed. In the portal, open the server → **Networking** → add your
current IP.

Afterwards, check what it cost: the database's **Overview** shows the free
vCore-seconds used this month.

## Results

| Where | When | Table-months | Rows | Load | Check | Spatial |
|---|---|---:|---:|---:|---:|---|
| SQL Server 2022, GitHub Actions | 2026-10-21 | 131 of 131 | 12,106,888 | 49 s | 41 s | 0 differences |
| Azure SQL Database (free offer, West US 3), from WSL at home | 2026-10-21 | 131 of 131 | 12,108,026 | 277 s | 82 s | 0 differences |

The Azure load is mostly upload time: `obs` (834 MB) took 178 s.

Why WSL and not PowerShell: Windows Smart App Control blocks the pixi
environment's pandas DLLs ("An Application Control policy has blocked this
file"). The Windows `bcp` and `sqlcmd` installed with SQL Server are fine;
it's the Python side that can't load.

The first query on the Azure copy (monthly EXO temperature) surfaced
Sunnyside at 85-102 °C in May-June 2026: the failed-sonde period already in
[impossible-readings.md](impossible-readings.md). It moved unchanged, as it
should.

Automated runs (cloud-azure.yml), 2026-10-21: three by hand, all
`sync: none` and 131 of 131; about 230 seconds each, most of it the flag
check; free compute left after the third, 95,619 vCore-seconds.

## Runbook and AI use

[cloud/runbook.md](cloud/runbook.md) writes this method up for a team moving
many databases, step by step. [ai-use.md](ai-use.md) says how AI was used to
build it, and the standard it follows.

## Open questions

- Reading the views from ArcGIS Pro (QGIS reads them; see "Map views").
- Port `repeated_hour` and the duplicate flags.
- The flag check is most of a run's time on 2 vCores; it could run weekly
  while the reconcile runs every time.
