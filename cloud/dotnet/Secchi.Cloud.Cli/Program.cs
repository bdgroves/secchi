// secchi-cloud: the C# half of the SQL Server copy (docs/cloud.md, "C# and .NET").
//
//   secchi-cloud files    fingerprint the export's bcp files, compare with the store
//   secchi-cloud load     bulk-copy the export into SQL Server with SqlBulkCopy
//   secchi-cloud check    read every row back from SQL Server, fingerprint it in C#,
//                         compare with the store
//
// The connection comes from the same environment variables the Python side
// uses (SECCHI_SQL_SERVER, _DATABASE, _USER, _PASSWORD, _TRUST_CERT). The
// password is never printed.

using System.Data;
using System.Diagnostics;
using System.Text.Json;
using Microsoft.Data.SqlClient;
using Secchi.Cloud.Core;

var root = RepoRoot();
var bcpDir = Path.Combine(root, "cloud", "out", "bcp");
var fingerprints = Path.Combine(root, "cloud", "out", "fingerprints.json");

try
{
    return args.FirstOrDefault() switch
    {
        "files" => Files(),
        "load" => Load(),
        "check" => Check(),
        _ => Usage(),
    };
}
catch (Exception e) when (e is SqlException or FormatException or InvalidOperationException or IOException)
{
    Console.Error.WriteLine($"secchi-cloud: {e.Message}");
    return 2;
}

int Usage()
{
    Console.Error.WriteLine("usage: secchi-cloud files | load | check   (see docs/cloud.md)");
    return 2;
}

// ---------------------------------------------------------------------------
// files: are the export files a faithful copy of the store?
// ---------------------------------------------------------------------------
int Files()
{
    var t0 = Stopwatch.StartNew();
    var prints = FileSource.Fingerprint(bcpDir, Console.Out);
    var rc = StoreCompare.Report("files (C#)", StoreCompare.Compare(fingerprints, prints), Console.Out);
    Console.WriteLine($"  {t0.Elapsed.TotalSeconds:F0}s");
    return rc;
}

// ---------------------------------------------------------------------------
// load: SqlBulkCopy, the .NET way to do what bcp does
// ---------------------------------------------------------------------------
int Load()
{
    using var conn = Open();
    using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(bcpDir, "manifest.json")));
    var t0 = Stopwatch.StartNew();
    foreach (var m in manifest.RootElement.EnumerateArray())
    {
        var table = m.GetProperty("table").GetString()!;
        var expected = m.GetProperty("rows").GetInt64();
        var layout = Layouts.All[table];
        CheckSchema(conn, layout);

        var t1 = Stopwatch.StartNew();
        using var bulk = new SqlBulkCopy(conn, SqlBulkCopyOptions.TableLock, null)
        {
            DestinationTableName = $"secchi.[{table}]",
            BatchSize = 100_000,          // the same batch size bcp uses (-b 100000)
            BulkCopyTimeout = 0,          // no time limit: obs is 12 million rows
        };
        foreach (var c in layout.Columns)
            bulk.ColumnMappings.Add(c.Name, c.Name);

        // Rows go up in chunks of one batch: read 100,000 lines into a
        // DataTable, send them, reuse the table. Memory stays flat however
        // big the file is.
        var chunk = NewTable(layout);
        long sent = 0;
        foreach (var row in BcpFile.Read(Path.Combine(bcpDir, m.GetProperty("file").GetString()!), layout))
        {
            chunk.Rows.Add(row.Select(v => v ?? DBNull.Value).ToArray());
            if (chunk.Rows.Count == bulk.BatchSize)
            {
                bulk.WriteToServer(chunk);
                sent += chunk.Rows.Count;
                chunk.Clear();
            }
        }
        if (chunk.Rows.Count > 0) { bulk.WriteToServer(chunk); sent += chunk.Rows.Count; }

        // Count what the server holds, not what we think we sent.
        var have = Scalar(conn, $"SELECT COUNT_BIG(*) FROM secchi.[{table}]");
        Console.WriteLine($"  {table,-16} {have,12:N0} rows  {t1.Elapsed.TotalSeconds,6:F1}s");
        if (have != expected || sent != expected)
            throw new InvalidOperationException($"{table}: expected {expected:N0} rows, sent {sent:N0}, table holds {have:N0}");
    }
    Console.WriteLine($"load (SqlBulkCopy): {t0.Elapsed.TotalSeconds:F0}s");
    return 0;
}

static DataTable NewTable(Layout layout)
{
    var t = new DataTable();
    foreach (var c in layout.Columns)
        t.Columns.Add(c.Name, c.Type switch
        {
            ColumnType.Guid => typeof(Guid),
            ColumnType.Int16 => typeof(short),
            ColumnType.Int32 => typeof(int),
            ColumnType.Int64 => typeof(long),
            ColumnType.Float => typeof(double),
            ColumnType.DateTime2 => typeof(DateTime),
            ColumnType.DateTimeOffset => typeof(DateTimeOffset),
            _ => typeof(string),
        });
    return t;
}

/// <summary>The live table must have exactly the columns the layout expects, in order.</summary>
static void CheckSchema(SqlConnection conn, Layout layout)
{
    using var cmd = new SqlCommand($"SELECT TOP (0) * FROM secchi.[{layout.Table}]", conn);
    using var r = cmd.ExecuteReader(CommandBehavior.SchemaOnly);
    var live = r.GetColumnSchema();
    var want = layout.Columns.Select(c => $"{c.Name} {c.Type}");
    var got = live.Select(c => $"{c.ColumnName} {TypeOf(c.DataTypeName!)}");
    if (!want.SequenceEqual(got))
        throw new InvalidOperationException(
            $"secchi.{layout.Table} doesn't match the layout in Layouts.cs:\n  expected {string.Join(", ", want)}\n  found    {string.Join(", ", got)}");
}

static string TypeOf(string sqlType) => sqlType switch
{
    "uniqueidentifier" => nameof(ColumnType.Guid),
    "smallint" => nameof(ColumnType.Int16),
    "int" => nameof(ColumnType.Int32),
    "bigint" => nameof(ColumnType.Int64),
    "float" => nameof(ColumnType.Float),
    "datetime2" => nameof(ColumnType.DateTime2),
    "datetimeoffset" => nameof(ColumnType.DateTimeOffset),
    "varchar" or "nvarchar" => nameof(ColumnType.Text),
    _ => sqlType,
};

// ---------------------------------------------------------------------------
// check: every row back out of SQL Server, fingerprinted in C#
// ---------------------------------------------------------------------------
int Check()
{
    using var conn = Open();
    var t0 = Stopwatch.StartNew();
    var variables = new Dictionary<short, string>();
    foreach (var r in Rows(conn, "SELECT variable_id, variable FROM secchi.variable", []))
        variables[(short)r[0]!] = (string)r[1]!;

    var storeParts = StoreParts();
    var prints = new Dictionary<(string, string), MonthPrint>();
    foreach (var spec in Specs.All(variables))
    {
        var t1 = Stopwatch.StartNew();
        var layout = Layouts.All[spec.Layout];
        var cols = string.Join(", ", layout.Columns.Select(c =>
            spec.Name == "watershed" && c.Name == "wkt" ? "CAST(NULL AS varchar(max)) AS wkt" : c.Name));
        var from = $"SELECT {cols} FROM secchi.[{spec.Name}]";
        if (!spec.Monthly)
            Specs.Accumulate(spec, Rows(conn, from, []), prints);
        else
        {
            // One query per month, so only one month's keys are in memory at a
            // time. The months to ask for: every month either side has.
            var utc = layout.Columns.First(c => c.Name == "ts").Type == ColumnType.DateTimeOffset;
            var listSql = utc
                ? $"SELECT DISTINCT CONVERT(char(7), SWITCHOFFSET(ts, '+00:00'), 120) FROM secchi.[{spec.Name}]"
                : $"SELECT DISTINCT CONVERT(char(7), ts, 120) FROM secchi.[{spec.Name}]";
            var months = Rows(conn, listSql, []).Select(r => (string)r[0]!)
                .Union(storeParts.Where(p => p.Item1 == spec.Name).Select(p => p.Item2))
                .OrderBy(p => p, StringComparer.Ordinal);
            foreach (var part in months)
            {
                var start = DateTime.ParseExact(part + "-01", "yyyy-MM-dd", null);
                SqlParameter[] range = utc
                    ? [new("@a", SqlDbType.DateTimeOffset) { Value = new DateTimeOffset(start, TimeSpan.Zero) },
                       new("@b", SqlDbType.DateTimeOffset) { Value = new DateTimeOffset(start.AddMonths(1), TimeSpan.Zero) }]
                    : [new("@a", SqlDbType.DateTime2) { Value = start },
                       new("@b", SqlDbType.DateTime2) { Value = start.AddMonths(1) }];
                Specs.Accumulate(spec, Rows(conn, from + " WHERE ts >= @a AND ts < @b", range), prints);
            }
        }
        var n = prints.Values.Where(p => p.Table == spec.Name).Sum(p => p.Rows);
        Console.WriteLine($"  {spec.Name,-16} {n,12:N0} rows  {t1.Elapsed.TotalSeconds,6:F1}s");
    }
    var rc = StoreCompare.Report("check (C#, from SQL Server)", StoreCompare.Compare(fingerprints, prints), Console.Out);
    Console.WriteLine($"  {t0.Elapsed.TotalSeconds:F0}s");
    return rc;
}

HashSet<(string, string)> StoreParts()
{
    using var doc = JsonDocument.Parse(File.ReadAllText(fingerprints).Replace("NaN", "null"));
    return doc.RootElement.GetProperty("rows").EnumerateArray()
        .Select(r => (r.GetProperty("tbl").GetString()!, r.GetProperty("part").GetString()!))
        .ToHashSet();
}

static IEnumerable<object?[]> Rows(SqlConnection conn, string sql, SqlParameter[] ps)
{
    using var cmd = new SqlCommand(sql, conn) { CommandTimeout = 0 };
    cmd.Parameters.AddRange(ps);
    using var r = cmd.ExecuteReader(CommandBehavior.SequentialAccess);
    while (r.Read())
    {
        var row = new object?[r.FieldCount];
        for (var i = 0; i < row.Length; i++)
            row[i] = r.IsDBNull(i) ? null : r.GetValue(i);
        yield return row;
    }
}

static long Scalar(SqlConnection conn, string sql)
{
    using var cmd = new SqlCommand(sql, conn) { CommandTimeout = 0 };
    return Convert.ToInt64(cmd.ExecuteScalar());
}

// ---------------------------------------------------------------------------

static SqlConnection Open()
{
    string Need(string k) => Environment.GetEnvironmentVariable(k) is { Length: > 0 } v ? v
        : throw new InvalidOperationException($"set {k} first (see docs/cloud.md)");
    var cs = new SqlConnectionStringBuilder
    {
        DataSource = Need("SECCHI_SQL_SERVER"),
        InitialCatalog = Need("SECCHI_SQL_DATABASE"),
        UserID = Need("SECCHI_SQL_USER"),
        Password = Need("SECCHI_SQL_PASSWORD"),
        Encrypt = SqlConnectionEncryptOption.Mandatory,
        TrustServerCertificate = Environment.GetEnvironmentVariable("SECCHI_SQL_TRUST_CERT") == "1",
        ConnectTimeout = 60,              // a paused Azure database takes a minute to wake
    };
    var conn = new SqlConnection(cs.ConnectionString);
    conn.Open();
    return conn;
}

static string RepoRoot()
{
    for (var d = new DirectoryInfo(Environment.CurrentDirectory); d != null; d = d.Parent)
        if (File.Exists(Path.Combine(d.FullName, "pyproject.toml"))) return d.FullName;
    throw new InvalidOperationException("run from inside the secchi repo");
}
