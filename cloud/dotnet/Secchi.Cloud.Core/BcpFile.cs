using System.Globalization;
using System.Text;

namespace Secchi.Cloud.Core;

/// <summary>
/// Reads the export's bcp character files: one row per line (a bare line
/// feed), fields separated by '|', an empty field is NULL. The export
/// refuses to write text containing '|' or a line break, so splitting is
/// safe. Each field is parsed to the column's type, so a value that doesn't
/// fit stops the read with the file, line and column named.
/// </summary>
public static class BcpFile
{
    public const char Sep = '|';

    static readonly string[] DateFormats =
        ["yyyy-MM-dd HH:mm:ss", "yyyy-MM-dd HH:mm:ss.FFFFFFF"];

    public static IEnumerable<object?[]> Read(string path, Layout layout)
    {
        using var reader = new StreamReader(path, new UTF8Encoding(false, throwOnInvalidBytes: true));
        var n = layout.Columns.Count;
        long line = 0;
        string? text;
        while ((text = reader.ReadLine()) != null)
        {
            line++;
            var fields = text.Split(Sep);
            if (fields.Length != n)
                throw new FormatException($"{path}:{line}: {fields.Length} fields, {layout.Table} has {n} columns");
            var row = new object?[n];
            for (var i = 0; i < n; i++)
            {
                try { row[i] = Parse(fields[i], layout.Columns[i].Type); }
                catch (FormatException e)
                {
                    throw new FormatException(
                        $"{path}:{line}: column {layout.Columns[i].Name} can't read '{fields[i]}' as {layout.Columns[i].Type}", e);
                }
            }
            yield return row;
        }
    }

    public static object? Parse(string s, ColumnType type)
    {
        if (s.Length == 0) return null;
        var inv = CultureInfo.InvariantCulture;
        return type switch
        {
            ColumnType.Text => s,
            ColumnType.Guid => Guid.ParseExact(s, "D"),
            ColumnType.Int16 => short.Parse(s, NumberStyles.AllowLeadingSign, inv),
            ColumnType.Int32 => int.Parse(s, NumberStyles.AllowLeadingSign, inv),
            ColumnType.Int64 => long.Parse(s, NumberStyles.AllowLeadingSign, inv),
            ColumnType.Float => ParseDouble(s),
            ColumnType.DateTime2 => DateTime.ParseExact(s, DateFormats, inv, DateTimeStyles.None),
            ColumnType.DateTimeOffset => DateTimeOffset.ParseExact(s, "yyyy-MM-dd HH:mm:ss zzz", inv),
            _ => throw new ArgumentOutOfRangeException(nameof(type)),
        };
    }

    /// <summary>
    /// The export writes each double in its shortest round-trip form, and
    /// .NET's parser is correctly rounded, so the bits that come back are
    /// the bits DuckDB had. The fingerprint's exact min and max prove it.
    /// </summary>
    public static double ParseDouble(string s) => s switch
    {
        "inf" or "Infinity" => double.PositiveInfinity,
        "-inf" or "-Infinity" => double.NegativeInfinity,
        "nan" or "NaN" or "-nan" => double.NaN,
        _ => double.Parse(s, NumberStyles.Float, CultureInfo.InvariantCulture),
    };
}
