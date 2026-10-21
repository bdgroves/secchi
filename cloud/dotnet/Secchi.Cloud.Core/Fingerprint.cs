using System.Globalization;
using System.Numerics;
using System.Security.Cryptography;
using System.Text;

namespace Secchi.Cloud.Core;

/// <summary>
/// What one row contributes to a fingerprint: a text key unique per row, a
/// time (which also picks the month), and the main number.
/// </summary>
public sealed record RowSpec(
    string Name,                         // the fingerprint's table name, as in fingerprints.json
    string Layout,                       // the layout the rows arrive in
    bool Monthly,
    Func<object?[], string> Key,
    Func<object?[], DateTime?> Ts,
    Func<object?[], double?> Value);

/// <summary>
/// The fingerprint of one table-month, computed here in C# from the rows
/// themselves. The definitions are the ones in src/secchi/cloud/tables.py,
/// but none of the arithmetic is delegated to a database: this is a third,
/// independent implementation next to DuckDB's and SQL Server's.
/// </summary>
public sealed class MonthPrint
{
    public string Table { get; }
    public string Part { get; }
    public long Rows { get; private set; }
    public long KeyHash { get; private set; }
    public DateTime? TsMin { get; private set; }
    public DateTime? TsMax { get; private set; }
    public double? VMin { get; private set; }
    public double? VMax { get; private set; }
    public BigInteger? VSum { get; private set; }
    public long VNull { get; private set; }
    // The distinct keys, exactly (case matters: "Temperature" != "temperature").
    // Held for one month at a time: Seal() keeps the count and lets the set go,
    // so 12 million rows need one month's keys in memory, not all of them.
    HashSet<string>? _keys = new(StringComparer.Ordinal);
    long _sealedKeys;
    public long Keys => _keys?.Count ?? _sealedKeys;
    public bool Sealed => _keys is null;

    public void Seal()
    {
        if (_keys is null) return;
        _sealedKeys = _keys.Count;
        _keys = null;
    }

    public MonthPrint(string table, string part) { Table = table; Part = part; }

    public void Add(string key, DateTime? ts, double? v)
    {
        if (_keys is null)
            throw new InvalidOperationException($"{Table} {Part}: rows arrived after the month was closed; they must come in time order");
        Rows++;
        _keys.Add(key);
        KeyHash += KeyHashOf(key);
        if (ts is { } t)
        {
            if (TsMin is null || t < TsMin) TsMin = t;
            if (TsMax is null || t > TsMax) TsMax = t;
        }
        if (v is not { } x) { VNull++; return; }
        if (VMin is null || Less(x, VMin.Value)) VMin = x;
        if (VMax is null || Less(VMax.Value, x)) VMax = x;
        // floor(value x 1000), summed exactly. Values above 10^12 are left
        // out (TEON has a few around 10^26); the min and max check those.
        if (Math.Abs(x) < 1e12)
            VSum = (VSum ?? BigInteger.Zero) + new BigInteger(Math.Floor(x * 1000.0));
    }

    /// <summary>First four bytes of the key's MD5, read as an unsigned number.</summary>
    public static long KeyHashOf(string key)
    {
        Span<byte> md5 = stackalloc byte[16];
        MD5.HashData(Encoding.UTF8.GetBytes(key), md5);
        return ((long)md5[0] << 24) | ((long)md5[1] << 16) | ((long)md5[2] << 8) | md5[3];
    }

    /// <summary>DuckDB's ordering: NaN sorts above every number.</summary>
    static bool Less(double a, double b) =>
        double.IsNaN(a) ? false : double.IsNaN(b) || a < b;

    public static string PartOf(DateTime? ts, bool monthly) =>
        !monthly ? "all" : ts!.Value.ToString("yyyy-MM", CultureInfo.InvariantCulture);
}

public static class Specs
{
    static string S(object? o) => o switch
    {
        null => "",                                        // concat() treats NULL as empty
        string s => s,
        Guid g => g.ToString("D"),                         // lower case, as in the store
        DateTime d => d.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture),
        IFormattable f => f.ToString(null, CultureInfo.InvariantCulture),
        _ => o.ToString() ?? "",
    };

    static double? D(object? o) => o switch
    {
        null => null,
        double d => d,
        long l => l,
        int i => i,
        short s => s,
        _ => throw new InvalidCastException($"not a number: {o.GetType().Name}"),
    };

    static DateTime? T(object? o) => o switch
    {
        null => null,
        DateTime d => d,
        DateTimeOffset dto => dto.UtcDateTime,             // USGS is counted by UTC month
        _ => throw new InvalidCastException($"not a time: {o.GetType().Name}"),
    };

    /// <summary>
    /// The fingerprint definitions. obs needs the variable names, because its
    /// key is "uuid|variable" and the table stores a variable id.
    /// </summary>
    public static IReadOnlyList<RowSpec> All(IReadOnlyDictionary<short, string> variables)
    {
        RowSpec Spec(string name, string layout, bool monthly, string[] key, string? ts, string? value,
                     Func<object?[], string>? keyFn = null)
        {
            var l = Layouts.All[layout];
            var k = key.Select(l.Index).ToArray();
            int? t = ts is null ? null : l.Index(ts);
            int? v = value is null ? null : l.Index(value);
            return new RowSpec(name, layout, monthly,
                keyFn ?? (r => string.Join('|', k.Select(i => S(r[i])))),
                r => t is null ? null : T(r[t.Value]),
                r => v is null ? null : D(r[v.Value]));
        }

        var obs = Layouts.All["obs"];
        int u = obs.Index("record_uuid"), vid = obs.Index("variable_id");
        return
        [
            Spec("obs", "obs", true, [], "ts", "value",
                 keyFn: r => S(r[u]) + "|" + variables[(short)r[vid]!]),
            Spec("usgs", "usgs", true, ["uuid", "variable", "statistic_id"], "ts", "value"),
            Spec("snotel", "snotel", true, ["uuid", "variable"], "ts", "value"),
            Spec("asos", "asos", true, ["uuid", "variable"], "ts", "value"),
            Spec("smoke", "smoke", true, ["uuid"], "ts", "value"),
            Spec("assets", "assets", true, ["uuid"], "ts", "total_available"),
            Spec("terc", "terc", false, ["station", "date_time_local", "lake_condition"], "date_time_local", "secchi_m"),
            Spec("cssl_climo", "cssl_climo", false, ["water_year"], null, "snowfall_cm"),
            Spec("watershed", "watershed_stage", false, ["watershed_id"], null, "area_km"),
        ];
    }

    /// <summary>
    /// Feed rows, in time order, into per-month prints. Each month is sealed
    /// when the next one starts; a row for a sealed month stops the run.
    /// </summary>
    public static void Accumulate(RowSpec spec, IEnumerable<object?[]> rows, IDictionary<(string, string), MonthPrint> into)
    {
        MonthPrint? current = null;
        foreach (var r in rows)
        {
            var ts = spec.Ts(r);
            var part = MonthPrint.PartOf(ts, spec.Monthly);
            if (current is null || current.Part != part)
            {
                current?.Seal();
                if (!into.TryGetValue((spec.Name, part), out current))
                    into[(spec.Name, part)] = current = new MonthPrint(spec.Name, part);
            }
            current.Add(spec.Key(r), ts, spec.Value(r));
        }
        current?.Seal();
    }
}
