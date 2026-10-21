# How the C# works: a walkthrough

*Written 2026-10-21.* A plain-words tour of `cloud/dotnet`: what .NET is,
how the two projects fit together, and what each file does. The results are
in [cloud.md](../cloud.md), "C# and .NET".

## .NET in five minutes

- **The SDK** (`dotnet` on the command line) builds and runs code. This repo
  targets .NET 8 (`net8.0`), a long-term-support release supported until
  November 2026. .NET 10 is the newer long-term release; moving up means
  changing `net8.0` to `net10.0` in the two project files.
- **A project** is a folder with a `.csproj` file: an XML list of settings,
  packages and other projects it uses. `dotnet build` turns it into a
  `.dll`.
- **A `.dll` isn't a Windows-only program.** It's compiled to an
  intermediate language that the .NET runtime turns into machine code when
  it runs. So `dotnet secchi-cloud.dll` runs unchanged on your Windows PC,
  on a Linux GitHub runner, and on a Mac.
- **NuGet** is .NET's package library, like pip for Python. The project
  file names a package and a version; `dotnet build` downloads it the first
  time. Here that's `Microsoft.Data.SqlClient`, Microsoft's SQL Server
  driver.
- **ADO.NET** is .NET's standard way to talk to databases. The same four
  ideas appear in every .NET database program: a connection, a command, a
  reader, and (for loading) a bulk copy.

## Two projects, and why

| Project | Builds to | Uses |
|---|---|---|
| `Secchi.Cloud.Core` | a library (`Secchi.Cloud.Core.dll`) | nothing but .NET itself |
| `Secchi.Cloud.Cli` | the `secchi-cloud` program | Core, plus `Microsoft.Data.SqlClient` |

Everything that doesn't need a database lives in Core:

- the table layouts;
- the file reader;
- the fingerprint arithmetic.

Core has no packages, so it builds anywhere, even offline, and it can be
tested against the export files with no SQL Server at all. That's how it was
first proven: C# fingerprinted the 12 million exported rows and matched
DuckDB. The Cli adds only what needs the database.

## Core, file by file

**`Layouts.cs`**: every table, column by column, with its SQL Server type:

```csharp
["obs"] = new("obs", [C("record_uuid", G), C("site_id", I2), C("sensor_type_id", I2),
                      C("ts", DT), C("variable_id", I2), C("value", F)]),
```

- A `record` is a small class whose only job is to hold data. `Layout` and
  `Column` are records.
- `ColumnType` is an `enum`: a fixed list of names (Guid, Int16, Float,
  DateTime2...).
- This file repeats `01_schema.sql` on purpose. The loader compares it with
  the live table before copying anything. `tests/test_cloud_dotnet.py`
  keeps the two in step.

**`BcpFile.cs`** reads an export file:

1. It reads the file one line at a time and splits each line on `|`.
2. It turns each field into its real type: `Guid.ParseExact`,
   `double.Parse`, `DateTime.ParseExact` and so on, using an exact format
   and the invariant culture, so "1.5" means one and a half even on a PC
   set to a language that writes decimals with a comma.
3. A field that doesn't fit stops the read with the file, line and column
   named.

`Read` is an **iterator**: `yield return row` hands back one row at a time,
so a 12-million-row file never sits in memory at once.

**`Fingerprint.cs`** is the arithmetic, done in C# rather than asked of a
database:

- `MD5.HashData` of each key's UTF-8 bytes. The first four bytes are read
  as an unsigned number and summed.
- A `HashSet<string>` with `StringComparer.Ordinal` counts the distinct
  keys. Ordinal means byte-for-byte, so `Temperature` and `temperature` are
  two keys, as in DuckDB.
- `Math.Floor(x * 1000.0)` is added to a `BigInteger`, a whole number with
  no size limit. A `decimal` would quietly round very large doubles to 15
  significant digits. A `BigInteger` built from a whole-number double is
  exact.
- Each month is **sealed** as soon as the next one starts. That keeps the
  count and frees the set, so memory holds one month's keys, not all 12
  million.

`Specs` says what each table contributes to a fingerprint: which columns
make the key, which is the time, and which is the value. The `Func<object?[],
string>` fields are small functions passed around as values (lambdas like
`r => ...`).

**`StoreCompare.cs`** reads `fingerprints.json` with `System.Text.Json` and
compares field by field with no allowances. It also quotes the bare `NaN`
that Python's json writes, because strict JSON doesn't allow it.

**`FileSource.cs`** runs the fingerprint over all the export files.

**`BcpDataReader.cs`** presents an export file as a **data reader**, the
standard ADO.NET shape for "rows you can read forward". `SqlBulkCopy` takes
any reader, so it can pull rows straight from the file. The class is long
only because the base class `DbDataReader` declares about thirty members
abstract. SqlBulkCopy actually calls six of them:

- `Read`
- `FieldCount`
- `GetName`
- `GetOrdinal`
- `IsDBNull`
- `GetValue`

## The program, `Program.cs`

The file uses **top-level statements**: no `Main` method. The code at the
top runs first, and the commands are local functions below it.

**Connecting.** `SqlConnectionStringBuilder` builds the connection string
from the same `SECCHI_SQL_*` variables the Python side reads, so the
password never appears in a file or on screen.
`Encrypt = SqlConnectionEncryptOption.Mandatory` means the connection is
always encrypted. Azure requires that anyway.

**`check`**, reading from SQL Server:

```csharp
using var cmd = new SqlCommand(sql, conn) { CommandTimeout = 0 };
cmd.Parameters.AddRange(ps);                       // @a, @b: the month's range
using var r = cmd.ExecuteReader(CommandBehavior.SequentialAccess);
while (r.Read()) { ... r.IsDBNull(i) ? null : r.GetValue(i) ... }
```

- **Parameters** (`@a`, `@b`) carry the month's dates as typed values,
  never pasted into the SQL text. That's the standard defence against SQL
  injection, and it keeps the time types exact.
- **`using`** closes the reader and command when the block ends, even if
  something fails.
- **`SequentialAccess`** tells the driver to stream each row rather than
  buffer it.

The check runs one query per month, so it needs one month's keys in memory
at a time.

**`load`**, with `SqlBulkCopy`:

```csharp
using var bulk = new SqlBulkCopy(conn, SqlBulkCopyOptions.TableLock, null)
{
    DestinationTableName = "secchi.[obs]", BatchSize = 100_000,
    BulkCopyTimeout = 0, EnableStreaming = true,
};
bulk.WriteToServer(new BcpDataReader(path, layout));
```

- `TableLock` is the same as bcp's `-h TABLOCK`: one lock for the whole
  load instead of one per row, which is much faster on an empty table.
- `BatchSize` matches bcp's `-b 100000`.
- `EnableStreaming` pulls rows from the reader as the server takes them.

Before loading, `CheckSchema` asks the server for the table's columns
(`SELECT TOP (0) *` with `CommandBehavior.SchemaOnly`) and compares them
with the layout. After loading, it counts what the server holds.

There are two loading styles, so they can be raced:

- **Streaming** (the default) uses the reader above.
- **`--batches`** was the first version. It filled a 100,000-row
  `DataTable`, sent it and reused it. `DataTable` is the classic ADO.NET
  in-memory table: easy to use, but every value is boxed into a `DataRow`
  first.

## Things that bit, and why

- **Invariant globalization.** The projects first set
  `InvariantGlobalization`, which makes .NET ignore the machine's language
  settings. That's good for parsing numbers the same way everywhere.
  `Microsoft.Data.SqlClient` then refused to connect: it uses the culture
  data to map SQL Server collations to code pages. Core keeps invariant
  mode; the Cli turns it off. Its parsing still names the invariant culture
  explicitly, so the result is the same.
- **NuGet was blocked** where this was first written, so Core had to build
  with no packages. That turned out to be a better design anyway.

## Where this goes next

The same pieces show up in everyday GIS engineering in .NET:

- **A geodatabase-to-Azure migration:** `SqlBulkCopy` for the rows, a
  reconcile in C#, parameters everywhere.
- **An ArcGIS Pro add-in** (the Pro SDK is C#/.NET).
- **An ASP.NET Core web API** serving GeoJSON from these views.
- **An Azure Function** that runs the sync and signs in with a managed
  identity instead of a password.
