"""secchi in SQL Server and Azure SQL Database.

A practice migration: the partitioned parquet store is the system of
record, and a SQL Server copy (local, in CI, or Azure SQL Database) has to
prove it matches. Four steps, one command each:

    pixi run cloud-inventory    what exists, and a fingerprint of every month
    pixi run cloud-export       bcp-ready files in cloud/out/bcp/
    pixi run cloud-load         schema + bulk load (sqlcmd and bcp)
    pixi run cloud-reconcile    the database's fingerprints against the store's

See docs/cloud.md. Nothing here writes to the store.
"""
