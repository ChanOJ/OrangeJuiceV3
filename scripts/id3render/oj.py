"""Data macros of Orange Juice v3 (`E:OJ:*`, `B:OJ:*`, `E:ID3:*`, `B:ID3:*`) over SQLite.

Each query is the SQL of `id3x/macros/oj/oj_e.cs` / `oj_b.cs` (row modifiers of `oj_bm.cs`)
translated from T-SQL to SQLite, with the server clock frozen at `NOW` (state of the database).
Only read paths are implemented: admin, session, banner, FTP search and POST macros return
nothing, like the site did for a visitor who is not logged in.

Privacy: every row goes through `redact()`, which removes passwords, logins, IPs, host names,
user agents, departments and birth years, and keeps only the local part of e-mail addresses
(`local@????`). The templates therefore never see the private values.
"""
import re
import sqlite3
from datetime import datetime, timedelta

from engine import txt2gif, vstr

MASK = "????"
CUTOFF = "2000-11-20"          # Orange Juice v3 launch: older news belong to the 2000 archive

# Columns never handed to a template, whatever the query
DROP = {"PASSWORD", "DEPARTMENT", "DEPARTMENT_NUM", "IP", "USER_AGENT", "MAILING", "NECTARINE_TRANSACID",
        "NECTARINE_STATUS", "NECTARINE_SKIN", "IDLOGIN", "IDSESSION", "DTLASTLOGIN", "NBLOGINS", "NBLOGOUTS",
        "TOTAL_LOGIN_TIME", "OJUID", "SEX_SIZE", "LOGIN", "COOKIE"}


def redact(row):
    out = {}
    for k, v in row.items():
        K = k.upper()
        if K in DROP:
            continue
        if K == "BIRTHYEAR":
            v = MASK if v not in (None, "", 0) else None
        elif K == "HOSTNAME":
            v = MASK if v else v
        elif K == "EMAIL" and v:
            local = str(v).split("@")[0].strip()
            v = f"{local}@{MASK}" if local else None
        out[K] = v
    return out


DATE_RE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(\.\d+)?$")


class DB:
    def __init__(self, path, now):
        self.con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row
        self.now = now
        self.con.create_function("NOW", 0, lambda: now.strftime("%Y-%m-%d %H:%M:%S"))
        self.con.create_function("DAYDIFF", 2, self._daydiff)
        self.con.create_function("HOURDIFF", 2, self._hourdiff)
        self.con.execute(
            "CREATE TEMP VIEW vsceners AS SELECT p.*, l1.name AS group1, l2.name AS group2, c.country AS country,"
            " c.language AS language, c.flag AS flag FROM PEOPLE p LEFT JOIN LINKS l1 ON p.idgroup1 = l1.id"
            " LEFT JOIN LINKS l2 ON p.idgroup2 = l2.id INNER JOIN COUNTRIES c ON p.idcountry = c.id")
        self.types = {}
        for name, t in self.con.execute("SELECT name, sql_type FROM _columns"):
            t = t.split("(")[0]
            self.types.setdefault(name.upper(), set()).add(t)

    @staticmethod
    def _parse(s):
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S") if s else None

    def _daydiff(self, a, b):
        a, b = self._parse(a), self._parse(b)
        return None if a is None or b is None else (b.date() - a.date()).days

    def _hourdiff(self, a, b):
        a, b = self._parse(a), self._parse(b)
        if a is None or b is None:
            return None
        return int((b.replace(minute=0, second=0) - a.replace(minute=0, second=0)).total_seconds() // 3600)

    def convert(self, k, v):
        if v is None:
            return None
        t = self.types.get(k.upper(), ())
        if isinstance(v, str) and DATE_RE.match(v) and ("datetime" in t or "smalldatetime" in t or not t):
            return datetime.strptime(v[:19], "%Y-%m-%d %H:%M:%S")
        if t and t <= {"bit"} and isinstance(v, int):
            return bool(v)
        return v

    def rows(self, sql, params=()):
        out = []
        for r in self.con.execute(sql, params):
            out.append(redact({k: self.convert(k, r[k]) for k in r.keys()}))
        return out

    def scalar(self, sql, params=()):
        r = self.con.execute(sql, params).fetchone()
        return r[0] if r else None


def is_int(v):
    return v is not None and re.fullmatch(r"-?\d+", str(v).strip()) is not None


def groups_(s):
    """id3F_ojuice.Groups: ' / group' without a parenthesised suffix."""
    if s is None:
        return None
    s = str(s)
    i = s.find("(")
    if i >= 0:
        s = s[:i].rstrip()
    return " / " + s if s else None


def groupsshort_(s):
    if s is None:
        return None
    s = str(s)
    if len(s) > 16:
        s = s[:16] + "..."
    i = s.find("/")
    if i >= 0:
        s = s[:i] + "^..."
    return " / " + s if s else None


def net_date(d, fmt):
    """.NET custom DateTime formats used by oj_bm.cs (en-US)."""
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
              "October", "November", "December"]
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return (fmt.replace("MMMM", "\x01").replace("ddd", "\x02").replace("HH", f"{d.hour:02d}")
            .replace("mm", f"{d.minute:02d}").replace(" d ", f" {d.day} ").replace(", d", f", {d.day}")
            .replace("\x01", months[d.month - 1]).replace("\x02", days[d.weekday()]))


class OJMacros:
    """Macro sets `OJ` and `ID3` for one database."""

    def __init__(self, db, nick_photos=(), multipage_style=None):
        self.db = db
        self.nick_photos = set(nick_photos)
        self.cache = {}
        self.now = db.now
        self.functions = {"groups_": groups_, "groupsshort_": groupsshort_}
        self.multipage_style = multipage_style

    def photo_ok(self, r):
        return f"{r['ID']}.{r['PIC_EXT']}".lower() in self.nick_photos

    # ------------------------------------------------------------------------------- E: macros
    CONSTANT_E = {"OJ:DAILYNEWS", "OJ:LASTPIC", "OJ:LASTPHOTO", "OJ:NBLINKS", "OJ:CURRENCIES_UPD", "OJ:IX_SERVERS"}

    def exec(self, name, a, page):
        """E: macro: rows to add as name:COLUMN (+ extra values written in page.dic)."""
        if name not in self.CONSTANT_E:
            return self._exec(name, a, page)
        if name not in self.cache:
            before = dict(page.dic)
            rows = self._exec(name, a, page)
            extra = {k: v for k, v in page.dic.items() if k not in before}
            self.cache[name] = (rows, extra)
        rows, extra = self.cache[name]
        for k, v in extra.items():
            page.dic.setdefault(k, v)
        return rows

    def _exec(self, name, a, page):
        db = self.db
        dic = page.dic
        if name == "ID3:TXT2GIF":
            buf = a[4]
            if buf and buf.startswith("@@"):
                o = dic.get(buf[2:-2].replace("|", ":"))
                buf = vstr(o) if o is not None else None
            dic[f"{name}:{a[5]}"] = txt2gif(buf, a[1] or "", a[3] or "-1", a[2] or "0")
            return None
        if name == "ID3:DATE":
            dt = self.now + timedelta(days=int(a[2]) if is_int(a[2]) else 0)
            v = {"2": vstr(dt), "JOUR": dt.day, "MOIS": dt.month, "ANNEE": dt.year}.get(a[1])
            if v is not None:
                dic[name + ":VAL"] = str(v)
            return None
        if name in ("ID3:NOCACHE", "ID3:NOUACACHE", "NEC:STATS"):
            return None
        if name == "OJ:NICK":
            if not is_int(a[1]):
                return None
            rows = db.rows("SELECT a.*, c.computer, l1.url urlgroup1, l2.url urlgroup2 FROM vsceners a"
                           " INNER JOIN computers c ON a.idcomputer = c.id LEFT JOIN links l1 ON l1.id = a.idgroup1"
                           " LEFT JOIN links l2 ON l2.id = a.idgroup2 WHERE a.id = ?", (int(a[1]),))
            if not rows:
                page.redirect = "/"
                return None
            r = rows[0]
            for n, col in ((1, "IDGROUP1"), (2, "IDGROUP2")):
                if r.get(col) is not None:
                    dic[f"{name}:NBGROUP{n}"] = db.scalar("SELECT COUNT(id) FROM people WHERE idgroup1 = ? OR idgroup2 = ?",
                                                           (r[col], r[col]))
            fn = "".join("/" + f for f in ("ANS", "ASC", "COD", "GFX", "RAY", "ORG", "SWP", "SYS", "WEB", "ZIK")
                         if r.get("IS" + f)).replace("//", "/")
            if len(fn) > 1:
                dic[name + ":FUNCTIONS"] = fn[1:]
            if r.get("PIC_EXT") and not self.photo_ok(r):
                r["PIC_EXT"] = None
            return rows
        if name == "OJ:ARTICLE":
            if not is_int(a[1]):
                return None
            return db.rows("SELECT * FROM news WHERE id = ? AND isarticle = 1 AND active = 1", (int(a[1]),))
        if name == "OJ:PICTURE":
            if not is_int(a[1]):
                return None
            return db.rows("SELECT * FROM pictures WHERE id = ? AND isactive = 1", (int(a[1]),))
        if name == "OJ:PARTY":
            if not is_int(a[1]):
                return None
            return db.rows("SELECT a.*, a.id_country idcountry, b.flag, b.country FROM parties a, countries b"
                           " WHERE a.id_country = b.id AND a.id = ? AND a.active = 1", (int(a[1]),))
        if name == "OJ:LINK":
            if not is_int(a[1]):
                return None
            return db.rows("SELECT a.*, b.catname, c.country, c.language FROM links a, linkscategories b, countries c"
                           " WHERE a.idcategory = b.id AND a.idcountry = c.id AND a.id = ? AND a.isactive = 1", (int(a[1]),))
        if name == "OJ:ONELINER":
            if not is_int(a[1]):
                return None
            return db.rows("SELECT * FROM oneliner WHERE id = ?", (int(a[1]),))
        if name == "OJ:LASTPIC":
            return db.rows("SELECT * FROM pictures WHERE isphoto = 0 AND isactive = 1 ORDER BY dtadded DESC, id DESC LIMIT 1")
        if name == "OJ:LASTPHOTO":
            return db.rows("SELECT * FROM pictures WHERE isphoto = 1 AND isactive = 1 ORDER BY dtadded DESC, id DESC LIMIT 1")
        if name == "OJ:DAILYNEWS":
            dic[name + ":NBWEBCAMS"] = 0     # webcam checker stopped: frozen as none up
            q = [("pictures WHERE isactive = 1 AND isphoto = 0 AND HOURDIFF(dtadded, NOW()) <= 48", "NBPICS"),
                 ("pictures WHERE isactive = 1 AND isphoto = 1 AND HOURDIFF(dtadded, NOW()) <= 48", "NBPHOTOS"),
                 ("people WHERE HOURDIFF(dt_crea, NOW()) <= 24", "NBNICKCREA"),
                 ("people WHERE HOURDIFF(dt_crea, NOW()) > 24 AND HOURDIFF(dt_upd, NOW()) <= 24", "NBNICKUPD"),
                 ("links WHERE isactive = 1 AND idcategory = 15 AND HOURDIFF(dt, NOW()) <= 48", "NBGROUPS"),
                 ("links WHERE isactive = 1 AND idcategory <> 15 AND HOURDIFF(dt, NOW()) <= 48", "NBLINKS")]
            for where, key in q:
                dic[f"{name}:{key}"] = db.scalar("SELECT COUNT(*) FROM " + where)
            return None
        if name == "OJ:NBLINKS":
            return db.rows("SELECT COUNT(*) nb FROM links WHERE isactive = 1 AND (url IS NOT NULL OR alturl IS NOT NULL)")
        if name == "OJ:CURRENCIES_UPD":
            return db.rows("SELECT MAX(dt) dt FROM currencies")
        if name == "OJ:IX_SERVERS":
            return db.rows("SELECT COUNT(id) nbservers, SUM(nbfiles) nbfiles, SUM(size) totalsize, MAX(lastupdate) lastupdate"
                           " FROM ix_servers WHERE active = 1 AND isup = 1 AND nbfiles > 0")
        # GOLINK (click counter + redirect), GETPUB (banners), LOGOUT, admin and session macros:
        # nothing for an anonymous visitor of a read-only archive.
        return None

    # ------------------------------------------------------------------------------- B: loops
    def loop(self, stored, name, a, page, select_top):
        """Rows of loop `name` (full list; the engine pages it) as (rows, total, invert)."""
        if stored == "OJ:PICLIB":
            for k in ("EVENT", "AUTHOR", "WHO", "KW", "RANK", "YEAR"):
                page.dic["FORM:" + k] = ""
        q = tuple(page.req.query.get(k) for k in ("sn", "act", "sp", "sl", "snick", "idgroup", "event", "author", "who", "kw", "rank", "year"))
        key = (stored, tuple(a[1:6]), q, select_top if stored in self.TOP_DEPENDENT else 0)
        if key not in self.cache:
            self.cache[key] = self._loop(stored, name, a, page, select_top)
        res = self.cache[key]
        if res is None and stored == "OJ:LINKS" and (a[2] or "").lower() not in ("groups", "www") \
                and (a[1] or "").lower() not in ("recent", "ressources", "parties", "softs", "forums", "groups"):
            page.redirect = "/www/centers/" + page.req.page_name
        return res

    TOP_DEPENDENT = {"OJ:ONELINER", "OJ:LASTDEMOS", "OJ:BIRTHDAYS", "OJ:PARTIESRESULTS", "OJ:TOPNEWSWRITERS"}

    def _loop(self, stored, name, a, page, select_top):
        db = self.db
        rows = None
        if stored == "ID3:ALPHABET":
            rows = [{"LETTRE": chr(c)} for c in range(ord("A"), ord("Z") + 1)]
        elif stored == "ID3:COUNT":
            rows = [{"NB": i} for i in range(int(a[1]) if is_int(a[1]) else 0)]
        elif stored == "OJ:NEWS":
            rows = self.news(a, page)
        elif stored == "OJ:ONELINER":
            if a[1] == "BOO":
                rows = db.rows("SELECT o.id, o.dt, o.hostname, o.sentence, o.nick, o.group1, IFNULL(o.flag, '00') flag,"
                               " IFNULL(c.country, 'Unknown') country FROM oneliner o LEFT JOIN countries c ON c.flag = o.flag"
                               " WHERE o.id NOT IN (SELECT idol FROM boo_ol) ORDER BY o.dt, o.id")
            else:
                rows = db.rows("SELECT o.id, o.dt, o.hostname, o.sentence, o.nick, o.group1, IFNULL(o.flag, '00') flag,"
                               " IFNULL(c.country, 'Unknown') country FROM oneliner o LEFT JOIN countries c ON c.flag = o.flag"
                               " WHERE o.id IN (SELECT id FROM oneliner ORDER BY dt DESC, id DESC LIMIT ?) ORDER BY o.dt, o.id",
                               (select_top,))
            self.mark_new_day(rows, "DT")
        elif stored == "OJ:BOO":
            rows = db.rows("SELECT b.idboo, b.comment, o.id, o.dt, o.hostname, o.sentence, o.nick, o.group1,"
                           " IFNULL(o.flag, '00') flag, IFNULL(c.country, 'Unknown') country FROM oneliner o"
                           " INNER JOIN boo_ol bol ON bol.idol = o.id INNER JOIN boo b ON b.idboo = bol.idboo"
                           " LEFT JOIN countries c ON c.flag = o.flag ORDER BY b.dt, b.idboo, o.dt, o.id")
            prev = None
            for r in rows:
                if r["IDBOO"] != prev:
                    r["ISNEWBOO"] = 1
                prev = r["IDBOO"]
        elif stored == "OJ:LASTDEMOS":
            rows = self.lastdemos(a, select_top)
        elif stored == "OJ:BIRTHDAYS":
            rows = self.birthdays(a, select_top)
        elif stored == "OJ:PARTIES":
            rows = self.parties(a, page)
        elif stored == "OJ:PARTIESRESULTS":
            rows = db.rows("SELECT a.*, c.country, c.flag FROM parties a, countries c WHERE a.id_country = c.id AND active = 1"
                           " AND a.results IS NOT NULL ORDER BY a.dt_end DESC, a.id DESC LIMIT ?", (select_top,))
        elif stored == "OJ:LINKS":
            rows = self.links(a, page)
        elif stored == "OJ:COUNTRIES":
            rows = db.rows("SELECT * FROM countries WHERE language IS NOT NULL ORDER BY language, country")
        elif stored == "OJ:COMPUTERS":
            rows = db.rows("SELECT * FROM computers ORDER BY id")
        elif stored == "OJ:LINKSCAT":
            rows = db.rows("SELECT * FROM linkscategories ORDER BY catname COLLATE NOCASE")
        elif stored == "OJ:NICKLIST":
            rows = self.nicklist(a, page)
        elif stored == "OJ:WALLPAPERS":
            rows = db.rows("SELECT CASE WHEN DAYDIFF(a.dt, NOW()) <= 7 THEN 1 ELSE 0 END new, a.*, b.nick, b.group1, b.group2"
                           " FROM wallpapers a LEFT JOIN vsceners b ON a.idpeople = b.id ORDER BY"
                           " CAST(a.clicks AS REAL) / (CASE DAYDIFF(a.dt, NOW()) WHEN 0 THEN 1 ELSE DAYDIFF(a.dt, NOW()) END)"
                           " + LENGTH(IFNULL(a.stars, '')) * 2 DESC, a.id")
        elif stored == "OJ:WEBCAMS":
            rows = []      # the webcam checker is gone: every webcam is shown as down (none up)
        elif stored == "OJ:PARTY_COMING":
            if is_int(a[1]):
                rows = db.rows("SELECT b.id, b.nick, b.group1, b.group2, b.flag, b.country FROM parties_coming a, vsceners b"
                               " WHERE a.id_people = b.id AND a.id_party = ? ORDER BY b.nick COLLATE NOCASE, b.id", (int(a[1]),))
        elif stored == "OJ:CURRENCIES":
            rows = db.rows("SELECT * FROM currencies ORDER BY ordre")
        elif stored == "OJ:TOPNEWSWRITERS":
            rows = db.rows("SELECT ng.nbnews, v.* FROM vsceners v INNER JOIN (SELECT id_people, COUNT(id) nbnews FROM news"
                           " WHERE dt > datetime(NOW(), '-4 months') GROUP BY id_people) ng ON ng.id_people = v.id"
                           " ORDER BY ng.nbnews DESC, v.id LIMIT ?", (select_top,))
        elif stored == "OJ:PICLIB":
            rows = self.piclib(a, page)
        if rows is None:
            return None
        return rows, None, False

    # -- helpers ------------------------------------------------------------------------------
    @staticmethod
    def mark_new_day(rows, col):
        prev = None
        for r in rows:
            d = r.get(col)
            if prev is None or d is None or d.day != prev.day or d.year != prev.year:
                r["ISNEWDAY"] = 1
            prev = d

    def news(self, a, page):
        if page.req.get("sn") is not None:
            return None                      # news search: client-side in the archive
        act = page.req.get("act")
        where = " AND a.active = 1"
        if act is not None:
            return None                      # inactive news (admin)
        params = []
        if is_int(a[1]):
            where += " AND a.category = ?"
            params.append(int(a[1]))
        else:
            where += " AND a.category <> 2"
        # The site showed the 200 latest news (TOP 200); the archive pages through every news of
        # the v3 era (older ones are in the 2000 archive).
        rows = self.db.rows(
            "SELECT a.id, a.id_people, a.dt, a.title, a.url, a.target, a.smalltxt, a.isarticle, a.category, a.clicks,"
            " c.nick, c.group1, c.group2, d.catname FROM news a, vsceners c, categories d"
            " WHERE a.id_people = c.id AND a.category = d.id AND a.dt >= ?" + where + " ORDER BY a.dt DESC, a.id DESC",
            [CUTOFF] + params)
        prev = None
        for r in rows:
            d = r["DT"]
            if prev is None or d.day != prev.day or d.year != prev.year:
                r["ISNEWDAY"] = 1
            prev = d
            r["SMALLTXT_DISP"] = self.news_text(r)
        return rows

    @staticmethod
    def news_text(r):
        """oj_bm.cs NEWS: MakeLinks, then every href through /news/golink.htm (here: direct links)."""
        from engine import make_links
        s = make_links(vstr(r.get("SMALLTXT")))
        return s

    def lastdemos(self, a, top):
        if a[1] == "2":
            rows = self.db.rows("SELECT * FROM lastdemos WHERE idsite = 2 ORDER BY dt DESC, id DESC")
        else:
            extra = " AND idsite = %d" % int(a[1]) if is_int(a[1]) else ""
            # random pick of the last 2 days in the original: least clicked first, ties by id
            rows = self.db.rows("SELECT * FROM lastdemos WHERE DAYDIFF(dt, NOW()) <= 2" + extra +
                                " ORDER BY clicks, id LIMIT ?", (top,))
        for r in rows:
            n, au = vstr(r.get("NAME")), vstr(r.get("AUTHOR"))
            if len(n) > 18:
                p = n[:18] + "..."
            elif len(n) == 18:
                p = n
            elif len(n + " (" + au) > 17:
                p = (n + " (" + au)[:17] + "...)"
            else:
                p = n + " (" + au + ")"
            r["PROD"] = p
        return rows

    def birthdays(self, a, top):
        md = f"{self.now.month:02d}{self.now.day:02d}"
        cols = "id, nick, group1, group2, birthmd, birthyear, uin, pic_ext, country, flag"
        if a[1] == "TODAY":
            rows = self.db.rows(f"SELECT {cols} FROM vsceners WHERE birthmd = ? ORDER BY birthmd, nick COLLATE NOCASE, id LIMIT ?",
                                (md, top))
        else:
            rows = self.db.rows(f"SELECT {cols} FROM vsceners WHERE birthmd > ? ORDER BY birthmd, nick COLLATE NOCASE, id LIMIT ?",
                                (md, top))
            if len(rows) < top:
                rows += self.db.rows(f"SELECT {cols} FROM vsceners WHERE birthmd <> '' AND birthmd < ? ORDER BY birthmd,"
                                     " nick COLLATE NOCASE, id LIMIT ?", (md, top))
        prev = None
        for r in rows:
            if r["BIRTHMD"] != prev:
                r["ISNEWDAY"] = 1
            prev = r["BIRTHMD"]
            r["NG"] = vstr(r.get("NICK")) + vstr(r.get("GROUP1"))
        return rows

    def parties(self, a, page):
        if page.req.get("sp") is not None and a[2] != "nosearch":
            return None
        if page.req.get("act") is not None:
            return None
        sql = "SELECT * FROM parties a, countries c WHERE a.id_country = c.id AND active = 1"
        order = " ORDER BY a.dt_begin, a.id"
        kind = (a[1] or "").lower()
        if kind == "next":
            sql += " AND a.dt_end >= NOW()"
        elif kind == "past":
            sql += " AND a.dt_end < NOW()"
            order = " ORDER BY a.dt_begin DESC, a.id DESC"
        rows = self.db.rows(sql + order)
        prev = None
        for r in rows:
            b, e = r["DT_BEGIN"], r["DT_END"]
            if prev is None or b.month != prev.month or b.year != prev.year:
                r["ISNEWMONTH"] = f"{b.month:02d}/{b.year}"
            prev = b
            if b.month == e.month:
                r["DATE"] = net_date(b, "MMMM, ddd d (HH:mm)") + " - " + net_date(e, "ddd d (HH:mm)")
            else:
                r["DATE"] = net_date(b, "ddd MMMM, d (HH:mm)") + " - " + net_date(e, "ddd MMMM, d (HH:mm)")
        return rows

    def links(self, a, page):
        if page.req.get("sl") is not None or page.req.get("act") is not None:
            return None
        db = self.db
        base = (" FROM links a, linkscategories c, countries d, vsceners e WHERE a.idcategory = c.id AND a.idcountry = d.id"
                " AND a.idpeople = e.id AND a.isactive = 1")
        sel = "SELECT a.*, c.*, d.*, e.nick, e.group1, e.group2, a.id id, a.url url, a.name name, a.dt dt"
        order = " ORDER BY a.idcategory ASC, a.clicks DESC, a.name COLLATE NOCASE ASC, a.id"
        k1 = (a[1] or "").lower()
        groups_sel = ("SELECT a.id, a.idcategory, a.name, a.url, a.alturl, a.clicks, a.dt, d.country, d.flag, COUNT(e.id) nbmembers"
                      " FROM links a INNER JOIN linkscategories c ON a.idcategory = c.id INNER JOIN countries d ON a.idcountry = d.id"
                      " LEFT JOIN vsceners e ON (e.idgroup1 = a.id OR e.idgroup2 = a.id) WHERE c.id = 15 AND a.isactive = 1")
        groups_order = " GROUP BY a.id ORDER BY a.name COLLATE NOCASE, a.id"
        cats = {"centers": 1, "alt": 2, "mags": 3, "cds": 4, "irc": 5, "hosting": 7}
        if k1 == "recent":
            rows = db.rows(sel + base + " AND DAYDIFF(a.dt, NOW()) <= 7 ORDER BY c.id, a.dt DESC, a.id")
        elif k1 == "ressources":
            rows = db.rows(sel + base + " AND c.id IN (8, 9, 10)" + order)
        elif k1 == "parties":
            rows = db.rows(sel + base + " AND c.id = 11 ORDER BY a.name COLLATE NOCASE, a.id")
        elif k1 == "softs":
            rows = db.rows(sel + base + " AND c.id = 12" + order)
        elif k1 == "forums":
            rows = db.rows(sel + base + " AND c.id = 13" + order)
        elif k1 == "groups":
            rows = db.rows(groups_sel + groups_order)
        elif (a[2] or "").lower() == "groups":
            if a[1] == "_":
                rows = db.rows(groups_sel + " AND NOT (lower(substr(a.name, 1, 1)) BETWEEN 'a' AND 'z')" + groups_order)
            else:
                rows = db.rows(groups_sel + " AND a.name LIKE ?" + groups_order, ((a[1] or "") + "%",))
        elif (a[2] or "").lower() == "www" and k1 in cats:
            rows = db.rows(sel + base + " AND c.id = ?" + order, (cats[k1],))
        else:
            page.redirect = "/www/centers/" + page.req.page_name
            return None
        prev = 0
        for r in rows:
            if r["IDCATEGORY"] != prev:
                r["ISNEWCAT"] = 1
            prev = r["IDCATEGORY"]
            if r.get("DT") and (self.now - r["DT"]).days <= 7:
                r["ISNEW"] = 1
        return rows

    def nicklist(self, a, page):
        where, params = [], []
        if page.req.get("snick") is not None:
            return None                      # people search: client-side in the archive
        idg = page.req.get("idgroup")
        if is_int(idg):
            where.append("(idgroup1 = ? OR idgroup2 = ?)")
            params += [int(idg), int(idg)]
        if a[1] is not None:
            v = a[1]
            if v[-1:] == "_":
                v = "_#"
            where.append("letter = ? COLLATE NOCASE")
            params.append(v[-1:])
        if not where:
            return None
        rows = self.db.rows("SELECT * FROM vsceners v WHERE " + " AND ".join(where) +
                            " ORDER BY nick COLLATE NOCASE, group1 COLLATE NOCASE, id", params)
        for r in rows:
            if r.get("DT_UPD") and (self.now - r["DT_UPD"]).days <= 7:
                r["ISNEW"] = 1
        return rows

    def piclib(self, a, page):
        t = (a[1] or "").lower()
        for k in ("event", "author", "who", "kw", "rank", "year", "act"):
            if page.req.get(k) is not None:
                return None                  # picture search: client-side in the archive
        for k in ("EVENT", "AUTHOR", "WHO", "KW", "RANK", "YEAR"):
            page.dic["FORM:" + k] = ""
        if t == "session":
            return None
        where = "a.isactive = 1"
        if t == "pics":
            where += " AND a.isphoto = 0"
        elif t == "photos":
            where += " AND a.isphoto = 1"
        rows = self.db.rows("SELECT a.* FROM pictures a WHERE " + where + " ORDER BY a.dtadded DESC, a.id DESC")
        for r in rows:
            if r.get("DTADDED") and (self.now - r["DTADDED"]).days <= 7:
                r["ISNEW"] = 1
        return rows

    # -- multipage ------------------------------------------------------------------------------
    def multipage(self, link, page, nbpages, pg):
        """id3x DoLoop multipage (`[ < / 8 / 9 / ... / > ]`, maxpages 10), then the site's
        MultiPageModif (oj_funcs.asp: digits as gifs, separators as datesep.gif cells), as the
        Wayback captures of 2006 show it."""
        maxp = 10
        left = 0
        if page - (maxp * 2 // 3) > 0:
            left = page - maxp // 2
        if left <= 0:
            left = 1
        right = left + maxp - 1
        if right > nbpages:
            right = nbpages
            left = max(nbpages - (maxp - 1), 1)
        s = ["[ "]
        if page > 1:
            s.append(f'<A href="{link}{page - 1}">&lt;</A> / ')
        for i in range(left, right + 1):
            if i > left:
                s.append(" / ")
            s.append(str(i) if i == page else f'<A href="{link}{i}">{i}</A>')
        if page < nbpages:
            s.append(f' / <A href="{link}{page + 1}">&gt;</A>')
        s.append(" ]")
        return multipage_modif("".join(s), page, nbpages)


def gif_digits(n, prefix="1_"):
    return "".join(f"<IMG src=p/{prefix}{c}.gif width=6 height=5 border=0>" for c in str(n))


def multipage_modif(st, page, nbpages):
    """MultiPageModif (id3/oj_funcs.asp), applied to the multipage string."""
    for i in range(1, nbpages + 1):
        if i != page:
            st = st.replace(f">{i}<", f">{gif_digits(i)}<")
    st = st.replace(f" {page} ", "<TD>" + gif_digits(page, "1_~") + "</TD>\r\n")
    st = st.replace("<A ", "<TD><A ").replace("</A>", "</A></TD>\r\n")
    st = st.replace("&lt;", "<IMG src=p/dateprev.gif width=7 height=5 border=0>")
    st = st.replace("&gt;", "<IMG src=p/datenext.gif width=7 height=5 border=0>")
    sep = "<TD><IMG src=p/datesep.gif width=2 height=10 hspace=3></TD>\r\n"
    return st.replace("/ ", sep).replace(" /", sep).replace("[", sep).replace("]", sep)
