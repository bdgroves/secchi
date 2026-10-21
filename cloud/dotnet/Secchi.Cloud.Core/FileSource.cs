namespace Secchi.Cloud.Core;

/// <summary>
/// Fingerprints the export's files before anything is loaded. If these
/// match the store, the files are a faithful copy; if a loaded table later
/// differs, the problem is in the load, not the export.
/// </summary>
public static class FileSource
{
    public static Dictionary<short, string> Variables(string bcpDir)
    {
        var layout = Layouts.All["variable"];
        return BcpFile.Read(Path.Combine(bcpDir, "variable.txt"), layout)
                      .ToDictionary(r => (short)r[0]!, r => (string)r[1]!);
    }

    public static Dictionary<(string, string), MonthPrint> Fingerprint(string bcpDir, TextWriter log)
    {
        var prints = new Dictionary<(string, string), MonthPrint>();
        foreach (var spec in Specs.All(Variables(bcpDir)))
        {
            var t0 = System.Diagnostics.Stopwatch.StartNew();
            var path = Path.Combine(bcpDir, spec.Layout + ".txt");
            Specs.Accumulate(spec, BcpFile.Read(path, Layouts.All[spec.Layout]), prints);
            var rows = prints.Values.Where(p => p.Table == spec.Name).Sum(p => p.Rows);
            log.WriteLine($"  {spec.Name,-16} {rows,12:N0} rows  {t0.Elapsed.TotalSeconds,6:F1}s");
        }
        return prints;
    }
}
