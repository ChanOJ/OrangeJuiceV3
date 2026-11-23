"""Extract every user table of a SQL Server 2000 MDF into SQLite (full fidelity).

    python3 -I scripts/mdf/extract_sqlite.py FILE.MDF OUT.sqlite

The MDF is only read. OUT is rebuilt from scratch (written to OUT.tmp, then
renamed). Besides the tables, the output holds:

  _extract_info    key/value: source file, sha256, extraction date, conventions
  _extract_tables  per table: expected live rows (IAM slot scan), sysindexes.rowcnt,
                   inserted rows, decode errors
  _decode_errors   one row per column that could not be decoded (value stored NULL,
                   the row is always inserted)
  _columns         original SQL Server column types and nullability
  _indexes         sysindexes entries (primary keys, unique, clustered, key columns)
  _procedures      procedures, views, triggers, functions with their syscomments text

Conventions: datetime -> 'YYYY-MM-DD HH:MM:SS.fff', smalldatetime ->
'YYYY-MM-DD HH:MM:SS' (ISO 8601 with a space, SQLite's own format); money and
decimal -> exact decimal text; char/varchar/text cp1252, nchar/nvarchar/ntext
UTF-16; image/binary/timestamp -> BLOB; bit -> 0/1. char(n) keeps its padding.
"""
import datetime
import decimal
import hashlib
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mdf  # noqa: E402

SKIP_TABLES = {"dtproperties"}  # Enterprise Manager diagram storage
MODULE_XTYPES = ("P", "V", "TR", "FN", "IF", "TF", "X")

SQLITE_TYPE = {
    "tinyint": "INTEGER", "smallint": "INTEGER", "int": "INTEGER", "bigint": "INTEGER",
    "bit": "INTEGER", "real": "REAL", "float": "REAL",
    "char": "TEXT", "varchar": "TEXT", "nchar": "TEXT", "nvarchar": "TEXT",
    "text": "TEXT", "ntext": "TEXT", "datetime": "TEXT", "smalldatetime": "TEXT",
    "uniqueidentifier": "TEXT", "money": "TEXT", "smallmoney": "TEXT",
    "decimal": "TEXT", "numeric": "TEXT",
    "binary": "BLOB", "varbinary": "BLOB", "image": "BLOB", "timestamp": "BLOB",
    "sql_variant": "BLOB",
}


def q(name):
    return '"%s"' % name.replace('"', '""')


def converter(col):
    """Python value from mdf.decode_row -> SQLite-bindable value."""
    t = col.type_name
    if t == "datetime":
        return lambda v: None if v is None else v.isoformat(" ", "milliseconds")
    if t == "smalldatetime":
        return lambda v: None if v is None else v.isoformat(" ", "seconds")
    if t in ("money", "smallmoney", "decimal", "numeric"):
        return lambda v: None if v is None else str(v)
    return None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def create_meta(cx):
    cx.executescript("""
        CREATE TABLE _extract_info (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE _extract_tables (table_name TEXT PRIMARY KEY, object_id INTEGER,
            created TEXT, expected_rows INTEGER, sysindexes_rowcnt INTEGER,
            inserted_rows INTEGER, decode_errors INTEGER, seconds REAL);
        CREATE TABLE _decode_errors (table_name TEXT, row_number INTEGER,
            first_column_value TEXT, column_name TEXT, error TEXT);
        CREATE TABLE _columns (table_name TEXT, colid INTEGER, name TEXT, sql_type TEXT,
            nullable INTEGER, computed INTEGER, sqlite_type TEXT,
            PRIMARY KEY (table_name, colid));
        CREATE TABLE _indexes (table_name TEXT, indid INTEGER, name TEXT, status INTEGER,
            is_primary_key INTEGER, is_unique INTEGER, is_clustered INTEGER,
            is_statistics INTEGER, key_columns TEXT, PRIMARY KEY (table_name, indid));
        CREATE TABLE _procedures (name TEXT, xtype TEXT, created TEXT, text TEXT);
    """)


def copy_table(cx, db, obj):
    oid, name = obj["id"], obj["name"]
    cols = [c for c in db.columns[oid] if not c.computed]
    defs = ", ".join("%s %s /* %s%s */" % (q(c.name), SQLITE_TYPE.get(c.type_name, "BLOB"), c.decl(),
                                           "" if c.nullable else " NOT NULL") for c in cols)
    cx.execute("CREATE TABLE %s (%s)" % (q(name), defs))
    for c in db.columns[oid]:
        cx.execute("INSERT INTO _columns VALUES (?,?,?,?,?,?,?)",
                   (name, c.colid, c.name, c.decl(), int(c.nullable), int(c.computed),
                    None if c.computed else SQLITE_TYPE.get(c.type_name, "BLOB")))

    names = [c.name for c in cols]
    convs = [(i, f) for i, f in enumerate(converter(c) for c in cols) if f]
    errors = []

    def rows():
        for n, (row, errs) in enumerate(db.rows(oid), 1):
            vals = [row[k] for k in names]
            for i, f in convs:
                vals[i] = f(vals[i])
            for col, msg in errs:
                errors.append((name, n, None if vals[0] is None else str(vals[0]), col, msg))
            yield vals

    sql = "INSERT INTO %s VALUES (%s)" % (q(name), ",".join("?" * len(cols)))
    cx.executemany(sql, rows())
    cx.executemany("INSERT INTO _decode_errors VALUES (?,?,?,?,?)", errors)
    inserted = cx.execute("SELECT count(*) FROM %s" % q(name)).fetchone()[0]
    return inserted, errors


def copy_indexes(cx, db, obj):
    """Record sysindexes entries; recreate primary keys as SQLite unique indexes."""
    notes = []
    for ix in sorted((i for i in db.indexes if i["id"] == obj["id"] and 0 < i["indid"] < 255),
                     key=lambda i: i["indid"]):
        st = ix["status"]
        keys = db.index_keys(ix)
        is_pk, is_stat = bool(st & 0x800), bool(st & 0x40) or ix["name"].startswith("_WA_Sys")
        cx.execute("INSERT INTO _indexes VALUES (?,?,?,?,?,?,?,?,?)",
                   (obj["name"], ix["indid"], ix["name"], st, int(is_pk), int(bool(st & 2)),
                    int(ix["indid"] == 1), int(is_stat), ",".join(keys)))
        if is_pk and keys:
            sql = "CREATE UNIQUE INDEX %s ON %s (%s)" % (
                q(ix["name"]), q(obj["name"]), ",".join(q(k) for k in keys))
            try:
                cx.execute(sql)
            except sqlite3.IntegrityError as e:
                notes.append("%s: primary key %s not unique (%s)" % (obj["name"], ix["name"], e))
    return notes


def main(src, out):
    t0 = time.time()
    db = mdf.Database(src)
    tmp = out + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    cx = sqlite3.connect(tmp)
    cx.execute("PRAGMA journal_mode=OFF")
    cx.execute("PRAGMA synchronous=OFF")
    cx.execute("BEGIN")
    create_meta(cx)

    report, notes = [], []
    for obj in db.objects_of("U"):
        if obj["name"].lower() in SKIP_TABLES:
            continue
        t1 = time.time()
        expected = db.count_rows(obj["id"])[0]
        rowcnt = next((i["rowcnt"] for i in db.indexes
                       if i["id"] == obj["id"] and i["indid"] in (0, 1)), None)
        inserted, errors = copy_table(cx, db, obj)
        notes += copy_indexes(cx, db, obj)
        dt = time.time() - t1
        cx.execute("INSERT INTO _extract_tables VALUES (?,?,?,?,?,?,?,?)",
                   (obj["name"], obj["id"], str(obj["crdate"]), expected, rowcnt, inserted,
                    len(errors), round(dt, 2)))
        report.append((obj["name"], expected, inserted, errors, dt))
        print("%-16s expected %7d inserted %7d errors %4d  %6.1fs"
              % (obj["name"], expected, inserted, len(errors), dt), flush=True)

    nproc = 0
    for obj in db.objects_of(*MODULE_XTYPES):
        cx.execute("INSERT INTO _procedures VALUES (?,?,?,?)",
                   (obj["name"], obj["xtype"].strip(), str(obj["crdate"]), db.module_text(obj["id"])))
        nproc += 1

    info = {
        "source_file": os.path.basename(src),
        "source_path": src,
        "source_sha256": sha256(src),
        "source_pages": str(db.f.page_count),
        "extracted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "extractor": "scripts/mdf/extract_sqlite.py",
        "codepage": db.codepage,
        "rows_from": "IAM chains (sysindexes.FirstIAM, indid 0/1); forwarded records once, stubs skipped",
        "datetime_format": "datetime 'YYYY-MM-DD HH:MM:SS.fff', smalldatetime 'YYYY-MM-DD HH:MM:SS'",
        "skipped_tables": ",".join(sorted(SKIP_TABLES)),
        "procedures": str(nproc),
        "notes": "; ".join(notes),
        "seconds": "%.1f" % (time.time() - t0),
    }
    cx.executemany("INSERT INTO _extract_info VALUES (?,?)", sorted(info.items()))
    cx.commit()
    cx.close()
    os.chmod(tmp, 0o600)  # personal data: owner-only, like the MDF
    os.replace(tmp, out)

    bad = [r for r in report if r[1] != r[2]]
    nerr = sum(len(r[3]) for r in report)
    print("%d tables, %d rows, %d modules, %d decode errors, %d count mismatches, %.1fs -> %s"
          % (len(report), sum(r[2] for r in report), nproc, nerr, len(bad), time.time() - t0, out))
    for r in report:
        for e in r[3][:3]:
            print("  decode error %s row %d (%s) %s: %s" % e)
    for n in notes:
        print("  note: " + n)
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
