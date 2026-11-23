#!/usr/bin/env python3
"""Statify Orange Juice v3 (www.ojuice.net, 2000-11-20 to 2006-09) into plain static HTML.

    python3 -I scripts/statify.py [--skins all|om,light] [--out site] [--raw PATH]

Reads ONLY the shared raw store of the id3/id3x sites (`RAW`, default ~/Code/id3x/raw, never
written): the skins `id3/z/<skin>/`, the user media `id3/zi/` and the SQLite extraction of the
OJUICE database `db/OJUICE.sqlite`. The output folder is emptied and rebuilt on every run; the
output is deterministic (sorted traversal, stable SQL ordering, no timestamps).

How it works (docs/data-model.md):
  * `id3render/engine.py` reimplements the id3 template engine ($$MACRO$$ language, includes,
    loops, filters, image relocation) and `id3render/oj.py` the data macros of the site over
    SQLite (SQL of id3x/macros/oj/*.cs), with the server clock frozen at NOW;
  * the site is crawled like a visitor would: from the home page, every internal link of every
    rendered page is resolved to (template, path, query) as IIS + id3x did, rendered, and its
    links followed. Pages whose URL carried a query string get a static name
    (`/index.htm?idcat=3&OJ_NEWS_QRYpg=2` -> `/index-cat3-p2.htm`), listed in
    build-meta/url-map.json for the Edge Script;
  * forms, logins, counters, banners and third-party scripts are removed (an "archived" notice
    or plain text replaces them), searches become client-side (/js/oj.js + /idx/*.json);
  * personal data is redacted at the source (oj.py `redact`) and again in the output (e-mails).

Skins: `o` (the default) at the site root, the others under /skin/<name>/ (SKINS below).
Python 3.9+, standard library only. Pages are written in iso-8859-1 like the original.
"""
import argparse
import hashlib
import html
import json
import os
import re
import shutil
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent / "id3render"))
import engine  # noqa: E402
import oj      # noqa: E402

# --- settings ---------------------------------------------------------------------------------
RAW = Path(os.environ.get("ID3X_RAW", "~/Code/id3x/raw")).expanduser()   # the one raw-path setting
REPO = Path(__file__).resolve().parent.parent
ENC = "iso-8859-1"
NOW = datetime(2006, 9, 7, 23, 59, 59)     # server clock: last day of the OJUICE database (2006-09-07)
HOST = "www.ojuice.net"
OWN_HOSTS = ("www.ojuice.net", "ojuice.net", "ojuice.org", "www.ojuice.org")
DEFAULT_SKIN = "o"
SKINS = ["o", "light"]      # rendered skins (o at the root); see docs/plan.md for the others
# Skin configs of id3 (parametres.inc): `ob` (oj_blue) = its own colours and pictures over the
# templates of `o` (cfg_custom_rep = o); `light`, `v2`, `om`, `o.bak` have no fallback folder.
FALLBACK = {"ob": "o"}
MASK = "????"
ARCHIVED = "/archived.htm"
SEARCH_PAGE = "/oj-search.htm"     # client-side search results (z/o/search.htm is the FTP search page)
META_CHARSET = '<META http-equiv="Content-Type" content="text/html; charset=iso-8859-1">'

# Templates of write paths, logins, admin, counters and dead services: never rendered; links to
# them go to /archived.htm.
REMOVED = {
    "adm_badurls.htm", "adm_banners.htm", "adm_banners_stats.htm", "adm_forum.htm", "adm_forums.htm",
    "adm_ftpsearch.htm", "adm_ftpsearch_del.htm", "adm_ftpsearch_edit.htm", "adm_ftpsearch_empty.htm",
    "adm_index.htm", "adm_links.htm", "adm_linksmodify.htm", "adm_news.htm", "adm_nicks.htm", "adm_oneliner.htm",
    "adm_parties.htm", "adm_partiesmodify.htm", "adm_piclib.htm", "adm_webcams.htm",
    "banner_stats.htm", "banners.htm", "charts_send.htm", "charts_sendok.htm", "form_contact.htm",
    "form_contact_ok.htm", "golink.htm", "grp_remove.htm", "link_edit.htm", "link_editok.htm", "logged_popup.htm",
    "login.htm", "login2.htm", "login2_pass.htm", "login_pass.htm", "loginlist.htm", "logout.htm", "mailing.htm",
    "message_write.htm", "messages_popup.htm", "necloginlist.htm", "news_add.htm", "news_addok.htm",
    "news_preview.htm", "news_preview_article.htm", "nick_edit.htm", "nick_editok.htm", "party_edit.htm",
    "party_editok.htm", "picture_edit.htm", "pictures.htm", "webcam_add.htm", "webcam_addok.htm",
    "ftpsearch.htm", "metasearch.htm", "tablogged.js", "banner170.js", "banner420.js",
}
# Query parameters that select content (kept, in this order, in the static name); paging
# parameters (<LOOP>pg) are kept too. Every other parameter is dropped.
QUERY_KEYS = {"idcat": "cat", "idgroup": "group"}
# Parameters of the original searches: the link goes to the client-side search page
SEARCH_KEYS = {"sn": "news", "snick": "people", "sl": "groups", "sp": "parties", "kw": "pictures",
               "event": "pictures", "author": "pictures", "who": "pictures", "sftp": None, "q": None}
DEAD_HOSTS = ("mirabilis.com", "icq.com", "cnxweb.com", "extreme-dm.com", "audiorealm.com", "hitbox.com")


# --- archive pages (rendered in the skin's frame like any template) ---------------------------------
FRAME = """<!--#include virtual="incl_head.inc" -->
<!--#include virtual="incl_top.inc" -->
$$SET:PAGE:TITLE=Orange Juice - %(title)s$$
<TD valign=top width=165 background=p/tabback.gif>
<!--#include virtual="incl_tabnews.inc" -->
<!--#include virtual="incl_tabmisc.inc" -->
<!--#include virtual="incl_tabgbottom.inc" -->
</TD>
$$E:ID3:TXT2GIF:p/1_@.gif:6:5:%(gif)s:title$$
<TD width="$$PAGE:MWIDTH$$" rowspan=2 valign=top align=center>
<!--#include virtual="incl_titlestart.inc" -->$$ID3:TXT2GIF:title$$<!--#include virtual="incl_titleend.inc" -->
<!--#include virtual="incl_framestart.inc" -->
%(body)s
<!--#include virtual="incl_frameend.inc" -->
</TD>
<TD valign=top width="188" background=p/tabdm.gif>
<!--#include virtual="incl_tabsearch.inc" -->
<!--#include virtual="incl_tabdbottom.inc" -->
</TD>
<!--#include virtual="incl_leftrightbottom.inc" -->
<!--#include virtual="incl_bottom.inc" -->
<!--#include virtual="incl_foot.inc" -->
"""
ARCHIVE_NOTE = ("This is a static, read-only archive of Orange Juice v3 (www.ojuice.net, 2000-2006). "
                "Logins, forms, votes, the oneliner, counters and the FTP search are no longer available.")
# The v3 site was in English only (unlike the 2000 edition): the archive notices are in English.


def virtual_pages(skins):
    radios = "".join(
        f'<INPUT type="radio" name="skin" value="{"" if s == DEFAULT_SKIN else s}"> '
        f'<A href="{"" if s == DEFAULT_SKIN else "/skin/" + s}/index.htm">{s}</A> - {SKIN_INFO.get(s, "")}<BR>'
        for s in skins)
    search_types = "".join(f'<OPTION value="{v}">{v}</OPTION>' for v in ("news", "people", "groups", "parties", "pictures"))
    pages = {
        "archived.htm": ("Archive", "&gt; archive", f'{ARCHIVE_NOTE}<BR><BR><A href="/index.htm">Back to the news</A>'),
        "skins.htm": ("Skins", "&gt; skins", '<DIV id="oj-skins">Choose how the archive looks (kept in this browser, '
                      f'no cookie):<BR><BR>{radios}<BR><INPUT type="button" id="oj-skin-go" value="USE THIS SKIN"></DIV>'),
        "oj-search.htm": ("Search", "&gt; search",
                          f'<SPAN class="oj-search" id="oj-box" data-t="news"><SELECT class="oj-type">{search_types}</SELECT> '
                          '<INPUT type="TEXT" size=30 maxlength=100 value=""> <INPUT type="BUTTON" value="go"></SPAN>'
                          '<NOSCRIPT><BR>The search of the archive needs JavaScript.</NOSCRIPT><DIV id="oj-results"></DIV>'),
        "404.htm": ("Not found", "&gt; page not found",
                    "This page is not in the archive.<BR><BR>"
                    f'{ARCHIVE_NOTE}<BR><BR><A href="/index.htm">Back to the news</A>'),
    }
    return {k: FRAME % {"title": t, "gif": g, "body": b} for k, (t, g, b) in pages.items()}


SKIN_INFO = {"o": "the final design (2004-2006), default", "om": "an earlier state of the same design (2002-2005)",
             "o.bak": "an older backup of the design (forums era)", "light": "the light version (light.ojuice.net)",
             "ob": "the blue version (2000-2001)", "v2": "the v2 design (home page only)"}


def page_key(path, query):
    """Canonical (path, query items) of an internal page request."""
    return path, tuple(sorted(query.items()))


def static_name(path, query):
    """Static file path of a page request: query parameters folded into the file name."""
    if not query:
        return path
    stem, dot, ext = path.rpartition(".")
    parts = []
    for k in QUERY_KEYS:
        if k in query:
            parts.append(QUERY_KEYS[k] + query[k])
    for k, v in sorted(query.items()):
        if k.endswith("pg"):
            parts.append("p" + v)
    return f"{stem}-{'-'.join(parts)}.{ext}"


class Site:
    def __init__(self, args, skin, prefix, macros, skins):
        self.args, self.skin, self.prefix, self.macros, self.skins = args, skin, prefix, macros, skins
        fallback = FALLBACK.get(skin)
        self.files = engine.SkinFiles(RAW / "id3" / "z", skin, fallback,
                                      virtual_pages(skins) if fallback or skin == DEFAULT_SKIN else None)
        if not (fallback or skin == DEFAULT_SKIN):
            # v2 / scene.org: the archive pages use the default skin's frame
            self.files.virtual = {}
            self.vfiles = engine.SkinFiles(RAW / "id3" / "z", DEFAULT_SKIN, None, virtual_pages(skins))
        self.pages = {}            # static path -> html
        self.seen = {}             # page key -> static path (or None: not a page)
        self.queue = deque()
        self.redirects = {}        # static path of a page that redirected -> target
        self.assets = set()        # /z/... and /zi/... paths referenced
        self.unlinked = []
        self.dropped = []
        self.url_map = {}          # original URL with query -> static path

    # -- requests -----------------------------------------------------------------------------
    def resolve(self, url, base):
        """(new_url, why) for a link found in a page at original path `base` (memoized)."""
        key = (url, base if not url.startswith("/") or url.startswith("//") else "")
        memo = self.__dict__.setdefault("_memo", {})
        r = memo.get(key)
        if r is None:
            r = self._resolve(url, base)
            if r[1] in ("ok", "external", "search") and not (r[0] or "").startswith("/archived") or r[0] is None:
                memo[key] = r
        return r

    def _resolve(self, url, base):
        url = html.unescape(url.strip())
        if not url or url.startswith("#"):
            return url, "ok"
        low = url.lower()
        if low.startswith(("javascript:", "mailto:", "news:", "ftp:", "irc:", "telnet:")):
            return url, "external"
        if low.startswith("//"):
            url = "http:" + url
        try:
            sp = urlsplit(urljoin("http://%s%s" % (HOST, base), url))
            sp.hostname
        except ValueError:
            return None, "malformed URL"
        host = (sp.hostname or "").lower()
        if host not in OWN_HOSTS:
            if host == "light.ojuice.net" and "light" in self.skins:
                return "/skin/light/", "ok"
            if any(host == h or host.endswith("." + h) for h in DEAD_HOSTS):
                return None, "dead third party"
            return url, "external"
        path = re.sub(r"/{2,}", "/", unquote(sp.path) or "/")
        if self.prefix and re.match(r"(?i)^\s*https?://", url) and path in ("/", "/index.htm", "/index.html"):
            # "click here for the full version" (light): the default skin's home, and the skin
            # chosen on /skins.htm is forgotten (/js/oj.js reads ?skin=o)
            return "/index.htm?skin=" + DEFAULT_SKIN, "ok"
        if re.match(r"^/skin/[^/]+/", path):      # a link to another skin of the archive (skins page)
            return path, "ok"
        query = {k.lower(): v for k, v in parse_qsl(sp.query, keep_blank_values=True)}
        frag = "#" + sp.fragment if sp.fragment else ""
        if path.startswith(("/z/", "/zi/")) or re.search(r"\.(gif|jpe?g|png|css|js|swf|ico|zip|rar|txt|mid|mod|xm|mp3|avi|bmp)$", path, re.I):
            return self.asset(path), "ok" if self.asset(path) else "missing asset"
        return self.page(path, query, frag)

    def asset(self, path):
        memo = self.__dict__.setdefault("_amemo", {})
        if path not in memo:
            memo[path] = self._asset(path)
        if memo[path]:
            self.assets.add(memo[path])
        return memo[path]

    def real_case(self, rel):
        """The on-disk spelling of a raw path (IIS was case-insensitive, bunny.net is not), or None."""
        cache = self.__dict__.setdefault("_dirs", {})
        cur, parts = RAW / "id3", []
        for seg in rel.strip("/").split("/"):
            if cur not in cache:
                cache[cur] = {n.lower(): n for n in sorted(os.listdir(cur))} if cur.is_dir() else {}
            name = cache[cur].get(seg.lower())
            if name is None:
                return None
            parts.append(name)
            cur = cur / name
        return "/" + "/".join(parts) if cur.is_file() else None

    def _asset(self, path):
        """Published path of a skin or media file referenced by a page, in its on-disk spelling."""
        if path.lower().endswith((".rar", ".exe", ".pls")):
            return None             # archives that cannot be checked for executables, playlists
        if path.startswith(("/z/", "/zi/")):
            cands = [path]
            m = re.match(r"^/z/[^/]+/(.*)$", path)
            if m:                   # a picture missing from the skin: the default skin's copy
                cands.append(f"/z/{DEFAULT_SKIN}/{m.group(1)}")
        else:                       # a root-relative file: id3 served it from the skin folder
            cands = [f"/z/{self.skin}/{path.lstrip('/')}"]
        for c in cands:
            real = self.real_case(c)
            if real:
                return real
        return None

    def golink(self, path, query):
        """golink.htm?id=&url= (click counter + redirect): the link goes straight to its target."""
        url = query.get("url")
        if not url:
            table = path.strip("/").split("/")[0].lower()
            if table in ("news", "links", "parties", "pictures", "wallpapers") and re.fullmatch(r"\d+", query.get("id", "")):
                url = self.macros.db.scalar(f"SELECT url FROM {table} WHERE id = ?", (int(query["id"]),))
        if not url:
            return None, "redirector without target"
        url = url.strip()
        if re.match(r"(?i)^(mailto:)?[\w.+-]+@[\w.-]+$", url):
            return None, "e-mail target"
        if re.match(r"(?i)^www\.", url):
            url = "http://" + url
        return self.resolve(url, "/")

    def page(self, path, query, frag=""):
        if path.endswith("/"):
            path += "index.htm"
        path = path.lower()
        name = path.rsplit("/", 1)[-1]
        if name in ("index.html", "default.htm", "default.asp"):
            path, name = path[: -len(name)] + "index.htm", "index.htm"
        if not name.endswith((".htm", ".html")):
            return None, "not a page"
        for k, kind in SEARCH_KEYS.items():
            if query.get(k):
                return self.prefix + (SEARCH_PAGE + f"?t={kind}" if kind else ARCHIVED), "search"
        if name == "golink.htm":
            return self.golink(path, query)
        if name in REMOVED:
            return self.prefix + ARCHIVED, "ok"
        if self.files.find(name) is None and not (hasattr(self, "vfiles") and self.vfiles.find(name) == name):
            return None, "no template"
        q = {}
        for k, v in query.items():
            if k in QUERY_KEYS and v.strip() and re.fullmatch(r"-?\d+", v.strip()):
                q[k] = v.strip()
            elif k.endswith("pg") and re.fullmatch(r"\d+", v.strip() or "x") and int(v) > 1:
                q[k.lower()] = str(int(v))
        key = page_key(path, q)
        if key not in self.seen:
            self.seen[key] = static_name(path, q)
            self.queue.append((path, q))
        target = self.seen[key]
        if target is None:
            return None, "not rendered"
        target = self.redirects.get(target, target)
        return self.prefix + target + frag, "ok"

    # -- crawl ---------------------------------------------------------------------------------
    def crawl(self, seeds):
        for s in seeds:
            self.page(s, {})
        while self.queue:
            path, q = self.queue.popleft()
            if len(self.pages) % 2000 == 1999:
                print(f"  {self.skin}: {len(self.pages) + 1} pages, {len(self.queue)} queued", flush=True)
            out = static_name(path, q)
            req = engine.Request(path, q)
            files = self.vfiles if hasattr(self, "vfiles") and self.vfiles.virtual.get(req.page_name) else self.files
            pg = engine.Page(files, self.macros, req, {"host": HOST, "now": NOW})
            text = pg.render(req.page_name)
            if text is None:
                self.seen[page_key(path, q)] = None
                continue
            if pg.out_of_range and pg.out_of_range.lower() in q:
                canon = {k: v for k, v in q.items() if k != pg.out_of_range.lower()}
                tgt = self.page(path, canon)[0]
                self.redirects[out] = tgt[len(self.prefix):] if tgt else "/index.htm"
                continue
            used = {k for k in q if k in req.used}
            if used != set(q):   # a parameter the page does not read: same page as without it
                canon = {k: v for k, v in q.items() if k in used}
                tgt = self.page(path, canon)[0]
                self.redirects[out] = tgt[len(self.prefix):] if tgt else "/"
                continue
            if pg.redirect:
                tgt, _ = self.page(pg.redirect, {})
                self.redirects[out] = (tgt or "/index.htm")[len(self.prefix):]
                continue
            self.pages[out] = (path, q, text)
            for am in self.ATTR_RE.finditer(remove_dead_scripts(text)):
                self.resolve(next(g for g in am.groups()[1:] if g is not None), path)
        # links are rewritten once every page is known (redirect targets resolved)
        self._memo = {}
        done = {}
        for out in sorted(self.pages):
            path, q, text = self.pages[out]
            done[out] = self.finalize(text, path, out)
            if q:
                self.url_map[path + "?" + "&".join(f"{k}={v}" for k, v in sorted(q.items()))] = out
        if self.queue:
            raise RuntimeError(f"pages found only while rewriting links: {list(self.queue)[:5]}")
        self.pages = done

    # -- output rewriting --------------------------------------------------------------------------
    ATTR_RE = re.compile(r"""(?is)\b(href|src|background|action|lowsrc)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
    TAG_RE = re.compile(r"(?s)<([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>")

    def finalize(self, text, base, out):
        text = icq_badges(remove_dead_scripts(text), self.skin, base.endswith("/nick.htm"))
        text = neutralize_forms(text, self, out)
        text = redact_emails(text)
        text = self.rewrite_links(text, base, out)
        text = self.sweep_own_host(text, base)
        text = redact_emails(text)
        text = re.sub(r"/\*setTimeout\(.*?\*/", "", text)     # the disabled auto-refresh of every page
        text = mask_ips(text)
        text = re.sub(r"(?i)(charset\s*=\s*)(?:windows-1252|cp1252|us-ascii|(?=[\"'>\s]))", r"\1iso-8859-1", text)
        if "/js/oj.js" not in text:
            tag = '<SCRIPT src="/js/oj.js"></SCRIPT>\r\n'
            i = text.lower().rfind("</body>")
            text = text[:i] + tag + text[i:] if i >= 0 else text + "\r\n" + tag
        if not re.search(r"(?i)charset\s*=", text):
            m = re.search(r"(?i)<head[^>]*>", text) or re.search(r"(?i)<html[^>]*>", text)
            text = text[:m.end()] + "\r\n" + META_CHARSET + text[m.end():] if m else META_CHARSET + "\r\n" + text
        return text

    OWN_URL_RE = re.compile(r"(?i)https?://(?:www\.)?ojuice\.(?:net|org)(?:/[^\s\"'<>)]*)?")

    def sweep_own_host(self, text, base):
        """http://www.ojuice.net/... left in tags, handlers and scripts (status bar texts,
        window.open...) -> archive path. Plain text mentions of the address stay as they are."""
        def fn(m):
            new, why = self.resolve(m.group(0), base)
            return new if new and why != "external" else "/index.htm"

        def inside(m):
            return self.OWN_URL_RE.sub(fn, m.group(0))
        text = re.sub(r"(?is)<script\b.*?</script\s*>", inside, text)
        return re.sub(r"(?s)<[a-zA-Z][^<>]*>", inside, text)

    def rewrite_links(self, text, base, out):
        def anchor(m):
            open_tag, label = m.group(1), m.group(2)
            hm = re.search(r"""(?is)\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", open_tag)
            if not hm:
                return m.group(0)
            val = next(g for g in hm.groups() if g is not None)
            new, why = self.resolve(val, base)
            if new is None:
                self.unlinked.append((out, val, why))
                return label
            return m.group(0)
        text = outside_scripts(text, lambda t: re.sub(r"(?is)(<a\b[^>]*>)(.*?)</a\s*>", anchor, t))

        def tag(m):
            name, attrs = m.group(1).lower(), m.group(2)
            drop = []

            def one(am):
                val = next(g for g in am.groups()[1:] if g is not None)
                new, why = self.resolve(val, base)
                if why == "external":
                    if am.group(1).lower() in ("src", "background", "lowsrc") and re.match(r"(?i)^\s*(https?:)?//", val):
                        drop.append(val)
                    if new != html.unescape(val.strip()):     # a redirector resolved to its target
                        return '%s="%s"' % (am.group(1), html.escape(new, quote=True).replace("&#x27;", "'"))
                    return am.group(0)
                if new is None:
                    self.unlinked.append((out, val, why))
                    drop.append(val)
                    return ""
                return '%s="%s"' % (am.group(1), html.escape(new, quote=True).replace("&#x27;", "'"))
            new_attrs = self.ATTR_RE.sub(one, attrs)
            if drop and name in ("img", "embed", "script", "input", "iframe", "frame", "bgsound"):
                self.dropped.append((out, "%s %s" % (name, drop[0][:80])))
                return "" if name != "script" else "<script>"
            if drop:
                new_attrs = re.sub(r"""(?is)\b(src|background|lowsrc)\s*=\s*(?:"(?:https?:)?//[^"]*"|'(?:https?:)?//[^']*'|(?:https?:)?//[^\s"'>]+)""",
                                   "", new_attrs)
            return "<%s%s>" % (m.group(1), new_attrs)
        text = outside_scripts(text, lambda t: self.TAG_RE.sub(tag, t))
        text = re.sub(r"(?is)<script>\s*</script\s*>", "", text)

        def lit(m):     # image file names in scripts and event handlers (rollovers)
            val = m.group(2)
            if re.match(r"(?i)^(https?:)?//", val) and not re.match(r"(?i)^https?://(www\.)?ojuice\.net/", val):
                return m.group(0)
            new, why = self.resolve(val, base)
            return m.group(1) + new + m.group(1) if new and why == "ok" else m.group(0)
        return re.sub(r"""(["'])([^"'<>\s()]+\.(?:gif|jpe?g|png|swf))\1""", lit, text, flags=re.I)


# --- page clean-up --------------------------------------------------------------------------------
def outside_scripts(text, fn):
    parts = re.split(r"(?is)(<script\b.*?</script\s*>)", text)
    return "".join(p if i % 2 else fn(p) for i, p in enumerate(parts))


ICQ_RE = re.compile(r"(?is)<A href=\"?http://wwp\.mirabilis\.com/scripts/Search\.dll\?to=(\d+)\"?>(.*?)</A>")


def icq_badges(text, skin, sheet):
    """The live ICQ status flower (online.mirabilis.com, gone) becomes the skin's own neutral
    flower p/icq3.gif (its commented-out fallback), the number as tooltip; on a people sheet the
    number is also written out."""
    def fn(m):
        img = re.search(r"(?i)<IMG[^>]*\bwidth=(\d+)[^>]*\bheight=(\d+)", m.group(2))
        w, h = img.groups() if img else ("13", "13")
        badge = (f'<IMG src="/z/{skin}/p/icq3.gif" width={w} height={h} align=top border=0 '
                 f'alt="ICQ #{m.group(1)}" title="ICQ #{m.group(1)}">')
        return f"{badge} <FONT class=aslink>{m.group(1)}</FONT>" if sheet else badge
    return ICQ_RE.sub(fn, text)


def remove_dead_scripts(text):
    """Banner scripts, the logged-in box (tablogged.js), stats beacons and other dynamic scripts.
    The ICQ status badge (people sheets) becomes the ICQ number as text."""

    text = re.sub(r"(?is)<script\b[^>]*\bsrc\s*=\s*[\"']?[^\"'>]*(?:banner\d*\.js|tablogged\.js|/cgi/)[^>]*>.*?</script\s*>", "", text)
    text = re.sub(r"(?is)<script\b[^>]*\bsrc\s*=\s*[\"']?https?://[^>]*>.*?</script\s*>", "", text)
    return text


FORM_RE = re.compile(r"(?is)<form\b([^>]*)>(.*?)</form\s*>")
CONTROL_RE = re.compile(r"(?is)<input\b[^>]*>|<select\b.*?</select\s*>|<textarea\b.*?</textarea\s*>|<button\b.*?</button\s*>")
SEARCH_TYPES = [("news", "news, article, jobs"), ("pictures", "picture/photo"), ("people", "scener"), ("groups", "group"),
                ("parties", "party")]


def neutralize_forms(text, site, out):
    """No <form> survives. Search forms become client-side fields (/js/oj.js); forms that write
    (POST, hidden `u=` macro: votes, attendance, edits) keep their content without controls and
    get an 'archived' notice; leftover unpaired form tags are removed."""
    def fn(m):
        attrs, inner = m.group(1), m.group(2)
        names = {n.lower() for n in re.findall(r"""(?i)\bname\s*=\s*["']?([^"'\s>]+)""", inner)}
        if re.search(r"(?is)<select\b[^>]*name\s*=\s*[\"']?cat\b", inner) and "kw" in names:
            # the "search in" box of every page: a type list and a keyword field
            opts = "".join(f'<OPTION value="{v}">- {t}' for v, t in SEARCH_TYPES)
            inner = re.sub(r"(?is)<select\b([^>]*)>.*?</select\s*>",
                           lambda sm: f'<SELECT class="oj-type"{re.sub(r"(?i)name=[^ >]+", "", sm.group(1))}>{opts}</SELECT>', inner, count=1)
            inner = re.sub(r"(?is)<input\b([^>]*name\s*=\s*[\"']?kw\b[^>]*)>",
                           lambda im: '<INPUT type="TEXT"' + re.sub(r"(?i)\b(type|name|value|onfocus)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", im.group(1)) + ">", inner)
            inner = re.sub(r"(?is)<input\b[^>]*type\s*=\s*[\"']?(?:hidden|submit|image)[^>]*>", "", inner)
            inner = inner.replace('<INPUT type="TEXT"', '<INPUT type="TEXT" data-oj-kw="1"', 1)
            return inner
        kind = next((v for k, v in SEARCH_KEYS.items() if k in names and v), None)
        is_write = re.search(r"(?i)method\s*=\s*[\"']?post", attrs) or "u" in names or "page_ok" in names
        if kind and not is_write:
            inner = re.sub(r"(?is)<input\b[^>]*type\s*=\s*[\"']?(?:hidden|submit|image)[^>]*>", "", inner)
            inner = re.sub(r"(?is)<input\b[^>]*>", lambda _m: search_widget(kind), inner, count=1)
            return re.sub(r"(?is)<input\b[^>]*>", "", inner)
        act = (re.search(r"""(?i)action\s*=\s*["']?([^"'\s>]+)""", attrs) or [None, ""])[1]
        site.dropped.append((out, "form " + (act or attrs.strip()[:60])))
        had_submit = re.search(r"(?is)<input\b[^>]*type\s*=\s*[\"']?(?:submit|image|button)|<button\b", inner)
        inner = CONTROL_RE.sub("", inner)
        if had_submit:
            inner += '<FONT class=discreet>[ <A href="%s">archived</A>: this form is no longer available ]</FONT>' % ARCHIVED
        return inner
    text = FORM_RE.sub(fn, text)
    text = re.sub(r"(?is)</?form\b[^>]*>", "", text)
    # scripts of the removed forms (find(), focus())
    text = re.sub(r"(?is)<script[^>]*>\s*<!--\s*function find\(\).*?</script\s*>", "", text)
    return re.sub(r"(?is)<script[^>]*>\s*<!--\s*document\.\w+\.\w+\.focus\(\);?\s*//\s*-->\s*</script>", "", text)


def search_widget(kind, size=18):
    return ('<SPAN class="oj-search" data-t="%s"><INPUT type="TEXT" size=%d maxlength=100 value="">'
            '<INPUT type="BUTTON" value="go"></SPAN>' % (kind, size))


MAILTO_A_RE = re.compile(r"<a\b[^>]*href\s*=\s*[\"']?\s*mailto:[^>]*>(.*?)</a\s*>", re.I | re.S)
EMAIL_RE = re.compile(r"([A-Za-z0-9._+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")


def redact_emails(text):
    text = MAILTO_A_RE.sub(lambda m: m.group(1), text)
    text = re.sub(r"(?i)mailto:", "", text)
    return EMAIL_RE.sub(lambda m: m.group(1) + "@" + MASK, text)


IPV4 = re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])")


def mask_ips(text):
    """IPv4 addresses are masked unless they are the host of a URL (public servers of the time)."""
    def fn(m):
        before = text[max(0, m.start() - 12):m.start()].lower()
        return m.group(0) if before.endswith(("://", "@")) else MASK
    return IPV4.sub(fn, text)


def encode_page(text):
    return text.encode(ENC, errors="xmlcharrefreplace")


# --- search indexes and script ---------------------------------------------------------------------
def clean_text(v, limit=0):
    t = html.unescape(re.sub(r"(?s)<[^>]*>", " ", engine.vstr(v)))
    t = redact_emails(re.sub(r"\s+", " ", t).strip())
    return t[:limit].rstrip() + "..." if limit and len(t) > limit else t


def write_indexes(site, out):
    """/idx/*.json: [url, title, subtitle, text, thumbnail] rows, only fields shown on public pages
    and only rows whose page is in the build (no real name, e-mail or birth data)."""
    db, pages = site.macros.db, site.pages
    idx = {}
    news, home_rank = [], 0
    for r in db.rows("SELECT a.id, a.dt, a.title, a.smalltxt, a.isarticle, a.url, a.category, c.nick, c.group1 FROM news a,"
                     " vsceners c WHERE a.id_people = c.id AND a.active = 1 AND a.dt >= ? ORDER BY a.dt DESC, a.id DESC",
                     (oj.CUTOFF,)):
        if r["CATEGORY"] != 2:
            home_rank += 1
            p = home_rank // 10 + (home_rank % 10 > 0)
            listing = "/index.htm" if p == 1 else f"/index-p{p}.htm"
        else:
            listing = "/index-cat2.htm"
        link = f"/{r['ID']}/article.htm" if r["ISARTICLE"] and f"/{r['ID']}/article.htm" in pages else ""
        if not link:
            u = engine.vstr(r["URL"]).strip()
            link = u if re.match(r"(?i)^(https?|ftp)://", u) else (listing if listing in pages else "")
        author = clean_text(r["NICK"]) + (" / " + clean_text(r["GROUP1"]) if r["GROUP1"] else "")
        news.append([link, clean_text(r["TITLE"]), f"[{r['DT']:%Y-%m-%d}] {author}", clean_text(r["SMALLTXT"], 300), ""])
    idx["news"] = news
    idx["people"] = [[f"/{r['ID']}/nick.htm", clean_text(r["NICK"]),
                      " / ".join(clean_text(g) for g in (r["GROUP1"], r["GROUP2"]) if g), "", ""]
                     for r in db.rows("SELECT id, nick, group1, group2 FROM vsceners ORDER BY nick COLLATE NOCASE, id")
                     if f"/{r['ID']}/nick.htm" in pages]
    idx["groups"] = [[f"/nicklist-group{r['ID']}.htm" if f"/nicklist-group{r['ID']}.htm" in pages else "",
                      clean_text(r["NAME"]), clean_text(r["COUNTRY"]), "", ""]
                     for r in db.rows("SELECT a.id, a.name, d.country FROM links a, countries d WHERE a.idcountry = d.id"
                                      " AND a.idcategory = 15 AND a.isactive = 1 ORDER BY a.name COLLATE NOCASE, a.id")]
    parties = []
    for r in db.rows("SELECT a.id, a.name, a.place, a.dt_begin, a.dt_end, c.country FROM parties a, countries c"
                     " WHERE a.id_country = c.id AND a.active = 1 ORDER BY a.dt_begin DESC, a.id DESC"):
        when = "next" if r["DT_END"] >= NOW else "past"
        link = next((u for u in (f"/{when}/{r['ID']}/party_infofile.htm", f"/{when}/{r['ID']}/party_results.htm",
                                 f"/{when}/{r['ID']}/party_coming.htm") if u in pages), "")
        parties.append([link, clean_text(r["NAME"]), f"[{r['DT_BEGIN']:%Y-%m-%d}] {clean_text(r['PLACE'])}, "
                        f"{clean_text(r['COUNTRY'])}", "", ""])
    idx["parties"] = parties
    idx["pictures"] = [[f"/{r['ID']}/viewpic.htm", clean_text(r["LEGEND"]) or "[no legend]",
                        ("photo" if r["ISPHOTO"] else "picture") + (f" {r['PICYEAR']}" if r["PICYEAR"] else ""),
                        " ".join(clean_text(r[k]) for k in ("DESCRIPTION", "AUTHOR", "EVENT", "WHO", "PLACE")),
                        site.asset(f"/zi/piclib/tn/{r['ID']:05d}.jpg") or ""]
                       for r in db.rows("SELECT * FROM pictures WHERE isactive = 1 ORDER BY dtadded DESC, id DESC")
                       if f"/{r['ID']}/viewpic.htm" in pages]
    sizes = {}
    for name, rows in idx.items():
        p = out / "idx" / (name + ".json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rows, ensure_ascii=True, separators=(",", ":")) + "\n", encoding="ascii")
        sizes[name] = (len(rows), p.stat().st_size)
    return sizes


def write_js(skins, out):
    js = (REPO / "scripts" / "oj.js").read_text(encoding="utf-8").replace("/*SKINS*/[]", json.dumps(skins))
    js = "".join(c if ord(c) < 128 else "\\u%04x" % ord(c) for c in js)     # ASCII only, whatever the charset
    p = out / "js" / "oj.js"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(js, encoding="utf-8")
    return hashlib.sha256(js.encode()).hexdigest()[:10]


# --- main -----------------------------------------------------------------------------------------
def build_skin(skin, skins, out, raw, jsv):
    """Crawl and write one skin; returns what main() needs (runs in its own process)."""
    global RAW
    RAW = raw
    db = oj.DB(RAW / "db" / "OJUICE.sqlite", NOW)
    photos = sorted(p.name.lower() for p in (RAW / "id3" / "zi" / "nicks").iterdir() if p.is_file())
    macros = oj.OJMacros(db, photos)
    prefix = "" if skin == DEFAULT_SKIN else f"/skin/{skin}"
    site = Site(None, skin, prefix, macros, skins)
    site.crawl(["/index.htm", "/archived.htm", "/skins.htm", "/oj-search.htm", "/404.htm"])
    for p, text in sorted(site.pages.items()):
        dst = out / (prefix + p).lstrip("/")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(encode_page(text.replace('/js/oj.js"', f'/js/oj.js?v={jsv}"')))
    idx = out / (prefix + "/index.htm").lstrip("/")
    if idx.exists():
        shutil.copyfile(idx, idx.with_name("index.html"))
    sizes = write_indexes(site, out) if skin == DEFAULT_SKIN else {}
    print(f"{skin}: {len(site.pages)} pages, {len(site.redirects)} redirects, {len(site.unlinked)} unlinked", flush=True)
    return skin, sorted(site.assets), site.url_map, sorted(set(site.unlinked)), sizes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skins", default="all", help="all, or a comma list of skins besides o")
    ap.add_argument("--out", default=str(REPO / "site"))
    ap.add_argument("--raw", default=None, help="raw store (default: $ID3X_RAW or ~/Code/id3x/raw)")
    ap.add_argument("--jobs", type=int, default=4, help="skins rendered in parallel (about 2.5 GB of memory each)")
    a = ap.parse_args(argv)
    raw = Path(a.raw).expanduser() if a.raw else RAW
    skins = SKINS if a.skins == "all" else [DEFAULT_SKIN] + [s for s in a.skins.split(",") if s and s != DEFAULT_SKIN]
    out = Path(a.out)
    for _ in range(5):              # macOS may recreate .DS_Store files while the tree is removed
        if out.exists():
            shutil.rmtree(out, ignore_errors=True)
    if out.exists():
        raise SystemExit(f"cannot empty {out}")
    out.mkdir(parents=True)
    jsv = write_js(skins, out)
    jobs = [(skin, skins, out, raw, jsv) for skin in skins]
    if a.jobs > 1:
        import multiprocessing
        with multiprocessing.get_context("spawn").Pool(min(a.jobs, len(jobs))) as pool:
            results = pool.starmap(build_skin, jobs)
    else:
        results = [build_skin(*j) for j in jobs]
    meta = {r[0]: r for r in results}
    for name, (n, size) in meta[DEFAULT_SKIN][4].items():
        print(f"  idx/{name}.json: {n} rows, {size // 1024} KB")
    assets = sorted({p for r in results for p in r[1]})
    for p in assets:
        dst = out / p.lstrip("/")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(raw / "id3" / p.lstrip("/"), dst)
    hosting = REPO / "hosting"
    hosting.mkdir(exist_ok=True)
    shutil.copyfile(out / "404.htm", hosting / "404.html")
    meta_dir = REPO / "build-meta"
    meta_dir.mkdir(exist_ok=True)
    url_map = {"comment": "Old www.ojuice.net URL (lower-case path?query, parameters sorted) -> static file of the archive.",
               "urls": dict(sorted(meta[DEFAULT_SKIN][2].items()))}
    (meta_dir / "url-map.json").write_text(json.dumps(url_map, indent=1) + "\n")
    with open(meta_dir / "unlinked.tsv", "w") as f:     # one line per (skin, link, reason)
        f.write("skin\tlink\treason\tpages\tfirst page\n")
        for skin in skins:
            agg = {}
            for page, link, why in meta[skin][3]:
                n, first = agg.get((link, why), (0, page))
                agg[(link, why)] = (n + 1, min(first, page))
            for (link, why), (n, first) in sorted(agg.items()):
                f.write(f"{skin}\t{link}\t{why}\t{n}\t{first}\n")
    print(f"assets: {len(assets)}")


if __name__ == "__main__":
    main()
