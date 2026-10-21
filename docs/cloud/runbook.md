# Runbook: moving SQL Server databases to Azure, and proving nothing changed

*Written 2026-10-21, from the secchi practice migration
([cloud.md](../cloud.md)).* secchi is one database with 9 tables and 12
million rows. This runbook is the same method written for a team moving
hundreds or thousands of databases. Each step says what to do, what
"done" looks like, and what secchi taught about it.

The rule underneath every step: **a move isn't finished until a check
proves the copy matches.** A log line saying "loaded" isn't proof.

## 0. Before anything moves

1. **Decide the target, per database.**
   - Azure SQL Managed Instance behaves most like on-premises SQL Server
     (SQL Agent, cross-database queries), so it's usually the easiest first
     home for an enterprise geodatabase.
   - Azure SQL Database suits a single self-contained database. secchi went
     here.
   - SQL Server on an Azure virtual machine is the fallback when something
     needs the whole server.
   - Before choosing for a geodatabase, check Esri's support matrix for the
     ArcGIS and SQL Server versions in use.
2. **Classify the data.** Mark anything restricted (criminal justice,
   health, personal) before it goes anywhere, including into test copies,
   logs and AI tools. Restricted data follows its own rules for where it
   may live.
3. **Set a budget and alerts on day one.** Cloud bills grow quietly. secchi
   used the free Azure SQL offer set to pause when the free compute runs
   out, plus a $5 budget alert as a backstop.
4. **Write down the auth plan.**
   - Logins with the least power that works.
   - No passwords in files or scripts.
   - Federated sign-in (OpenID Connect) for automation.

## 1. Inventory

For every database and table, list:

- rows, columns, size and date range;
- what feeds it and what reads it;
- an owner;
- whether it moves: move, retire, or wait.

A large share of a big estate turns out to be retired, duplicated or
unused, and the cheapest table to migrate is the one you don't.

At the same time, **fingerprint every table by month** (see step 4). This
is the "before" picture, taken before anyone touches anything.

- **Done when:** every table has an owner, a decision and a fingerprint.
- **secchi:** `pixi run cloud-inventory` writes
  [inventory.md](inventory.md). It found three bookkeeping files that
  shouldn't move, and three that could wait.

## 2. Plan the waves

Group databases into waves.

- **Wave 1:** small, well-understood databases with few readers, to prove
  the method.
- **Middle waves:** bigger and busier databases.
- **Last:** anything edited all day by many people, or anything using
  traditional versioning or replication. These need the most testing.
  Branch versioning works through feature services.

Each wave gets:

- a date;
- a rollback plan (the old database stays read-only and available until
  the wave is signed off);
- a parallel-run period.

## 3. Copy

1. Create the schema from scripts in version control, never by hand in a
   console. The same scripts build the test copy and the real one.
2. Bulk-copy the data. With `bcp`, stop on any file whose row count comes
   out wrong.
3. Build the indexes and spatial columns **after** the load. This is
   faster, and it keeps extra columns out of the way of the bulk copy.
4. Copy first, fix later. Reshaping, renaming and cleanup are separate
   changes, made after the copy is proven. Otherwise you can't tell a
   migration bug from an intended change.

**secchi:**

- [01_schema.sql](../../cloud/sql/01_schema.sql), `bcp`, then
  [02_after_load.sql](../../cloud/sql/02_after_load.sql).
- 12 million rows reached Azure in 277 seconds, mostly upload time.

## 4. Prove it: reconcile

For each table and month, compute the same numbers on both sides and
require them to be **exactly** equal:

| Check | Catches |
|---|---|
| Row count, distinct keys | missing or duplicated rows |
| Key checksum (sum of a hash of every row key) | a row attached to the wrong site, feature or variable |
| Earliest and latest time | time-zone shifts, truncated months |
| Smallest and largest value, exactly | precision lost in transit |
| Sum of floor(value × 1000) | any change of 0.001 or more |
| Count of missing values | blanks becoming zeros or the reverse |
| Spatial test: which polygon each point falls in, both engines | projection mistakes, broken geometry |

Comparing month by month tells you **where** a difference is, not just
that there is one, so a repair reloads one month instead of everything.

**No allowances.** secchi's first check let value sums differ by 0.0001
per row for rounding. A deliberate test change of +1 slipped through,
because over 60,000 rows the allowance added up to 6. If two engines
can't agree exactly on a number, change what you compute until they can.
For secchi that meant summing floor(value × 1000) instead of rounded
decimals.

- **Done when:** every table-month matches, and the report is saved with
  the wave.
- **secchi:** `pixi run cloud-reconcile`. 131 of 131 table-months match,
  12,108,026 rows, with 0 spatial differences.

## 5. What the reconcile will find

Expect these. secchi hit each one, and each was caught by a check, not by
luck:

- **Letter case.** SQL Server's default collation ignores case.
  `Temperature` and `temperature` were different variables in the source
  and became a duplicate-key error. Use a case-sensitive (binary)
  collation for codes and names that came from a case-sensitive system.
- **Text encoding.** One note held a garbled apostrophe. Move it
  byte-for-byte into a UTF-8 column, and let the checksum prove it
  arrived unchanged. Don't fix data during the move.
- **Projection.** A polygon file was in Web Mercator metres with no CRS
  declared. Reproject it once, with the same function the old system
  used, and check it with a spatial test in both engines.
- **Platform differences.** `bcp` spells a line ending differently on
  Windows and Linux, and a table with one more column than its file
  shifts every row. Test the loader on the platform that will run it.
- **Hidden bad data.** The first query on the copy showed a lake at 85 °C.
  That was a failed sensor, already known. It moved unchanged, which is
  correct. Quality views label it; the raw table keeps it.

## 6. Parallel run

Keep the old and new systems running side by side. On a schedule:

1. reconcile;
2. reload only the table-months that differ;
3. reconcile again;
4. log the result.

Because the reconcile decides the work, revised history is caught too,
and so are new rows.

**Test the repair on purpose.** In CI, delete a month and change one value,
then require the sync to find and fix exactly those two table-months.

- **Done when:** a set number of clean runs in a row; secchi uses every
  run. Then readers switch over.
- **secchi:** `pixi run cloud-sync`, every other morning from GitHub
  Actions:
  - the sync logs a row to `secchi.run_log`;
  - a failed run opens an issue;
  - the sabotage test runs on every code change.

## 7. Switch readers over

1. Point GIS clients and reports at the new database with read-only
   logins.
2. Rebuild layers from views that live in version control and are
   recreated on every sync, so the map can't drift from the data.
3. Check one or two numbers on the map against the old system by hand.

For ArcGIS Pro, distance matters: Pro talking to a geodatabase in the cloud
can feel slow. Common fixes:

- run Pro in Azure Virtual Desktop next to the data;
- edit through feature services.

**secchi:**

- QGIS reads five map views as `secchi_reader`.
- Before the views went live, they were checked against the source: 2,738
  camera frames, the latest flows (for example Trout Creek 12.5 cfs) and
  322 gauge-days.

## 8. Watch the cost

- Measure what one run costs; don't estimate it.
- A serverless database bills every second it's awake, and the auto-pause
  delay counts.
- On the Azure free offer that delay is fixed at 60 minutes. A two-minute
  sync therefore costs an hour, about 1,800 vCore-seconds.
- The lever is how often it runs. secchi moved from nightly to every other
  day, about 27,000 of the 100,000 free a month.
- Record the remaining budget with every run, so cost is a dataset, not a
  surprise.

## 9. Sign off and retire

When the parallel run has been clean for the agreed period:

1. Readers are on the new system.
2. The reconcile reports are filed with the wave.
3. The old database goes read-only, then is archived on the agreed date.

Write down what the wave taught, and carry it into the next wave's
runbook.

## Checklist for one wave

- [ ] Target chosen; data classified; budget alert set
- [ ] Inventory row and owner for every table; fingerprints taken
- [ ] Schema scripts in version control; tested on the target platform
- [ ] Load stops on any wrong row count
- [ ] Reconcile: every table-month exact, spatial test clean, report saved
- [ ] Sabotage test passes (delete a month, change a value, sync repairs exactly those)
- [ ] Parallel run clean for the agreed period
- [ ] Read-only logins for readers; views in version control
- [ ] Cost per run measured and logged
- [ ] Old system read-only; archive date set; lessons written down
