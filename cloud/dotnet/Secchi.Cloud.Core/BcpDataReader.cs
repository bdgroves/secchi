using System.Collections;
using System.Data.Common;

namespace Secchi.Cloud.Core;

/// <summary>
/// An export file, presented as a data reader. SqlBulkCopy pulls rows from
/// it one at a time, as fast as the server takes them: no DataTable in
/// between, and only the current row in memory.
///
/// SqlBulkCopy only calls a handful of these members (Read, FieldCount,
/// GetName/GetOrdinal for the column mappings, IsDBNull and GetValue). The
/// rest are here because DbDataReader declares them abstract; they read the
/// current row like any reader would.
/// </summary>
public sealed class BcpDataReader : DbDataReader
{
    readonly Layout _layout;
    readonly IEnumerator<object?[]> _rows;
    object?[]? _row;
    bool _closed;

    public BcpDataReader(string path, Layout layout)
    {
        _layout = layout;
        _rows = BcpFile.Read(path, layout).GetEnumerator();
    }

    /// <summary>How many rows have been handed over so far.</summary>
    public long RowsRead { get; private set; }

    public override bool Read()
    {
        if (_closed || !_rows.MoveNext()) { _row = null; return false; }
        _row = _rows.Current;
        RowsRead++;
        return true;
    }

    object? Cell(int i) => (_row ?? throw new InvalidOperationException("no current row"))[i];

    public override int FieldCount => _layout.Columns.Count;
    public override string GetName(int ordinal) => _layout.Columns[ordinal].Name;
    public override int GetOrdinal(string name) => _layout.Index(name);
    public override bool IsDBNull(int ordinal) => Cell(ordinal) is null;
    public override object GetValue(int ordinal) => Cell(ordinal) ?? DBNull.Value;

    public override int GetValues(object[] values)
    {
        var n = Math.Min(values.Length, FieldCount);
        for (var i = 0; i < n; i++) values[i] = GetValue(i);
        return n;
    }

    public override Type GetFieldType(int ordinal) => _layout.Columns[ordinal].Type switch
    {
        ColumnType.Guid => typeof(Guid),
        ColumnType.Int16 => typeof(short),
        ColumnType.Int32 => typeof(int),
        ColumnType.Int64 => typeof(long),
        ColumnType.Float => typeof(double),
        ColumnType.DateTime2 => typeof(DateTime),
        ColumnType.DateTimeOffset => typeof(DateTimeOffset),
        _ => typeof(string),
    };

    public override string GetDataTypeName(int ordinal) => GetFieldType(ordinal).Name;

    public override object this[int ordinal] => GetValue(ordinal);
    public override object this[string name] => GetValue(GetOrdinal(name));

    public override bool GetBoolean(int ordinal) => (bool)GetValue(ordinal);
    public override byte GetByte(int ordinal) => (byte)GetValue(ordinal);
    public override char GetChar(int ordinal) => (char)GetValue(ordinal);
    public override DateTime GetDateTime(int ordinal) => (DateTime)GetValue(ordinal);
    public override decimal GetDecimal(int ordinal) => (decimal)GetValue(ordinal);
    public override double GetDouble(int ordinal) => (double)GetValue(ordinal);
    public override float GetFloat(int ordinal) => (float)GetValue(ordinal);
    public override Guid GetGuid(int ordinal) => (Guid)GetValue(ordinal);
    public override short GetInt16(int ordinal) => (short)GetValue(ordinal);
    public override int GetInt32(int ordinal) => (int)GetValue(ordinal);
    public override long GetInt64(int ordinal) => (long)GetValue(ordinal);
    public override string GetString(int ordinal) => (string)GetValue(ordinal);

    public override long GetBytes(int ordinal, long dataOffset, byte[]? buffer, int bufferOffset, int length) =>
        throw new NotSupportedException("the export files hold no binary columns");
    public override long GetChars(int ordinal, long dataOffset, char[]? buffer, int bufferOffset, int length) =>
        throw new NotSupportedException("read text with GetString");

    public override int Depth => 0;
    public override bool HasRows => true;
    public override bool IsClosed => _closed;
    public override int RecordsAffected => -1;
    public override bool NextResult() => false;
    public override IEnumerator GetEnumerator() => new DbEnumerator(this);

    public override void Close()
    {
        if (_closed) return;
        _closed = true;
        _rows.Dispose();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing) Close();
        base.Dispose(disposing);
    }
}
