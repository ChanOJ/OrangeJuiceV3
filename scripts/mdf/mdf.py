"""Minimal read-only reader for SQL Server 2000 data files (.MDF, dbi_version 539).

Layers:
  MdfFile        page access (mmap, read-only), page headers, slot arrays
  parse_record   FixedVar record -> fixed bytes, null bitmap, variable columns
  Column/decode  typed value decoding from syscolumns metadata
  Database       system catalog bootstrap (sysobjects, syscolumns, sysindexes,
                 systypes, syscomments), table scans via IAM chains and LOB
                 (text/ntext/image) reading from the text tree

Never writes to the file. Intended as the base of an MDF -> SQLite extractor.
"""
import codecs
import datetime
import decimal
import mmap
import struct
import uuid
from collections import namedtuple

PAGE_SIZE = 8192
HEADER_SIZE = 96

# page types (m_type)
PT_DATA, PT_INDEX, PT_TEXT_MIX, PT_TEXT_TREE = 1, 2, 3, 4
PT_GAM, PT_SGAM, PT_IAM, PT_PFS, PT_BOOT, PT_FILEHDR = 8, 9, 10, 11, 13, 15

# record types (bits 1-3 of status byte A)
REC_PRIMARY, REC_FORWARDED, REC_FORWARDING_STUB, REC_INDEX = 0, 1, 2, 3
REC_BLOB, REC_GHOST_INDEX, REC_GHOST_DATA, REC_GHOST_VERSION = 4, 5, 6, 7

PageHeader = namedtuple(
    "PageHeader",
    "page_no type level flags index_id prev_page min_len next_page slot_cnt "
    "obj_id free_cnt free_data this_page ghost_cnt",
)


def page_ptr(buf, off):
    """6-byte page pointer -> (file_id, page_id)."""
    page_id, file_id = struct.unpack_from("<IH", buf, off)
    return file_id, page_id


class MdfFile:
    def __init__(self, path):
        self.path = path
        self._f = open(path, "rb")
        self._mm = mmap.mmap(self._f.fileno(), 0, access=mmap.ACCESS_READ)
        self.page_count = len(self._mm) // PAGE_SIZE

    def close(self):
        self._mm.close()
        self._f.close()

    def page(self, n):
        return self._mm[n * PAGE_SIZE:(n + 1) * PAGE_SIZE]

    @staticmethod
    def header(pg, n=None):
        return PageHeader(
            page_no=n, type=pg[1], level=pg[3],
            flags=struct.unpack_from("<H", pg, 4)[0],
            index_id=struct.unpack_from("<H", pg, 6)[0],
            prev_page=page_ptr(pg, 8),
            min_len=struct.unpack_from("<H", pg, 14)[0],
            next_page=page_ptr(pg, 16),
            slot_cnt=struct.unpack_from("<H", pg, 22)[0],
            obj_id=struct.unpack_from("<i", pg, 24)[0],
            free_cnt=struct.unpack_from("<H", pg, 28)[0],
            free_data=struct.unpack_from("<H", pg, 30)[0],
            this_page=page_ptr(pg, 32),
            ghost_cnt=struct.unpack_from("<H", pg, 58)[0],
        )

    @staticmethod
    def slots(pg):
        """Record offsets from the slot array (0 = empty slot)."""
        n = struct.unpack_from("<H", pg, 22)[0]
        return [struct.unpack_from("<H", pg, PAGE_SIZE - 2 - 2 * i)[0] for i in range(n)]

    def scan_headers(self):
        for n in range(self.page_count):
            pg = self._mm[n * PAGE_SIZE:n * PAGE_SIZE + HEADER_SIZE]
            yield self.header(pg, n)

    # ---- allocation -----------------------------------------------------
    def iam_pages(self, first_iam):
        """Follow an IAM chain; return the sorted list of allocated page numbers."""
        pages = set()
        file_id, n = first_iam
        seen = set()
        while n and n not in seen:
            seen.add(n)
            pg = self.page(n)
            if pg[1] != PT_IAM:
                raise ValueError("page %d is not an IAM page" % n)
            offs = self.slots(pg)
            hdr = offs[0]  # slot 0: IAM header record
            # single-page allocations: 8 page pointers at record offset 46
            for i in range(8):
                f_, p_ = page_ptr(pg, hdr + 46 + 6 * i)
                if p_:
                    pages.add(p_)
            # start page of this IAM's interval: record offset 40
            start = page_ptr(pg, hdr + 40)[1]
            # extent bitmap: slot 1, after its 4-byte record header
            bm = offs[1] + 4
            bm_len = struct.unpack_from("<H", pg, offs[1] + 2)[0] - 4
            for i in range(bm_len):
                b = pg[bm + i]
                if b:
                    for bit in range(8):
                        if b & (1 << bit):
                            ext = start + (i * 8 + bit) * 8
                            pages.update(range(ext, ext + 8))
            n = page_ptr(pg, 16)[1]
        return sorted(p for p in pages if p < self.page_count)


Record = namedtuple("Record", "rtype fixed ncols nulls var")

# LOB record types (u16 at record offset 12 of a REC_BLOB record, SQL Server 2000)
BLOB_SMALL_ROOT = 0   # data inline: length u16 @14, data @20
BLOB_INTERNAL = 2     # on TEXT_TREE pages: count u16 @16, level u16 @18, 16-byte links @20
BLOB_DATA = 3         # fragment: data @14 up to the record length
BLOB_LARGE_ROOT = 4   # 84 bytes: count u16 @16, level u16 @18, up to 5 12-byte links @24
BLOB_EMPTY_ROOT = 8   # 84-byte root of a zero-length value (stale bytes may follow)


class LobError(ValueError):
    pass


def parse_record(pg, off):
    """Parse a FixedVar record at page offset off.

    fixed: bytes of the record from offset 0 to the fixed-length end (so that
           syscolumns.xoffset values index it directly).
    nulls: int bitmask (bit i = column ordinal i is NULL).
    var:   list of bytes, one per present variable column.
    """
    a = pg[off]
    rtype = (a >> 1) & 7
    fixed_end = struct.unpack_from("<H", pg, off + 2)[0]
    fixed = pg[off:off + fixed_end]
    if rtype == REC_FORWARDING_STUB:
        return Record(rtype, fixed, 0, 0, [])
    p = off + fixed_end
    ncols = 0
    nulls = 0
    if a & 0x10:  # has null bitmap
        ncols = struct.unpack_from("<H", pg, p)[0]
        p += 2
        nb = (ncols + 7) // 8
        nulls = int.from_bytes(pg[p:p + nb], "little")
        p += nb
    var = []
    if a & 0x20:  # has variable columns
        nvar = struct.unpack_from("<H", pg, p)[0]
        p += 2
        ends = struct.unpack_from("<%dH" % nvar, pg, p)
        start = p + 2 * nvar - off
        for e in ends:
            e &= 0x7FFF
            var.append(pg[off + start:off + e])
            start = e
    return Record(rtype, fixed, ncols, nulls, var)


# ---- types ---------------------------------------------------------------
TYPE_NAMES = {
    34: "image", 35: "text", 36: "uniqueidentifier", 48: "tinyint", 52: "smallint",
    56: "int", 58: "smalldatetime", 59: "real", 60: "money", 61: "datetime",
    62: "float", 98: "sql_variant", 99: "ntext", 104: "bit", 106: "decimal",
    108: "numeric", 122: "smallmoney", 127: "bigint", 165: "varbinary",
    167: "varchar", 173: "binary", 175: "char", 189: "timestamp",
    231: "nvarchar", 239: "nchar",
}
LOB_TYPES = {34, 35, 99}
EPOCH = datetime.datetime(1900, 1, 1)


def _sql_ctl(err):
    """Decode error handler: bytes undefined in the code page (cp1252 0x81, 0x8D,
    0x8F, 0x90, 0x9D) map to U+0080..U+009F, as Windows/SQL Server convert them."""
    return "".join(chr(b) for b in err.object[err.start:err.end]), err.end


codecs.register_error("sqlctl", _sql_ctl)


class TextPtr(bytes):
    """16-byte in-row pointer of a text/ntext/image value: timestamp (8, equal to
    the root record's blob id), page id (4), file id (2), slot (2)."""

    @property
    def location(self):
        ts, page_id, file_id, slot = struct.unpack("<QIHH", self)
        return ts, file_id, page_id, slot


class Column(namedtuple("Column", "name colid xtype length xoffset bitpos prec scale colstat typestat")):
    @property
    def computed(self):
        return bool(self.colstat & 4)

    @property
    def type_name(self):
        return TYPE_NAMES.get(self.xtype, "xtype%d" % self.xtype)

    @property
    def is_var(self):
        return self.xoffset < 0

    @property
    def nullable(self):
        return not (self.typestat & 1)  # typestat bit 0 = NOT NULL (observed)

    def decl(self):
        t = self.type_name
        if self.xtype in (167, 175, 165, 173):
            t += "(%d)" % self.length
        elif self.xtype in (231, 239):
            t += "(%d)" % (self.length // 2)
        elif self.xtype in (106, 108):
            t += "(%d,%d)" % (self.prec, self.scale)
        return t


def decode_value(col, raw, codepage="cp1252"):
    t = col.xtype
    if t in LOB_TYPES:
        return TextPtr(raw)  # resolved by Database.read_lob
    if t in (167, 175):
        return raw.decode(codepage, "sqlctl")
    if t in (231, 239):
        return raw.decode("utf-16-le")
    if t in (165, 173, 189):
        return bytes(raw)
    if t == 48:
        return raw[0]
    if t == 52:
        return struct.unpack("<h", raw)[0]
    if t == 56:
        return struct.unpack("<i", raw)[0]
    if t == 127:
        return struct.unpack("<q", raw)[0]
    if t == 59:
        return struct.unpack("<f", raw)[0]
    if t == 62:
        return struct.unpack("<d", raw)[0]
    if t == 61:
        ticks, days = struct.unpack("<Ii", raw)
        return EPOCH + datetime.timedelta(days=days, milliseconds=round(ticks * 10 / 3))
    if t == 58:
        mins, days = struct.unpack("<HH", raw)
        return EPOCH + datetime.timedelta(days=days, minutes=mins)
    if t == 60:
        hi, lo = struct.unpack("<iI", raw)
        return decimal.Decimal((hi << 32) | lo) / 10000
    if t == 122:
        return decimal.Decimal(struct.unpack("<i", raw)[0]) / 10000
    if t in (106, 108):
        sign = 1 if raw[0] else -1
        v = int.from_bytes(raw[1:], "little")
        return decimal.Decimal(sign * v).scaleb(-col.scale)
    if t == 36:
        return str(uuid.UUID(bytes_le=bytes(raw)))
    return bytes(raw)


def decode_lob(col, data, codepage="cp1252"):
    """LOB bytes (from Database.read_lob) -> text (cp1252), ntext (UTF-16LE) or image."""
    if data is None or col.xtype == 34:
        return data
    if col.xtype == 99:
        return data.decode("utf-16-le")
    return data.decode(codepage, "sqlctl")


_EMPTY = {167: "", 175: "", 231: "", 239: "", 165: b"", 173: b""}


def decode_row(cols, rec, codepage="cp1252", lob=None, errors=None):
    """cols: list of Column sorted by colid. Computed columns are not stored and
    are skipped; the null bitmap ordinal is the position among stored columns.

    lob:    optional callable(TextPtr) -> bytes; LOB columns are then decoded to
            str/bytes instead of returning the TextPtr.
    errors: optional list; when given, a column that fails to decode is set to
            None and (column name, message) is appended instead of raising."""
    out = {}
    for i, c in enumerate(c for c in cols if not c.computed):
        if i >= rec.ncols and rec.ncols:
            out[c.name] = None  # column added after the row was written
            continue
        if rec.nulls >> i & 1:
            out[c.name] = None
            continue
        try:
            if c.is_var:
                k = -c.xoffset - 1
                if k < len(rec.var):
                    v = decode_value(c, rec.var[k], codepage)
                else:  # trailing zero-length var columns are not stored
                    v = _EMPTY.get(c.xtype)
            elif c.xtype == 104:
                v = (rec.fixed[c.xoffset] >> c.bitpos) & 1
            else:
                raw = rec.fixed[c.xoffset:c.xoffset + c.length]
                if len(raw) != c.length:
                    raise ValueError("fixed column truncated (%d/%d bytes)" % (len(raw), c.length))
                v = decode_value(c, raw, codepage)
            if lob is not None and isinstance(v, TextPtr):
                v = decode_lob(c, lob(v), codepage)
        except Exception as e:  # noqa: BLE001 - reported, never swallowed silently
            if errors is None:
                raise
            errors.append((c.name, "%s: %s" % (type(e).__name__, e)))
            v = None
        out[c.name] = v
    return out


# ---- catalog bootstrap ---------------------------------------------------
# syscolumns stored layout (verified on dbi_version 539)
_SC_FIXED = struct.Struct("<iBBhhBBhhBBhiihhh")  # from record offset 4


def _boot_syscolumns(rec):
    (id_, xtype, typestat, xusertype, length, xprec, xscale, colid, xoffset,
     bitpos, reserved, colstat, cdefault, domain, number, colorder, offset) = _SC_FIXED.unpack_from(rec.fixed, 4)
    name = rec.var[0].decode("utf-16-le") if rec.var else ""
    return dict(name=name, id=id_, xtype=xtype, typestat=typestat, xusertype=xusertype, length=length,
                prec=xprec, scale=xscale, colid=colid, xoffset=xoffset, bitpos=bitpos,
                colstat=colstat, cdefault=cdefault, number=number)


class Database:
    def __init__(self, path, codepage="cp1252"):
        self.f = MdfFile(path)
        self.codepage = codepage
        self.pages_by_obj = {}   # (type, obj_id) -> [page numbers], from header scan
        for h in self.f.scan_headers():
            self.pages_by_obj.setdefault((h.type, h.obj_id), []).append(h.page_no)
        self.columns = {}        # obj_id -> [Column]
        for rec in self._raw_records(self.pages_by_obj.get((PT_DATA, 3), [])):
            c = _boot_syscolumns(rec)
            self.columns.setdefault(c["id"], []).append(Column(
                c["name"], c["colid"], c["xtype"], c["length"], c["xoffset"],
                c["bitpos"], c["prec"], c["scale"], c["colstat"], c["typestat"]))
        for v in self.columns.values():
            v.sort(key=lambda c: c.colid)
        # now that syscolumns is known, decode the other catalogs generically
        self.objects = {r["id"]: r for r in self.scan_object(1, use_iam=False, lobs=False)}
        self.indexes = list(self.scan_object(2, use_iam=False, lobs=False))
        self._modules = None

    def _raw_records(self, pages, include_ghosts=False):
        for n in pages:
            pg = self.f.page(n)
            if pg[1] != PT_DATA:
                continue
            for off in self.f.slots(pg):
                if not off:
                    continue
                rec = parse_record(pg, off)
                if rec.rtype in (REC_PRIMARY, REC_FORWARDED) or (
                        include_ghosts and rec.rtype == REC_GHOST_DATA):
                    yield rec

    def data_pages(self, obj_id, use_iam=True):
        """Data pages of a table: via sysindexes.FirstIAM (indid 0/1) when possible,
        else every page whose header says type=1 and obj_id (may include stale pages)."""
        if use_iam:
            for ix in getattr(self, "indexes", []):
                if ix["id"] == obj_id and ix["indid"] in (0, 1) and ix.get("FirstIAM"):
                    fid, pid = page_ptr(ix["FirstIAM"], 0)
                    if pid:
                        return [p for p in self.f.iam_pages((fid, pid))
                                if self.f.page(p)[1] == PT_DATA
                                and struct.unpack_from("<i", self.f.page(p), 24)[0] == obj_id]
        return self.pages_by_obj.get((PT_DATA, obj_id), [])

    def scan_object(self, obj_id, use_iam=True, include_ghosts=False, lobs=True):
        """Decoded rows (dicts). lobs=False leaves text/image columns as TextPtr."""
        cols = self.columns[obj_id]
        lob = self.read_lob if lobs else None
        for rec in self._raw_records(self.data_pages(obj_id, use_iam), include_ghosts):
            yield decode_row(cols, rec, self.codepage, lob)

    def rows(self, obj_id):
        """Live rows via the IAM chain as (row dict, [(column, error)]): a column
        that cannot be decoded is None and reported, the row is never dropped."""
        cols = self.columns[obj_id]
        for rec in self._raw_records(self.data_pages(obj_id)):
            errors = []
            yield decode_row(cols, rec, self.codepage, self.read_lob, errors), errors

    # ---- LOBs (text / ntext / image) -------------------------------------
    def _blob_record(self, page_id, slot, blob_id=None):
        if not 0 < page_id < self.f.page_count:
            raise LobError("page %d out of range" % page_id)
        pg = self.f.page(page_id)
        if pg[1] not in (PT_TEXT_MIX, PT_TEXT_TREE):
            raise LobError("page %d has type %d, not a text page" % (page_id, pg[1]))
        slots = self.f.slots(pg)
        if slot >= len(slots) or not slots[slot]:
            raise LobError("page %d slot %d is empty" % (page_id, slot))
        off = slots[slot]
        if (pg[off] >> 1) & 7 != REC_BLOB:
            raise LobError("page %d slot %d is not a blob record" % (page_id, slot))
        rec = pg[off:off + struct.unpack_from("<H", pg, off + 2)[0]]
        if blob_id is not None and struct.unpack_from("<Q", rec, 4)[0] != blob_id:
            raise LobError("page %d slot %d belongs to another blob (stale pointer)" % (page_id, slot))
        return rec

    def read_lob(self, ptr):
        """Follow a 16-byte text pointer and return the value bytes."""
        blob_id, _file_id, page_id, slot = TextPtr(ptr).location
        return self._blob_node(self._blob_record(page_id, slot, blob_id), blob_id, 0)

    def _blob_node(self, rec, blob_id, depth):
        btype = struct.unpack_from("<H", rec, 12)[0]
        if btype == BLOB_SMALL_ROOT:
            n = struct.unpack_from("<H", rec, 14)[0]
            if 20 + n > len(rec):
                raise LobError("small root length %d exceeds record" % n)
            return bytes(rec[20:20 + n])
        if btype == BLOB_DATA:
            return bytes(rec[14:])
        if btype == BLOB_EMPTY_ROOT:
            return b""
        if btype not in (BLOB_LARGE_ROOT, BLOB_INTERNAL) or depth > 8:
            raise LobError("unexpected blob node type %d at depth %d" % (btype, depth))
        count = struct.unpack_from("<H", rec, 16)[0]
        base, size = (24, 12) if btype == BLOB_LARGE_ROOT else (20, 16)
        if base + count * size > len(rec):
            raise LobError("blob node with %d links exceeds record" % count)
        parts, prev = [], 0
        for i in range(count):
            p = base + i * size
            end = struct.unpack_from("<I", rec, p)[0]
            page_id, _file_id, slot = struct.unpack_from("<IHH", rec, p + size - 8)
            piece = self._blob_node(self._blob_record(page_id, slot, blob_id), blob_id, depth + 1)
            # link offsets are cumulative over the whole value
            want = end - prev
            if want < 0 or len(piece) < want:
                raise LobError("fragment %d:%d holds %d bytes, link expects %d"
                               % (page_id, slot, len(piece), want))
            parts.append(piece[:want])
            prev = end
        return b"".join(parts)

    def count_rows(self, obj_id, use_iam=True):
        """Return (live, ghost, stubs, slot_total) for the table's data pages."""
        live = ghost = stubs = total = 0
        for n in self.data_pages(obj_id, use_iam):
            pg = self.f.page(n)
            for off in self.f.slots(pg):
                if not off:
                    continue
                total += 1
                rt = (pg[off] >> 1) & 7
                if rt in (REC_PRIMARY, REC_FORWARDED):
                    live += 1
                elif rt == REC_GHOST_DATA:
                    ghost += 1
                elif rt == REC_FORWARDING_STUB:
                    stubs += 1
        return live, ghost, stubs, total

    def sys_id(self, name):
        for o in self.objects.values():
            if o["name"] == name:
                return o["id"]
        raise KeyError(name)

    def objects_of(self, *xtypes):
        return sorted((o for o in self.objects.values() if o["xtype"].strip() in xtypes),
                      key=lambda o: o["name"].lower())

    def index_keys(self, ix):
        """Key column names of a sysindexes row, from its `keys` blob (32-byte
        entries, colid u16 at +16; the trailing row locator entry has colid 0)."""
        names = {c.colid: c.name for c in self.columns.get(ix["id"], [])}
        k = ix.get("keys") or b""
        out = []
        for i in range(0, len(k) - 31, 32):
            colid = struct.unpack_from("<H", k, i + 16)[0]
            if colid in names:
                out.append(names[colid])
        return out

    def module_text(self, obj_id):
        """Concatenated syscomments text for a proc/view/trigger (None if encrypted/absent)."""
        if self._modules is None:
            self._modules = {}
            for r in self.scan_object(self.sys_id("syscomments"), use_iam=False, lobs=False):
                self._modules.setdefault(r["id"], []).append(r)
        parts = sorted(self._modules.get(obj_id, []),
                       key=lambda r: (r.get("number") or 0, r["colid"]))
        if not parts:
            return None
        chunks = []
        for r in parts:
            t = bytes(r.get("ctext") or b"")
            if r["status"] & 1:
                return None  # WITH ENCRYPTION
            # status & 2 ("compressed"): stored as 8-bit code page text, else UTF-16LE
            enc = self.codepage if r["status"] & 2 else "utf-16-le"
            chunks.append(t.decode(enc, "replace"))
        return "".join(chunks)
