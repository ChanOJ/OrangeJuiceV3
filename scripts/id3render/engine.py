"""Template engine of id3 / id3x, reimplemented for a static rendering.

The id3 ASP engine (`id3/cgi/_f.asp`: init, disp, do_exec, do_bcl, MakeFuncs) and its .NET
successor id3x (`id3x/core/start.aspx`, same template language) render a skin file
`z/<skin>/<page>` after:

  1. SSI includes `<!--#include virtual="x" -->` and `$$INCL:x$$` (from the skin folder, then the
     fallback skin), indentation and blank lines removed, `$$-$$` / `$$_$$` (skin URL) replaced;
  2. a split on `$$`: odd items are macros (`E:` exec query, `B:` loop query, `SET:`, `INIT:`,
     `IF...`, `BIF`, `DO`, `LOOP`, `BFIN`, `END`, value macros with filters);
  3. the `E:` macros run while parsing, the `B:` loops after, then the `SET:`s;
  4. a display pass over the token array with jump indexes (IF -> matching END, DO <-> LOOP);
  5. `src=` / `background=` image paths moved under `/z/<skin>/`.

This module follows the ASP code line by line (it is the readable one; the id3x parser lives in
a DLL that is not ours to publish nor to run), with the id3x behaviours seen in its sources and in
Wayback captures (multipage links, en-US dates). The data macros (`OJ:...`, `ID3:...`) are
provided by a `MacroSet` (see `oj.py`).
"""
import html
import math
import re
import zlib
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

WORD_CHARS = "abcdefghijklmnopqrstuvwxyz\u00e0\u00e4\u00e2\u00e9\u00e8\u00ea\u00eb\u00ef\u00ee\u00f6\u00f4\u00fc\u00fb\u00f9\u00e7\u00f1\u00f8\u00c5\u00c60123456789"
ACCENTS_FROM = "\u00c0\u00c4\u00c2\u00c9\u00c8\u00ca\u00cb\u00cf\u00ce\u00d6\u00d4\u00dc\u00db\u00d9\u00db\u00c7\u00e0\u00e4\u00e2\u00e9\u00e8\u00ea\u00eb\u00ef\u00ee\u00f6\u00f4\u00fc\u00fb\u00f9\u00fb\u00e7"
ACCENTS_TO = "AAAEEEEIIOOUUUUCaaaeeeeiioouuuuc"

INCLUDE = '<!--#include virtual="'


# --------------------------------------------------------------------------------------------
# Value conversions (VBScript / .NET semantics)
# --------------------------------------------------------------------------------------------
def vstr(v):
    """CStr_ / .NET ToString(): None -> '', bool -> True/False, dates in en-US (id3x locale)."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, datetime):
        h = v.hour % 12 or 12
        return f"{v.month}/{v.day}/{v.year} {h}:{v.minute:02d}:{v.second:02d} {'AM' if v.hour < 12 else 'PM'}"
    if isinstance(v, float):
        return repr(v).rstrip("0").rstrip(".") if v != int(v) else str(int(v))
    return str(v)


def is_numeric(v):
    if v is None or v == "":
        return False
    if isinstance(v, (bool, int, float)):
        return True
    try:
        return math.isfinite(float(str(v).replace(",", ".")))
    except ValueError:
        return False


def clng(v):
    try:
        if isinstance(v, bool):
            return -1 if v else 0
        return int(round(float(str(v).strip())))
    except (TypeError, ValueError, OverflowError):
        return 0


def cdbl(v):
    try:
        if isinstance(v, bool):
            return -1.0 if v else 0.0
        return float(str(v).strip())
    except (TypeError, ValueError):
        return 0.0


def truthy(v):
    """$$IF:X$$ (case 4): numeric -> non zero, else non blank. Booleans by value (id3x)."""
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if is_numeric(v):
        return clng(v) != 0
    return str(v).strip() != ""


def truthy0(v):
    """$$IF0$$ / row values: not null, not '', not '0', not False."""
    if v is None or v is False:
        return False
    s = vstr(v)
    return s not in ("", "0", "False")


def html_encode(s):
    """Server.HTMLEncode / HttpUtility.HtmlEncode (.NET 1.1): & < > " and 160-255 as &#n;."""
    out = []
    for c in s:
        if c == "&":
            out.append("&amp;")
        elif c == "<":
            out.append("&lt;")
        elif c == ">":
            out.append("&gt;")
        elif c == '"':
            out.append("&quot;")
        elif 160 <= ord(c) <= 255:
            out.append(f"&#{ord(c)};")
        else:
            out.append(c)
    return "".join(out)


def url_encode(s):
    """HttpUtility.UrlEncode: space -> +, lower-case hex, UTF-8 (the .NET default)."""
    return re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), quote_plus(s, safe="-_.!*'()"))


def escape_js(s):
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'").replace("\r", "\\r").replace("\n", "\\n")


def vire_accents(s):
    for a, b in zip(ACCENTS_FROM, ACCENTS_TO):
        s = s.replace(a, b)
    return s


def ncase(s):
    out, prev = [], " "
    for c in s:
        out.append(c.lower() if prev.lower() in WORD_CHARS and prev != "" else c.upper())
        prev = c
    return "".join(out)


def pcase(s):
    return s[:1].upper() + s[1:].lower()


def scase(s):
    s += " "
    word, out = "", []
    for c in s:
        if c.lower() not in WORD_CHARS:
            if len(word) == 1:
                word = word.upper()
            elif word != word.upper():
                word = word[:1].upper() + word[1:].lower()
            out.append(word + c)
            word = ""
        else:
            word += c
    return "".join(out)[:-1]


def txt2gif(s, font, height, width):
    if s is None:
        return None
    height = f" height={height}" if clng(height) != -1 else ""
    width = f" width={width}" if clng(width) != 0 else ""
    s = vire_accents(vstr(s))
    names = {" ": "space", "?": "qm", "#": "diese", ".": "dot", "/": "slash", "@": "at", "*": "star",
             "<": "lt", ">": "gt", '"': "quot"}
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "&":
            low = s[i:i + 5].lower()
            if low.startswith("&gt;"):
                c, i = "gt", i + 3
            elif low.startswith("&lt;"):
                c, i = "lt", i + 3
            elif low == "&amp;":
                c, i = "&", i + 4
        else:
            c = names.get(c, c)
        out.append(f"<IMG src={font.replace('@', c)} border=0{width}{height}>")
        i += 1
    return "".join(out)


EMAIL_RE = re.compile(r"^[-\w._~]+@[\w][-\w.]*\.\w+$")
MAKELINKS_RE = re.compile(r"((?:http|ftp|www|news)[.:][^\s(),\"<>]+[^\s(),\"<>.]+|[-\w._~]+@[\w][-\w.]*\.[\w]+)", re.I)


def make_links(buf, classe=""):
    """MakeLinks (_f.asp): wrap bare URLs and e-mails in <A>."""
    for m in reversed(list(MAKELINKS_RE.finditer(buf))):
        chaine, start, end = m.group(0), m.start(), m.end()
        ok = True
        if start > 7:   # VBScript: debut > 7 (0-based FirstIndex)
            if buf[start - 1] == "=" or buf[start - 2] == "=":
                ok = False
            elif buf[start - 7:start].lower() in ("mailto:", "=&quot;"):
                ok = False
            elif buf[end:end + 4].lower() == "</a>":
                ok = False
        if not ok:
            continue
        cls = "{class}"
        if chaine[:3].lower() == "www":
            rep = f'<A href="http://{chaine}" target=_blank{cls}>{chaine}</A>'
        elif chaine[:4].lower() == "ftp.":
            rep = f'<A href="ftp://{chaine}" target=_blank{cls}>{chaine}</A>'
        elif EMAIL_RE.match(chaine):
            rep = f'<A href="mailto:{chaine}"{cls}>{chaine}</A>'
        elif chaine[:4].lower() != "news":
            rep = f'<A href="{chaine}" target=_blank{cls}>{chaine}</A>'
        else:
            continue
        buf = buf[:start] + rep + buf[end:]
    return buf.replace("{class}", f" class={classe}" if classe else "")


SMILIES = [(" :)", 1), (" :-)", 1), (" =)", 1), (" ;)", 2), (":shocked:", 3), (" :D", 4), (":weird:", 5),
           (" :(", 6), (" =(", 6), (" :-(", 6), (" :/", 6), (" =/", 6), (":angry:", 7), (":clown:", 8),
           (":star:", 10), (":sick:", 11), (":evil:", 15), (":cool:", 16), (" :P", 17), (" =P", 17),
           (":kick:", 18), (" :'(", 19), (":up:", 20), (":down:", 21), (" :o", 22), (" =o", 22), (":wtf:", 25),
           (":mad:", 29), (":claps:", 32), (":lol:", 36)]


def make_smiles(buf):
    for code, n in SMILIES:
        sp = "  " if n != 1 else " "
        buf = buf.replace(code, f' <IMG src="/zi/smilies/smiley{n}.gif"{sp}alt="smiley" ALIGN=absmiddle> ')
    return buf


def quoted2italic(buf):
    out = []
    for line in buf.split("\r\n"):
        if line[:2] in ("> ", ">>") or line[:5] == "&gt; " or line[:8] == "&gt;&gt;" or line[:1] in ("%", "|"):
            out.append(f"<I>{line}</I>\r\n")
        else:
            out.append(line + "\r\n")
    return "".join(out)


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def format_width(s, w):
    """FormatWidth (_f.asp): cut a text in lines of at most w characters, at word ends."""
    s = vstr(s)
    out = []
    while len(s) > w:
        tmp = s[:w]
        k = len(tmp)
        while k > 0 and tmp[k - 1].lower() in WORD_CHARS:
            k -= 1
        if k > 0:
            out.append(tmp[:k] + "\r\n")
            s = s[k:]
        else:
            out.append(tmp + "\r\n")
            s = s[w:]
    out.append(s)
    return "".join(out)


def to_date(v):
    if isinstance(v, datetime):
        return v
    return None


def make_funcs(args, value, functions=None, ctx=None):
    """MakeFuncs (_f.asp) plus the id3x functions of the site (`groups_`, `groupsshort_`)."""
    a = re.sub(r" +", " ", args.replace("%20", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ").strip()).split(" ")
    v = value
    i = 0
    while i < len(a):
        f = a[i]
        if f == "exec":
            # `exec 68+(XX*13)`, `exec XX\\60`, `exec CLng(XX*100)`: VBScript Execute of an arithmetic
            # expression on the value. A masked value (birth year) stays masked: no age is computed.
            i += 1
            if is_numeric(v):
                year = str(ctx.config["now"].year) if ctx is not None and "now" in ctx.config else "2006"
                expr = (a[i].replace("XX", repr(cdbl(v))).replace("Year(Date)", year).replace("CLng(", "round(")
                        .replace("\\", "//").replace("%", " % "))
                if re.fullmatch(r"[\d\s.+\-*/()%round]+", expr):
                    r = eval(expr, {"__builtins__": {}, "round": round}, {})   # digits, operators, round only
                    v = int(r) if float(r).is_integer() else r
        elif f == "format":
            i += 1
            v = format_width(v, clng(a[i])) if v is not None else None
        elif f == "substring":
            i += 1
            v = vstr(v)[clng(a[i]):]
        elif f in ("mod2", "alt"):
            v = a[i + 1 + (clng(v) % 2)]
            i += 2
        elif f == "right":
            i += 1
            n = clng(a[i]); s = vstr(v); v = s[-n:] if n else ""
        elif f == "left":
            i += 1
            v = vstr(v)[:clng(a[i])]
        elif f == "truncate":
            i += 1
            s = vstr(v)
            if len(s) > clng(a[i]):
                v = s[:clng(a[i])] + (a[i + 1] if i + 1 < len(a) else "")
            i += 1
        elif f in ("nbr", "formatnumber"):
            i += 1
            if v is not None:
                v = f"{cdbl(v):,.{clng(a[i])}f}"
        elif f == "date":
            i += 1
            d = to_date(v)
            if d is not None:
                k = a[i]
                if k in ("jour",):
                    v = d.day
                elif k == "mois":
                    v = d.month
                elif k in ("nommois", "monthname"):
                    v = MONTHS[d.month - 1]
                elif k == "petitmois":
                    v = MONTHS[d.month - 1][:3]
                elif k == "annee":
                    v = d.year
                elif k == "sql":
                    v = d.strftime("%Y-%m-%d %H:%M:%S")
                elif k == "dt":
                    v = d.strftime("%d/%m/%Y %H:%M:%S")
                elif k in ("weekday", "dayname"):
                    v = DAYS[d.weekday()]
                elif k == "1":
                    v = f"{DAYS[d.weekday()]}, {MONTHS[d.month - 1]} {d.day:02d}, {d.year}"
                elif k == "2":
                    v = f"{d.month}/{d.day}/{d.year}"
                elif k == "3":
                    h = d.hour % 12 or 12
                    v = f"{h}:{d.minute:02d}:{d.second:02d} {'AM' if d.hour < 12 else 'PM'}"
                elif k == "4":
                    v = f"{d.hour:02d}:{d.minute:02d}"
        elif f in ("min", "minuscules", "lcase", "lowercase"):
            v = vstr(v).lower()
        elif f in ("maj", "capitales", "uppercase", "ucase"):
            v = vstr(v).upper()
        elif f in ("dslash", "2sl"):
            v = vstr(v).replace("\\", "\\\\")
        elif f in ("mixed", "mixedcase", "pcase", "mcase"):
            v = pcase(vstr(v)) if v is not None else None
        elif f in ("ncase", "namecase"):
            v = ncase(vstr(v)) if v is not None else None
        elif f == "scase":
            v = scase(vstr(v)) if v is not None else None
        elif f in ("urlencode", "encode", "url"):
            if v is not None:
                v = url_encode(vstr(v))
        elif f == "urlencode20":
            if v is not None:
                v = url_encode(vstr(v)).replace("+", "%20")
        elif f in ("htmlencode", "html"):
            if v is not None:
                v = html_encode(vstr(v))
        elif f == "ncencode":
            if v is not None:
                v = re.sub("[\u201c\u201d\"\u2018\u2019']", "&#39;", vstr(v))
        elif f in ("day", "jour"):
            d = to_date(v); v = d.day if d else v
        elif f in ("month", "mois"):
            d = to_date(v); v = d.month if d else v
        elif f in ("year", "annee"):
            d = to_date(v); v = d.year if d else v
        elif f in ("hour", "heure"):
            d = to_date(v); v = d.hour if d else v
        elif f == "minute":
            d = to_date(v); v = d.minute if d else v
        elif f in ("second", "seconde"):
            d = to_date(v); v = d.second if d else v
        elif f == "removehtml":
            v = re.sub(r"<[^>]*>", "", vstr(v))
        elif f == "ampencoding":
            v = vstr(v).replace("&", "&amp;")
        elif f == "to2":
            if v is not None:
                s = vstr(v); v = "0" + s if len(s) == 1 else s
        elif f == "to":
            i += 1
            s = vstr(v); v = "0" * max(0, clng(a[i]) - len(s)) + s
        elif f == "tocsv":
            if v is not None:
                v = vstr(v).replace('"', "").replace("\r\n", " ")
        elif f == "chiffre":
            i += 1
            v = (clng(v) // 10 ** (clng(a[i]) - 1)) % 10
        elif f == "makehref":
            if EMAIL_RE.match(vstr(v)):
                v = "mailto:" + vstr(v)
        elif f == "escape":
            if v is not None:
                v = escape_js(vstr(v))
        elif f == "pluriel":
            v = "s" if clng(v) > 1 else ""
        elif f == "br2crlf":
            if v is not None:
                v = vstr(v).replace("<br>", "\r\n").replace("<BR>", "\r\n")
        elif f == "crlf2br":
            if v is not None:
                v = vstr(v).replace("\r\n", "<BR>")
        elif f == "sp2nbsp":
            if v is not None:
                v = vstr(v).replace(" ", "\u00a0")
        elif f == "txt2gif":
            i += 1
            font = a[i]
            w = a[i + 1] if i + 1 < len(a) else "0"
            h = a[i + 2] if i + 2 < len(a) else "-1"
            w = "" if w == '""' else w
            h = "" if h == '""' else h
            v = txt2gif(v, font, h, w)
            i += 2
        elif f == "quoted2italic":
            v = quoted2italic(vstr(v))
        elif f == "avance":
            v = clng(v) + 1
        elif f == "recule":
            v = clng(v) - 1 if clng(v) > 1 else 0
        elif f == "trim":
            v = vstr(v).strip()
        elif f == "makelinks":
            i += 1
            v = make_links(vstr(v), a[i] if i < len(a) else "")
        elif f == "makesmiles":
            v = make_smiles(vstr(v))
        elif f == "removeqry":
            i += 1
            s = vstr(v)
            pat = re.compile(r"(^|\?|&)" + re.escape(a[i]) + r"=[^&]*(&|$)", re.I)
            while pat.search(s):
                s = pat.sub(r"\1", s)
            if s.startswith("&"):
                s = s[1:]
            if s.endswith("&"):
                s = s[:-1]
            v = s
        elif f == "":
            pass
        else:
            if ":" not in f:
                if functions is not None and f in functions:
                    v = functions[f](v)
                elif ctx is not None:
                    ctx.unknown_filters.add(f)     # reported by the build; the value is left as it is
        i += 1
    return v


# --------------------------------------------------------------------------------------------
# Template loading
# --------------------------------------------------------------------------------------------
class SkinFiles:
    """Reads `z/<skin>/<file>` with the engine's fallback folder (cfg_custom_rep)."""

    def __init__(self, zdir, skin, fallback=None, virtual=None):
        self.zdir, self.skin, self.fallback = Path(zdir), skin, fallback
        self.virtual = virtual or {}       # archive pages written by the build (name -> text)

    def find(self, name):
        name = name.replace(",", "/").replace("\\", "/")
        if name in self.virtual:
            return name
        for rep in (self.skin, self.fallback):
            if rep:
                p = self.zdir / rep / name
                if p.is_file():
                    return p
        return None

    def read(self, name):
        if name in self.virtual:
            return self.virtual[name]
        p = self.find(name)
        return None if p is None else p.read_bytes().decode("cp1252", errors="replace")

    def load(self, name):
        """init(): template text after includes and whitespace compaction, or None (404)."""
        cache = self.__dict__.setdefault("_cache", {})
        if name not in cache:
            cache[name] = self._load(name)
        return cache[name]

    def _load(self, name):
        page = self.read(name)
        if page is None:
            return None
        page = page.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        pos = page.find(INCLUDE)
        guard = 0
        while pos != -1:
            guard += 1
            if guard > 5000:
                raise RuntimeError(f"include loop in {name}")
            start = pos + len(INCLUDE)
            end, fin = page.find('" -->', start), '" -->'
            end3 = page.find('"-->', start)
            if end3 != -1 and (end == -1 or end3 < end):
                end, fin = end3, '"-->'
            if end == -1:
                break
            inc = page[start:end]
            content = self.read(inc)
            page = page.replace(INCLUDE + inc + fin, content.replace("\r\n", "\n").replace("\n", "\r\n") if content is not None else "")
            pos = page.find(INCLUDE, pos)
        pos = page.find("$$INCL:")
        while pos != -1:
            end = page.find("$$", pos + 7)
            if end == -1:
                break
            inc = page[pos + 7:end]
            content = self.read("incl_" + inc) or ""
            page = page.replace("$$INCL:" + inc + "$$", content)
            pos = page.find("$$INCL:", pos)
        if "text/plain" not in page[:1000]:
            page = re.sub(r"\r\n\s+", "\r\n", page)
            page = re.sub(r"\r\n(</?(?:TR|TD|TABLE|FORM)[^>\r\n]*>)(?:\r\n)?", r"\1", page, flags=re.I)
        page = page.replace("$$BCL$$", "$$LOOP$$")
        rep = self.skin
        page = page.replace("$$-$$", f"/z/{self.fallback or rep}/" if self.fallback else f"/z/{rep}/")
        page = page.replace("$$_$$", f"/z/{rep}/")
        page = page.replace("%2F", "/").replace("%3F", "?").replace("%3D", "=")
        return page


def before_space(s):
    i = s.find(" ")
    return s if i < 0 else s[:i]


def after_space(s):
    i = s.find(" ")
    return "" if i < 0 else s[i + 1:]


# --------------------------------------------------------------------------------------------
# Page rendering
# --------------------------------------------------------------------------------------------
class Loop:
    __slots__ = ("rows", "invert")

    def __init__(self, rows, invert=False):
        self.rows, self.invert = rows, invert


class Request:
    """A GET request of the static crawl: path elements and query parameters."""

    def __init__(self, path, query=None):
        self.path = path                       # '/16139/nick.htm'
        self.query = dict(query or {})         # lower-case keys
        self.used = set()                      # query parameters the page read

    def get(self, name):
        k = name.lower()
        self.used.add(k)
        v = self.query.get(k)
        return v if v not in (None, "") else None

    @property
    def elements(self):
        return [e for e in self.path.split("/")[1:]] if self.path != "/" else ["index.htm"]

    @property
    def page_name(self):
        return self.elements[-1] or "index.htm"


class Page:
    """One rendering of one template (init + disp)."""

    def __init__(self, files, macros, request, config):
        self.files, self.macros, self.req, self.config = files, macros, request, config
        self.dic = {}
        self.loops = {}
        self.redirect = None
        self.out_of_range = None
        self.cur_loop = ""
        self.dic["SRV"] = config["host"]
        self.unknown_filters = set()
        self.functions = macros.functions

    # -- dictionary helpers ------------------------------------------------------------------
    def add(self, key, value):
        if key not in self.dic:
            self.dic[key] = value

    def rquery(self, exclude=""):
        parts = []
        for k, v in self.req.query.items():
            if k == exclude.lower() or k == "u" or k[-2:] in (".x", ".y"):
                continue
            parts.append(f"{k}={url_encode(v)}")
        return ("?" + "&".join(parts)).replace(" ", "") if parts else ""

    # -- macro argument parsing (do_exec / do_bcl) --------------------------------------------
    def macro_args(self, parts, start):
        args = [None]
        i = start
        n = len(parts) - 1
        rep = self.req.elements
        while i <= n:
            p = parts[i]
            if p == "REP":
                i += 1
                if i <= n:
                    k = clng(parts[i])
                    j = len(rep) - 1 - k
                    args.append(rep[j] if 0 <= j < len(rep) else None)
                else:
                    args.append(None)
            elif p == "VAL":
                i += 1
                args.append(self.dic.get(parts[i].replace("_", ":")) if i <= n else None)
            elif p == "QRY":
                i += 1
                args.append(self.req.get(parts[i]) if i <= n else None)
            else:
                args.append(p)
            i += 1
        args += [None] * 8
        return args

    def do_exec(self, macro):
        parts = macro.split(":")
        name = parts[1] + ":" + parts[2]
        args = self.macro_args(parts, 3)
        rows = self.macros.exec(name, args, self)
        if rows:
            for row in rows:
                for k, v in row.items():
                    self.add(f"{name}:{k.upper()}", v)

    def do_loop(self, spec, layout):
        parts = ("B:" + spec).split(":")
        name = ":".join(parts[1:4]) if len(parts) > 3 else ":".join(parts[1:3])
        stored = parts[1] + ":" + parts[2]
        args = self.macro_args(parts, 3)
        col, pli = layout.get(name, (1, 9999))
        pli = clng(pli)
        pgkey = name.replace(":", "_") + "pg"
        if pli == 0:
            pli, page = 999, 1
        else:
            page = self.req.get(pgkey)
            page = clng(page) if page and is_numeric(page) else 1
            page = max(page, 1)
        res = self.macros.loop(stored, name, args, self, select_top=pli * page)
        if res is None:
            self.dic["_btot_" + name] = 0
            self.loops[name] = Loop([])
            return
        rows, total, invert = res
        nbr = total if total is not None else len(rows)
        self.add(name + ":NBR", nbr)
        if nbr > pli:
            nbpages = -(-nbr // pli)
            self.add(name + ":NBRPAGES", nbpages)
            self.add(name + ":PAGE", page)
            requery = self.req.page_name + self.rquery(pgkey)
            sep = "&" if "?" in requery else "?"
            link = f"{requery}{sep}{pgkey}="
            if page < nbpages:
                self.add(name + ":LIENAVANT", link + str(page + 1))
            if page > 1:
                self.add(name + ":LIENARRIERE", link + str(page - 1))
            self.add(name + ":MULTIPAGE", self.macros.multipage(link, page, nbpages, self))
        first = pli * (page - 1)
        if page > 1 and first >= nbr:
            self.out_of_range = pgkey           # past the last page: the build links page 1 instead
        rows = rows[first:first + pli]
        self.dic["_btot_" + name] = len(rows)
        self.loops[name] = Loop(rows, invert)

    # -- parse (init) ---------------------------------------------------------------------------
    def render(self, template_name):
        text = self.files.load(template_name)
        if text is None:
            return None
        tokens = text.split("$$")
        n = len(tokens)
        kind = [0] * n
        jump = [0] * n
        data = [""] * n
        filt = [""] * n
        stack = [0] * 60
        level = 0
        in_loop = False
        loop_name = ""
        loops_todo = []
        sets = []
        layout = {}
        for i in range(1, n, 2):
            m = tokens[i].replace("\r\n", " ")
            tokens[i] = m
            parts = m.split(":")
            head = parts[0]
            rest = m[m.find(":") + 1:] if ":" in m else ""
            if head == "B":
                loops_todo.append(rest)
                tokens[i] = ""
            elif head == "CONTENT":
                tokens[i] = ""
            elif head == "PUB":
                tokens[i] = ""
            elif head == "E":
                tokens[i] = ""
                self.do_exec(m)
            elif head == "SET":
                sets.append(rest)
                tokens[i] = ""
            elif head == "INIT":
                k, _, v = rest.partition("=")
                self.add(k, v)
                tokens[i] = ""
            elif head == "DYN":
                tokens[i] = ""
            elif head == "TTL":
                tokens[i] = ""
            elif head == "PAGE" and len(parts) > 1 and parts[1] in ("TITRE", "DESCRIPTION", "KEYWORDS"):
                self.add(m, self.config.get("cfg_" + parts[1].lower()))
            elif head in ("REP", "FORM", "SESSION", "COOKIE", "RND") and m not in self.dic:
                key = before_space(m)
                sub = before_space(parts[1]) if len(parts) > 1 else ""
                if head == "REP":
                    val = self.rep_value(sub)
                elif head == "FORM":
                    val = self.req.get(sub)
                elif head == "RND":
                    # $$RND:a:b$$ (random affiliate buttons): a deterministic pick per page path
                    lo, hi = clng(parts[1]) if len(parts) > 1 else 1, clng(before_space(parts[2])) if len(parts) > 2 else 1
                    val = lo + zlib.crc32(self.req.path.encode()) % max(hi - lo + 1, 1)
                    parts = parts[:2]
                else:
                    val = None
                if len(parts) == 3:
                    # `$$FORM:X:default$$`: id3x has no default value (Wayback: value=""), the macro
                    # name is looked up as it is and stays empty.
                    tokens[i] = m
                    filt[i] = ""
                else:
                    self.add(key, val)
                    tokens[i] = key
                    filt[i] = after_space(parts[1]) if len(parts) > 1 else ""
            elif head in ("!", "REM"):
                level += 1; stack[level] = i; kind[i] = 120
            elif head in ("IF", "_IF"):
                level += 1; stack[level] = i; kind[i] = 4; data[i] = rest
            elif head == "IF=":
                level += 1; stack[level] = i; kind[i] = 20; data[i] = rest
            elif head == "IF!":
                level += 1; stack[level] = i; kind[i] = 21; data[i] = rest
            elif head in ("IF>", "IF+"):
                level += 1; stack[level] = i; kind[i] = 22; data[i] = rest
            elif head in ("IF<", "IF-"):
                level += 1; stack[level] = i; kind[i] = 23; data[i] = rest
            elif head == "IFNULL":
                level += 1; stack[level] = i; kind[i] = 24; data[i] = rest
            elif head in ("_IF0", "IF0", "ELSE"):
                level += 1; stack[level] = i; kind[i] = 5; data[i] = rest
            elif head == "BIF0":
                level += 1; stack[level] = i; kind[i] = 3; data[i] = rest
            elif head == "BIF":
                level += 1; stack[level] = i; kind[i] = 2; loop_name = rest; data[i] = rest
            elif head in ("BCL", "DO"):
                level += 1; stack[level] = i; kind[i] = 10; data[i] = rest
                lp = rest.split(":")
                layout.setdefault(loop_name, (clng(lp[0]) if lp else 1, clng(lp[1]) if len(lp) > 1 else 9999))
                in_loop = True
            elif head == "LOOP":
                if in_loop:
                    jump[stack[level]] = i
                    jump[i] = stack[level]
                    level -= 1
                    kind[i] = 11
                    in_loop = False
            elif head == "BFIN":
                jump[stack[level]] = i
                jump[i] = stack[level]
                level -= 1
                kind[i] = 12
            elif head in ("END", "FIN", "ENDIF"):
                kind[i] = 99
                jump[stack[level]] = i
                level -= 1
            else:
                if in_loop and m[:len(loop_name)] == loop_name and loop_name:
                    sub = m[len(loop_name) + 1:]
                    data[i] = before_space(sub)
                    filt[i] = after_space(sub)
                else:
                    tokens[i] = before_space(m)
                    filt[i] = after_space(m)
            if level < 0:
                level = 0
        for spec in loops_todo:
            self.do_loop(spec, layout)
        for s in sets:
            k, _, expr = s.partition("=")
            pieces = expr.split("@@")
            val = []
            for j, p in enumerate(pieces):
                if j % 2 == 0:
                    val.append(p)
                else:
                    key, _, fargs = p.partition(" ")
                    if key in self.dic:
                        v = self.dic[key]
                        v = make_funcs(fargs, v, self.functions, self) if fargs else v
                        val.append(vstr(v))
            val = "".join(val)
            if val != "":
                self.dic[k] = val
        return self.display(tokens, kind, jump, data, filt)

    def rep_value(self, sub):
        rep = self.req.elements
        if sub in ("DOM", "SRV", "SVR"):
            return self.config["host"]
        if sub == "PAGE":
            return self.req.path[1:] + self.rquery()
        if sub == "REP":
            p = self.req.path[1:]
            k = p.rfind("/")
            return "/" + p[:k + 1] if k >= 0 else ""
        if sub == "NOM":
            return self.req.page_name
        if sub == "URI":
            return self.req.path[1:]
        if sub == "QRY":
            q = self.rquery()
            return q[1:] if q else ""
        k = clng(sub)
        j = len(rep) - 1 - k
        return rep[j] if 0 <= j < len(rep) else None    # id3x: REP:n only for existing elements

    # -- display (disp) ---------------------------------------------------------------------------
    def lookup_row(self, line, key):
        """Value of `key` (full macro name) in the current loop row, or a KeyError."""
        if self.cur_loop and line > 0:
            rows = self.loops[self.cur_loop].rows
            prefix = self.cur_loop + ":"
            if key.startswith(prefix) and line <= len(rows):
                col = key[len(prefix):]
                if col == "I":
                    return line
                row = rows[line - 1]
                if col in row:
                    return row[col]
        raise KeyError(key)

    def get_cond(self, key, line):
        if key in self.dic:
            return True, self.dic[key], True
        try:
            return True, self.lookup_row(line, key), False
        except KeyError:
            return False, None, False

    def display(self, tokens, kind, jump, data, filt):
        out = []
        line = 0
        self.cur_loop = ""
        nb_lines = 0
        stop = 0
        invert = False
        ncol = 1
        i = 0
        n = len(tokens)
        while i < n:
            m = tokens[i]
            if i % 2 == 0:
                out.append(m)
                i += 1
                continue
            k = kind[i]
            d = data[i]
            if k == 120:
                i = jump[i]
            elif k == 4:
                found, v, glob = self.get_cond(d, line)
                ok = (truthy(v) if glob else truthy0(v)) if found else False
                if not ok and jump[i]:
                    i = jump[i]
            elif k == 5:
                found, v, _ = self.get_cond(d, line)
                if found and truthy0(v) and jump[i]:
                    i = jump[i]
            elif k in (20, 21):
                pos = d.find(":")
                if pos > 0:
                    choices = d[:pos].split("$")
                    key = d[pos + 1:]
                    found, v, _ = self.get_cond(key, line)
                    ok = False
                    if found:
                        sv = vstr(v)
                        for c in choices:
                            if c == sv:
                                ok = True
                            else:
                                alt = c.replace("_", ":")
                                if alt in self.dic and vstr(self.dic[alt]).lower() == sv.lower():
                                    ok = True
                    if (k == 20 and not ok or k == 21 and ok) and jump[i]:
                        i = jump[i]
            elif k in (22, 23):
                pos = d.find(":")
                if pos > 0:
                    ref = d[:pos]
                    key = d[pos + 1:]
                    found, v, glob = self.get_cond(key, line)
                    ok = False
                    if found:
                        ok = cdbl(ref) > cdbl(v) if k == 22 else cdbl(ref) < cdbl(v)
                    if ok and jump[i]:
                        i = jump[i]
            elif k == 24:
                found, v, _ = self.get_cond(d, line)
                if found and v is not None and jump[i]:
                    i = jump[i]
            elif k == 3:
                if clng(self.dic.get("_btot_" + d, 0)) > 0 and jump[i]:
                    i = jump[i]
            elif k == 2:
                self.cur_loop = d
                nb_lines = clng(self.dic.get("_btot_" + d, 0))
                if nb_lines != 0:
                    lp = self.loops.get(d)
                    invert = bool(lp and lp.invert)
                    if invert:
                        line, stop = nb_lines, 0
                    else:
                        line, stop = 1, nb_lines + 1
                elif jump[i]:
                    i = jump[i]
            elif k == 10:
                sp = d.split(":")
                ncol = clng(sp[0]) if sp else 1
                if ncol > 1:
                    pad = sp[2] if len(sp) > 2 else None
                    if pad is not None:
                        out.append(f'<TABLE width="100%" border="0" cellpadding="{pad}" cellspacing="{pad}"><TR><TD valign="top">')
                    else:
                        out.append('<TABLE width="100%" border="0"><TR><TD valign="top">')
                    self.by_col = -(-nb_lines // ncol)
            elif k == 11:
                if invert:
                    line -= 1
                    done = line <= stop
                else:
                    line += 1
                    done = line >= stop
                if done:
                    line = 0
                    if ncol > 1:
                        out.append("</TD></TR></TABLE>")
                else:
                    if ncol > 1 and (line - 1) % self.by_col == 0:
                        out.append('</TD><TD valign="top">')
                    i = jump[i]
            elif k in (12, 99):
                pass
            elif m != "" or d != "":
                if d != "":
                    try:
                        v = self.lookup_row(line, self.cur_loop + ":" + d)
                        found = True
                    except KeyError:
                        key = self.cur_loop + ":" + d
                        found = key in self.dic
                        v = self.dic.get(key)
                    if found:
                        if filt[i]:
                            v = make_funcs(filt[i], v, self.functions, self)
                        if v is not None:
                            out.append(vstr(v))
                elif m in self.dic:
                    v = self.dic[m]
                    if filt[i]:
                        v = make_funcs(filt[i], v, self.functions, self)
                    if v is not None:
                        out.append(vstr(v))
            i += 1
        return move_pictures("".join(out), self.files.skin)


SRC_PROTECT = re.compile(r"\b(src|background)=([\"']?)(/?z/|/?zi/|http://|\$\$)(.*\.[^\"'>]*)([\"']?)\b", re.I)
SRC_MOVE = re.compile(r"\b(src|background)=([\"']?)/?([^>\r\n]+\.(?:gif|jpg|jpeg|png))([\"']?)\b", re.I)


def move_pictures(buf, rep):
    """disp(): relative `src=` / `background=` images moved to /z/<rep>/ (once each)."""
    if "src=" in buf.lower() or "background=" in buf.lower():
        while SRC_PROTECT.search(buf):
            buf = SRC_PROTECT.sub(r"@\1@=\2\3\4\5", buf)
        while SRC_MOVE.search(buf):
            buf = SRC_MOVE.sub(lambda m: f"@{m.group(1)}@={m.group(2)}/z/{rep}/{m.group(3)}{m.group(4)}", buf)
    return re.sub(r"@(src|background)@", lambda m: m.group(1).lower(), buf, flags=re.I)
