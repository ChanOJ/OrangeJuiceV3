# Orange Juice v3: data model and static rendering

Orange Juice v3 ran on the in-house engines id3 (classic ASP) and id3x (ASP.NET) with SQL Server
2000 (database OJUICE). This file describes, in public-safe terms, what `scripts/statify.py`
reads and how it renders the archive. No secret, personal data or IP address is reproduced here.

Inputs (shared raw store `~/Code/id3x/raw`, setting `RAW` / `$ID3X_RAW`, read-only):

| Input | Content |
| --- | --- |
| `id3/z/<skin>/` | Skin templates (`*.htm`, `incl_*.inc`), CSS, JS and pictures (`p/`) |
| `id3/zi/` | User media: ID photos (`nicks/`), picture library (`piclib/`, `tn/`, `ori/`), smilies, wallpapers, files |
| `db/OJUICE.sqlite` | Full extraction of the OJUICE database (state of 2006-09-07), incl. `_procedures`, `_columns` |
| `id3/cgi/_f.asp`, `id3x/core/start.aspx`, `id3x/macros/oj/*.cs` | Engine and macro sources: read to reimplement them, never published |

## 1. Template engine (`scripts/id3render/engine.py`)

- A URL `/<a>/<b>/<page>.htm` renders the skin file `<page>.htm`; the other path elements are the
  `REP:n` arguments (`REP:1` = parent folder), the query string the `FORM:x` values.
- Loading: SSI includes from the skin folder (then the fallback folder of the skin config),
  `$$INCL:x$$`, indentation and blank lines removed, `$$-$$` / `$$_$$` = skin URL.
- Parsing (split on `$$`): `E:` macros run while parsing (first value wins), then the `B:` loops,
  then `SET:`. Display: `IF`, `IF0`, `ELSE`, `IF=`, `IF!`, `IF+`, `IF-`, `IFNULL`, `REM`, `BIF`,
  `BIF0`, `DO:cols:perpage`, `LOOP`, `BFIN`, `END`; value macros with chained filters
  (`MakeFuncs` of `_f.asp`, plus `groups_` / `groupsshort_` of the site).
- id3x behaviours kept (checked on Wayback captures): en-US dates (`9/1/2006 5:00:00 PM`),
  `FORM:x:default` without default, `REP:n` empty beyond the path, multi-column loops at 100 %,
  the multipage `[ < / 8 / ... / > ]` turned into digit pictures by the site's `MultiPageModif`.
- `src=` / `background=` pictures relative to the page are moved under `/z/<skin>/`.
- `$$RND:a:b$$` (random affiliate buttons) is a deterministic pick per page path.

## 2. Data macros (`scripts/id3render/oj.py`)

The SQL of `oj_e.cs` / `oj_b.cs` and the row modifiers of `oj_bm.cs`, translated to SQLite, with
the view `vsceners` (people + groups from `LINKS` + country). SQL Server's case-insensitive
collation is reproduced with `COLLATE NOCASE`; ties are broken by id.

| Macro | Tables | Notes |
| --- | --- | --- |
| `B:OJ:NEWS` | NEWS, CATEGORIES, vsceners | every active news with `dt >= 2000-11-20` (the site showed the latest 200), 10 per page |
| `E:OJ:ARTICLE`, `E:OJ:NICK`, `E:OJ:PICTURE`, `E:OJ:PARTY`, `E:OJ:LINK`, `E:OJ:ONELINER` | one row by id | NICK adds the group member counts and the functions |
| `B:OJ:NICKLIST` | vsceners | by letter (`/_a/nicklist.htm`) or group (`?idgroup=`), 2000 per page |
| `B:OJ:LINKS` | LINKS, LINKSCATEGORIES | categories (`/www/<cat>/links.htm`) and groups by letter (`/groups/<l>/links.htm`) |
| `B:OJ:PARTIES`, `PARTIESRESULTS`, `PARTY_COMING` | PARTIES, PARTIES_COMING | next / past split on the frozen clock |
| `B:OJ:PICLIB` | PICTURES | pictures / photos, newest first |
| `B:OJ:ONELINER`, `B:OJ:BOO` | ONELINER, boo, boo_ol | host names shown as `????`, IPs never read |
| `B:OJ:BIRTHDAYS`, `TOPNEWSWRITERS`, `LASTDEMOS`, `WALLPAPERS`, `COUNTRIES`, `COMPUTERS`, `CURRENCIES` | | |
| `E:OJ:DAILYNEWS` | counts of the last 24/48 h on the frozen clock | webcams: 0 |

Not rendered (anonymous visitor of a read-only site): session, login, admin, POST, banner
(`GETPUB`), webcam check, FTP search, Google search, Nectarine live statistics, forums.

Clock: frozen at 2006-09-07 23:59:59, the last day of the database.

## 3. Pages, URLs and the crawl

The build starts at `/index.htm` (and the archive pages) and follows every internal link of every
rendered page. Paths are kept and lower-cased (IIS was case-insensitive, bunny.net is not; the
pictures keep their on-disk spelling). Query strings that select content become part of the file
name; other parameters are dropped:

| Old URL | Static file |
| --- | --- |
| `/index.htm?OJ_NEWS_QRYpg=N` | `/index-pN.htm` |
| `/index.htm?idcat=C&OJ_NEWS_QRYpg=N` | `/index-catC-pN.htm` |
| `/nicklist.htm?idgroup=G` | `/nicklist-groupG.htm` |
| `/<x>/<page>.htm?<LOOP>pg=N` | `/<x>/<page>-pN.htm` |
| `/news/golink.htm?id=&url=U` and other `golink.htm` counters | the target `U` directly |
| searches (`?sn=`, `?snick=`, `?sl=`, `?sp=`, `?kw=`) | `/oj-search.htm?t=<type>` (client-side) |
| write, login and admin pages | `/archived.htm` |

`build-meta/url-map.json` lists every old query URL and its static file (Edge Script source);
`build-meta/unlinked.tsv` the links made plain text (one line per link and reason, with a page count) (dead third parties, pictures missing from
the skins since 2006).

## 4. Removed or frozen

Forms (search boxes become client-side fields; write forms keep their text with an "archived"
notice), the logged-in box (`tablogged.js`), banners, statistics beacons, the ICQ status badge
(the ICQ number is shown as text), webcams (none up), click counters (direct links), the
disabled auto-refresh, Nectarine listener counts.

Repairs: the HELP buttons (hover popups of `common.js`, which only handled IE's `document.all`
and Netscape 4, so they showed nothing in other browsers) are drawn by `/js/oj.js` with the same
placement; the "click here for the full version" link of `light` goes to `/index.htm?skin=o`,
which also forgets the skin chosen on `/skins.htm`. Archive notices are in English only, like
the v3 site.

## 5. Redactions

- `oj.py redact()` drops password, login, session, IP, user agent, department and Nectarine
  account columns before any template sees a row; birth years become `????`, e-mails
  `local@????`, oneliner host names `????`.
- In the output: `mailto:` links become text, any `local@domain` becomes `local@????`, IPv4
  addresses are masked unless they are the host of a URL.
- ID photos (`/zi/nicks/<id>.<ext>`) only for people whose sheet is published.
- `scripts/verify_build.py` re-reads the private values at run time and fails on any leak.

## 6. Skins

See `docs/plan.md` (table of every `z/` folder). Rendered: `o` at the root and `light` under `/skin/light/`, chosen with `/skins.htm`
(`localStorage`, no cookie); new visitors always get `o`.

## 7. Output

Pages are written in iso-8859-1 (characters outside latin-1 as numeric references), declare it,
and load `/js/oj.js?v=<hash>`. Search indexes `/idx/{news,people,groups,parties,pictures}.json`
hold `[url, title, subtitle, text, thumbnail]` rows of public fields only. Deterministic: sorted
traversal, stable SQL ordering, no timestamps.
