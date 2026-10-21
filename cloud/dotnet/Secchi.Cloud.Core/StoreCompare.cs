using System.Globalization;
using System.Numerics;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Secchi.Cloud.Core;

/// <summary>One table-month: did C#'s fingerprint equal the store's?</summary>
public sealed record MonthResult(string Table, string Part, long Rows, IReadOnlyList<string> Problems)
{
    public bool Ok => Problems.Count == 0;
}

/// <summary>
/// Compares C#'s fingerprints with the store's, written by
/// `pixi run cloud-inventory` (DuckDB) to cloud/out/fingerprints.json.
/// Every field must be equal: no allowances.
/// </summary>
public static partial class StoreCompare
{
    sealed record StorePrint(string Table, string Part, long Rows, long Keys, long KeyHash,
                             DateTime? TsMin, DateTime? TsMax, double? VMin, double? VMax,
                             BigInteger VSum, long VNull);

    // Python's json writes NaN and Infinity bare, which isn't JSON. Quote them.
    [GeneratedRegex(@"(?<=[:\[,]\s*)(-?Infinity|NaN)(?=\s*[,\]}])")]
    private static partial Regex BareFloat();

    public static IReadOnlyList<MonthResult> Compare(string fingerprintsJson,
                                                     IReadOnlyDictionary<(string, string), MonthPrint> mine)
    {
        var text = BareFloat().Replace(File.ReadAllText(fingerprintsJson), "\"$1\"");
        using var doc = JsonDocument.Parse(text);
        var store = new Dictionary<(string, string), StorePrint>();
        foreach (var r in doc.RootElement.GetProperty("rows").EnumerateArray())
        {
            var p = new StorePrint(
                r.GetProperty("tbl").GetString()!, r.GetProperty("part").GetString()!,
                r.GetProperty("n_rows").GetInt64(), r.GetProperty("n_keys").GetInt64(),
                r.GetProperty("key_hash").GetInt64(),
                Time(r.GetProperty("ts_min")), Time(r.GetProperty("ts_max")),
                Num(r.GetProperty("v_min")), Num(r.GetProperty("v_max")),
                r.GetProperty("v_sum").ValueKind == JsonValueKind.Null
                    ? BigInteger.Zero : BigInteger.Parse(r.GetProperty("v_sum").GetString()!, CultureInfo.InvariantCulture),
                r.GetProperty("v_null").GetInt64());
            store[(p.Table, p.Part)] = p;
        }

        var results = new List<MonthResult>();
        foreach (var key in store.Keys.Union(mine.Keys).OrderBy(k => k.Item1, StringComparer.Ordinal)
                                      .ThenBy(k => k.Item2, StringComparer.Ordinal))
        {
            store.TryGetValue(key, out var a);
            mine.TryGetValue(key, out var b);
            var problems = new List<string>();
            if (a is null || b is null)
                problems.Add("missing in " + (a is null ? "store" : "C#"));
            else
            {
                void Check<T>(string what, T x, T y)
                {
                    if (!EqualityComparer<T>.Default.Equals(x, y)) problems.Add($"{what} {x} vs {y}");
                }
                Check("n_rows", a.Rows, b.Rows);
                Check("n_keys", a.Keys, b.Keys);
                Check("key_hash", a.KeyHash, b.KeyHash);
                Check("v_null", a.VNull, b.VNull);
                Check("ts_min", a.TsMin, b.TsMin);
                Check("ts_max", a.TsMax, b.TsMax);
                Check("v_min", a.VMin, b.VMin);
                Check("v_max", a.VMax, b.VMax);
                Check("v_sum", a.VSum, b.VSum ?? BigInteger.Zero);
            }
            results.Add(new MonthResult(key.Item1, key.Item2, (a?.Rows ?? b?.Rows) ?? 0, problems));
        }
        return results;
    }

    static DateTime? Time(JsonElement e) => e.ValueKind == JsonValueKind.Null ? null
        : DateTime.ParseExact(e.GetString()!, "yyyy-MM-dd HH:mm:ss.ffffff", CultureInfo.InvariantCulture);

    static double? Num(JsonElement e) => e.ValueKind switch
    {
        JsonValueKind.Null => null,
        JsonValueKind.String => BcpFile.ParseDouble(e.GetString()!),
        _ => e.GetDouble(),
    };

    /// <summary>The headline and any differences, for the console and the CI notice.</summary>
    public static int Report(string what, IReadOnlyList<MonthResult> results, TextWriter o)
    {
        var bad = results.Where(r => !r.Ok).ToList();
        o.WriteLine($"{what}: {results.Count - bad.Count} of {results.Count} table-months match, " +
                    $"{results.Sum(r => r.Rows):N0} rows");
        foreach (var r in bad.Take(20))
            o.WriteLine($"  DIFFERENT {r.Table} {r.Part}: {string.Join("; ", r.Problems)}");
        return bad.Count == 0 ? 0 : 1;
    }
}
