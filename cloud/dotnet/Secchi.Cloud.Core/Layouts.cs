namespace Secchi.Cloud.Core;

/// <summary>The SQL Server column types the bcp files carry.</summary>
public enum ColumnType { Guid, Int16, Int32, Int64, Float, DateTime2, DateTimeOffset, Text }

public sealed record Column(string Name, ColumnType Type);

/// <summary>
/// One table as it sits in SQL Server, column for column, in the order the
/// export writes them. This repeats cloud/sql/01_schema.sql on purpose: the
/// loader compares it with the live table before it copies a row, so the
/// two can't drift apart without the load stopping.
/// </summary>
public sealed record Layout(string Table, IReadOnlyList<Column> Columns)
{
    public int Index(string name)
    {
        for (var i = 0; i < Columns.Count; i++)
            if (Columns[i].Name == name) return i;
        throw new ArgumentException($"{Table} has no column {name}");
    }
}

public static class Layouts
{
    static Column C(string n, ColumnType t) => new(n, t);
    const ColumnType G = ColumnType.Guid, I2 = ColumnType.Int16, I4 = ColumnType.Int32,
        I8 = ColumnType.Int64, F = ColumnType.Float, DT = ColumnType.DateTime2,
        DTO = ColumnType.DateTimeOffset, T = ColumnType.Text;

    static Column[] Wide(bool unit) => unit
        ? [C("uuid", T), C("site", T), C("sensor_type", T), C("ts", DT), C("lat", F), C("lng", F),
           C("variable", T), C("value", F), C("unit", T)]
        : [C("uuid", T), C("site", T), C("sensor_type", T), C("ts", DT), C("lat", F), C("lng", F),
           C("variable", T), C("value", F)];

    public static readonly IReadOnlyDictionary<string, Layout> All = new Dictionary<string, Layout>
    {
        ["site"] = new("site", [C("site_id", I2), C("site", T), C("lat", F), C("lng", F)]),
        ["sensor_type"] = new("sensor_type", [C("sensor_type_id", I2), C("sensor_type", T)]),
        ["variable"] = new("variable", [C("variable_id", I2), C("variable", T)]),
        ["obs"] = new("obs", [C("record_uuid", G), C("site_id", I2), C("sensor_type_id", I2),
                              C("ts", DT), C("variable_id", I2), C("value", F)]),
        ["usgs"] = new("usgs", [C("uuid", T), C("site", T), C("site_number", T), C("sensor_type", T),
                                C("ts", DTO), C("lat", F), C("lng", F), C("variable", T),
                                C("statistic_id", T), C("statistic", T), C("value", F),
                                C("unit_of_measure", T), C("approval_status", T), C("qualifier", I4)]),
        ["snotel"] = new("snotel", Wide(unit: true)),
        ["asos"] = new("asos", Wide(unit: true)),
        ["smoke"] = new("smoke", Wide(unit: false)),
        ["assets"] = new("assets", [C("uuid", T), C("site", T), C("sensor_type", T), C("ts", DT),
                                    C("lat", F), C("lng", F), C("kind", T), C("ref", T),
                                    C("total_available", I8)]),
        ["terc"] = new("terc", [C("station", T), C("date_time_local", DT), C("secchi_m", F),
                                C("disappear_m", F), C("reappear_m", F), C("viewing_condition", I4),
                                C("lake_condition", T)]),
        ["cssl_climo"] = new("cssl_climo", [C("water_year", I4), C("snowfall_cm", F),
                                            C("max_depth_cm", I4), C("snowfall_hs_cm", F)]),
        ["watershed_stage"] = new("watershed_stage", [C("watershed_id", I4), C("name", T),
                                                      C("area_km", F), C("wkt", T)]),
    };
}
