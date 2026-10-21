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
- the sum of values rounded to 4 places (values above 10^12 left out:
  TEON has a few impossible readings around 10^26, and those are
  checked by the min and max instead);
- the number of missing values.

Everything must be equal. The one allowance: the 4-place sums may differ by
rounding at most 0.0001 per row. Any such difference is reported as a note,
and none is expected.

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

**Azure SQL Database**, from your PC: below.

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
# once: sqlcmd and bcp
curl -sSL https://packages.microsoft.com/keys/microsoft.asc | sudo tee /etc/apt/trusted.gpg.d/microsoft.asc > /dev/null
curl -sSL https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/prod.list | sudo tee /etc/apt/sources.list.d/mssql-release.list > /dev/null
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

## Open questions

- How many vCore-seconds does one full load use? Measure on the first run.
- A nightly reconcile against Azure from GitHub Actions needs the runner
  allowed through the firewall. Options: a GitHub-hosted runner with a
  service principal that adds and removes its own IP rule, or a
  self-hosted runner in WSL.
- Next steps on the SQL side: incremental loads (only new days) instead of
  full reloads, and reading `secchi.obs` from ArcGIS Pro or QGIS as a query
  layer.
