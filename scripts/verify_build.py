#!/usr/bin/env python3
"""Verify the statified build of Orange Juice v3 (stdlib only, read-only).

    python3 -I scripts/verify_build.py [--build site] [--hosting hosting] [--raw ~/Code/id3x/raw]

Hard checks (exit 1 on failure):
  * the build looks complete (key pages present in every skin, enough people sheets)
  * no server-side, engine or executable file, nothing matching scripts/exclude-globs.txt
  * no template leftovers (`$$MACRO$$`, `<!--#include`, `<%`) and no `<form` at all
  * every html page declares iso-8859-1 and decodes as such
  * every internal link (href/src/background) resolves to a file of the build, keeps no query
    string except the archive's own search page, and stays within its skin prefix
  * no link to http(s)://(www.)ojuice.net, no `mailto:`, no e-mail address (`local@????` passes)
  * people: no published birth year next to a day/month, no e-mail domain, no department, no
    password; ID photos (/zi/nicks/) only for ids that have a people sheet in the build
    (private values are read at run time from the SQLite database, never stored)
  * no IPv4 address outside the host part of a URL
  * /js/oj.js and /idx/*.json exist
.DS_Store files (recreated by macOS) are ignored. Without the raw store (CI), the database
checks are skipped with a notice.
"""
import argparse
import fnmatch
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

ENC = "iso-8859-1"
REPO = Path(__file__).resolve().parent.parent
HTML_EXT = (".htm", ".html")
FORBIDDEN_EXT = (".asp", ".aspx", ".asa", ".asax", ".asmx", ".inc", ".cs", ".config", ".dll", ".pdb", ".bak",
                 ".exe", ".com", ".scr", ".bat", ".cgi", ".pl", ".php", ".sqlite", ".mdf", ".ldf", ".rar")
LINK_RE = re.compile(r"""(?is)\b(?:href|src|background|lowsrc)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
SKIP_SCHEME = re.compile(r"(?i)^(?:[a-z][a-z0-9+.-]*:|//|#)")
LEFTOVERS = [
    ("asp open tag", re.compile(r"<%")),
    ("ssi include", re.compile(r"(?i)<!--\s*#(?:include|exec|echo|config)")),
    ("$$ macro", re.compile(r"\$\$(?:[A-Z0-9]+:[A-Z]|-\$\$|_\$\$|[A-Z]+\$\$)")),
    ("form tag", re.compile(r"(?i)<form\b")),
    ("mailto:", re.compile(r"(?i)mailto:")),
]
# The removal-request footer (statify.py) carries the only mailto: of the site: leave it out of the scans.
REMOVAL_NOTE_RE = re.compile(r'(?s)<div id="removal-note".*?</div>')
EMAIL_RE = re.compile(r"[A-Za-z0-9._+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
OWN_HOST_RE = re.compile(r"(?i)https?://(?:www\.)?ojuice\.(?:net|org)(?![\w.-])")
IPV4 = re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])")
KEY_PAGES = ("index.htm", "index.html", "archived.htm", "skins.htm", "oj-search.htm", "145/nick.htm",
             "_a/nicklist.htm", "groups/a/links.htm", "next/parties.htm", "past/parties.htm", "faq.htm",
             "misc.htm", "birthdates.htm", "index-p2.htm")
SEARCH_PAGE = "oj-search.htm"


def html_escape(s):
    return "".join(f"&#{ord(c)};" if 160 <= ord(c) <= 255 else {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}.get(c, c) for c in s)


def all_files(root):
    out = []
    for r, ds, fs in os.walk(root):
        ds.sort()
        for f in sorted(fs):
            if f != ".DS_Store":
                out.append(Path(r, f).relative_to(root).as_posix())
    return out


def load_globs(path):
    if not path.is_file():
        return []
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


def glob_match(rel, pat):
    if "/" not in pat.rstrip("/"):
        if pat.endswith("/"):
            return ("/" + pat) in ("/" + rel) or rel.startswith(pat)
        return fnmatch.fnmatchcase(rel.rsplit("/", 1)[-1], pat)
    rx = re.escape(pat.rstrip("/")).replace(r"\*", ".*")
    return re.match(rx + ("(/|$)" if pat.endswith("/") else "$"), rel) is not None or re.search("(^|/)" + rx + "(/|$)", rel) is not None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=str(REPO / "site"))
    ap.add_argument("--hosting", default=str(REPO / "hosting"))
    ap.add_argument("--raw", default=os.environ.get("ID3X_RAW", "~/Code/id3x/raw"))
    a = ap.parse_args(argv)
    build = Path(a.build)
    raw = Path(a.raw).expanduser()
    errors = defaultdict(list)

    def err(kind, msg):
        errors[kind].append(msg)

    files = all_files(build)
    fileset = set(files)
    skins = sorted({f.split("/")[1] for f in files if f.startswith("skin/") and f.count("/") >= 2})
    for k in KEY_PAGES:
        if k not in fileset:
            err("missing key page", k)
    for s in skins:
        if f"skin/{s}/index.htm" not in fileset:
            err("missing key page", f"skin/{s}/index.htm")
    nicks = {int(m.group(1)) for f in files for m in [re.fullmatch(r"(\d+)/nick\.htm", f)] if m}
    if len(nicks) < 20000:
        err("incomplete build", f"only {len(nicks)} people sheets at the root")
    for f in ("js/oj.js", "idx/news.json", "idx/people.json"):
        if f not in fileset:
            err("missing key file", f)

    globs = load_globs(REPO / "scripts" / "exclude-globs.txt")
    for root in (build, Path(a.hosting)):
        if not root.exists():
            continue
        for f in all_files(root):
            if f.lower().endswith(FORBIDDEN_EXT):
                err("forbidden file", f)
            for g in globs:
                if glob_match(f, g):
                    err("excluded file", f"{f} ({g})")

    # private values, read at run time only
    db = raw / "db" / "OJUICE.sqlite"
    years, emails, deps, photos = {}, {}, {}, {}
    if db.is_file():
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        for pid, by, md, em, dep, ext in con.execute(
                "SELECT id, birthyear, birthmd, email, department, pic_ext FROM people"):
            if by and md and len(md) == 4:
                years[pid] = (str(by), md[2:], md[:2])
            if em and "@" in em:
                emails[pid] = em.strip().lower()
            if dep and len(dep.strip()) > 3:
                deps[pid] = dep.strip()
    else:
        print(f"notice: {db} not found, private-data checks skipped")

    nbpages = 0
    for f in files:
        p = build / f
        if f.lower().endswith(".zip"):
            import zipfile
            try:
                names = zipfile.ZipFile(p).namelist()
            except (zipfile.BadZipFile, OSError):
                names = []
                err("zip", f"{f}: unreadable")
            for n in names:
                if n.lower().endswith((".exe", ".com", ".dll", ".scr", ".bat", ".pif", ".vbs", ".cmd", ".msi")):
                    err("executable in zip", f"{f}: {n}")
        if not f.endswith(HTML_EXT):
            if f.startswith("zi/nicks/"):
                m = re.match(r"zi/nicks/(\d+)\.", f)
                if not m or int(m.group(1)) not in nicks:
                    err("private photo", f)
            continue
        nbpages += 1
        try:
            text = REMOVAL_NOTE_RE.sub("", p.read_bytes().decode(ENC))
        except UnicodeDecodeError:
            err("charset", f)
            continue
        if not re.search(r"(?i)charset\s*=\s*iso-8859-1", text):
            err("charset", f"{f}: no iso-8859-1 declaration")
        for kind, rx in LEFTOVERS:
            m = rx.search(text)
            if m:
                err(kind, f"{f}: {text[max(0, m.start() - 40):m.end() + 40]!r}")
        for tm in re.finditer(r"(?is)<script\b.*?</script\s*>|<[a-zA-Z][^<>]*>", text):
            if OWN_HOST_RE.search(tm.group(0)):
                err("own host link", f"{f}: {tm.group(0)[:100]}")
                break
        for m in EMAIL_RE.finditer(text):
            err("e-mail", f"{f}: {m.group(0)}")
        for m in IPV4.finditer(text):
            before = text[max(0, m.start() - 12):m.start()].lower()
            if not before.endswith(("://", "@")):
                err("ipv4", f"{f}: {m.group(0)}")
        prefix = "/" + "/".join(f.split("/")[:2]) if f.startswith("skin/") else ""
        base = "/" + f
        for lm in LINK_RE.finditer(re.sub(r"(?is)<script\b.*?</script\s*>", "", text)):
            val = next(g for g in lm.groups() if g is not None).strip()
            if not val or SKIP_SCHEME.match(val) or val.startswith("javascript"):
                continue
            sp = urlsplit(urljoin("http://x" + base, val.replace("&amp;", "&")))
            path = unquote(sp.path)
            rel = path.lstrip("/")
            if sp.query and not rel.endswith(SEARCH_PAGE) and not (rel == "js/oj.js" and re.fullmatch(r"v=[0-9a-f]+", sp.query)) \
                    and not (rel == "index.htm" and sp.query == "skin=o"):
                err("query link", f"{f}: {val}")
            if rel.endswith("/") or rel == "":
                rel += "index.html"
            if rel not in fileset:
                err("broken link", f"{f}: {val}")
            elif rel.endswith(HTML_EXT) and prefix and not path.startswith(prefix + "/") and not path.startswith("/skin/") \
                    and not (path == "/index.htm" and sp.query == "skin=o"):     # "full version" link
                err("skin prefix", f"{f}: {val}")
        m = re.fullmatch(r"(?:skin/[^/]+/)?(\d+)/nick\.htm", f)
        if m:
            pid = int(m.group(1))
            if pid in years:
                y, d, mo = years[pid]
                if re.search(rf"{d}/{mo}/{y}\b", text):
                    err("birth year", f)
            if pid in emails and emails[pid] in text.lower():
                err("e-mail domain", f)
            cl = re.search(r"Country :.*?<BR>", text, re.S)
            if pid in deps and cl and "[" + html_escape(deps[pid]) + "]" in cl.group(0):
                err("department", f)
            if re.search(r"(?i)password", text) and re.search(r"(?i)password\s*:", text):
                err("password", f)

    if errors:
        for kind in sorted(errors):
            items = errors[kind]
            print(f"FAIL {kind}: {len(items)}")
            for x in items[:10]:
                print("   ", x)
        sys.exit(1)
    print(f"OK: {nbpages} pages, {len(files)} files, {len(nicks)} people sheets, skins: {', '.join(skins) or '-'}")


if __name__ == "__main__":
    main()
