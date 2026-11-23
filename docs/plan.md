# Orange Juice v3: plan of the static archive

Orange Juice v3 (www.ojuice.net) ran from 2000-11-20 (v3 launch, the 2000 archive covers what came
before) to 2006-09 on the in-house engine id3 (classic ASP) then id3x (ASP.NET), with SQL Server 2000
(database OJUICE). The engine is **not** ported or published: a Python renderer pre-renders the
`$$MACRO$$` templates of the skins over the SQLite extraction of the database.

Inputs: the shared raw store `~/Code/id3x/raw` (one setting, `RAW` in `scripts/statify.py`, or
`$ID3X_RAW`), never the NAS. Nothing of it is published except the rendered pages, the skin
images and the user media the pages reference.

## 1. Inventory (details: `private/notes/macro_inventory.md`, git-ignored)

- **Template language** (`id3/cgi/_f.asp` init/disp, `id3x/core/start.aspx`): SSI includes,
  `$$E:svc:macro:args$$` (query run while parsing, fills `svc:macro:COLUMN`), `$$B:...$$` loops
  with `BIF` / `DO:cols:perpage` / `LOOP` / `BFIN` / `BIF0`, `IF`, `IF0`, `ELSE`, `IF=`, `IF!`,
  `IF+`, `IF-`, `END[:name]`, `REM`, `SET:` (after the loops), `INIT:`, value macros with chained
  filters (htmlencode, urlencode, txt2gif, to2, ncase, pluriel, escape, truncate, makehref,
  date parts, exec, ...), `REP:n` (path elements), `FORM:x` (query string), `PAGE:*`, `$$-$$`.
  Relative `src=` / `background=` images are moved under `/z/<skin>/`.
- **Data macros**: 29 `B:` loops and 21 `E:` macros in `id3x/macros/oj/oj_{e,b,bm}.cs` (plain SQL,
  no stored procedure except `oj_getpub` for banners). Read paths are ported to SQLite in
  `scripts/id3render/oj.py`; admin, session, POST, banner, FTP search and webcam macros return
  nothing (anonymous visitor of a read-only site).
- **Pages of skin `o`** (89 templates): read pages rendered (home and news, article, people sheet
  and lists, birthdays, groups and links, parties next/past with results, info file and
  attendance, picture library and picture page, oneliner popups, best of oneliner, charts,
  misc, FAQ, interviews, wallpapers, webcams, contact, tshirts, ...); about 55 write, login,
  admin and counter pages removed (`REMOVED` in statify.py).
- **Skins** (`z/`):

  | Skin | Verdict | Archive |
  | --- | --- | --- |
  | `o` | final design (2004-2006) | site root, default for every visitor |
  | `light` | light.ojuice.net, distinct lighter fork (own head and menus) | `/skin/light/` |
  | `ob` | oj_blue (2000-09 to 2001-03): blue pictures over the templates of `o` | not rendered (owner decision 2026-11-23, after a preview) |
  | `v2` | older v2 design, home page only | not rendered (owner decision 2026-11-23: home page only) |
  | `om` | older copy of `o`: the home page differs by 12 lines (copyright year, one button) | not rendered (to confirm) |
  | `o.bak` | backup of an older `o` (forum era); same look, older scripts (browser redirect to ugly.ojuice.net) | not rendered (to confirm) |
  | `imode` | i-mode / WinCE side pages (5 news, oneliner form) | not rendered (to confirm) |
  | `u`, `o.stop`, `oj_maint`, `onds` | one JS file, parked or maintenance pages, news syndication | not skins |
  | `scene.org`, `offline` | another site (Scene.org), a Nectarine notice | not Orange Juice |
  | `ojold` | OrangeJuice97 | already published |

## 2. Decisions (same as OrangeJuice2k unless noted)

- **Crawl, not a page list**: the build starts at `/index.htm` and follows every internal link of
  every rendered page, resolving it as IIS + id3x did (last path element = template, the others
  = `REP:n` arguments). Only reachable pages exist; 404s of the original are not reproduced.
- **URLs**: path-only URLs kept exactly, lower-cased (`/16139/nick.htm`, `/groups/a/links.htm`,
  `/past/123/party_results.htm`). Query strings that select content get a static name:
  `/index.htm?idcat=3&OJ_NEWS_QRYpg=2` -> `/index-cat3-p2.htm`, `/nicklist.htm?idgroup=12` ->
  `/nicklist-group12.htm`. Every such URL is in `build-meta/url-map.json` for the Edge Script
  (301 from the old query URLs, like 2k).
- **News**: the site showed the 200 latest news (`TOP 200`); the archive pages through every
  active news of the v3 era (dt >= 2000-11-20, 4441 news, 10 per page), as 2k lifted its caps.
- **Clock**: frozen at 2006-09-07 23:59:59 (last day of the database): next/past parties,
  birthdays, "new" markers, "last 4 months" news writers.
- **People** (all 25,125 sheets): nick, real name, e-mail as `local@????` (text), birth date as
  `dd/mm/????`, ICQ number as text (no ICQ status image), sex, ID photo (file present in
  `zi/nicks/`), country, computer, groups, functions, homepage, IRC, preferences. Hidden:
  department, password, logins, IPs, oneliner host names (`????`).
- **Removed**: forms (searches become client-side fields), logins and the logged-in box,
  counters and the `golink.htm` redirector (direct links), banners, stats beacons, ICQ status,
  live webcam checks (none up), FTP search, Google search, Nectarine live stats.
- **Search**: client-side (`/js/oj.js` + `/idx/*.json`), `/oj-search.htm` results page.
- **Skin chooser**: `localStorage` only, default always `o`.

## 3. Implementation order (page type by page type)

1. Engine + `nick.htm` checked against the Wayback capture of `/16139/nick.htm` (2005-01-28):
   identical after whitespace normalisation except the removed stats beacon.
2. Crawl of skin `o`: home and news pages, articles, people lists and sheets, groups, links,
   parties, picture library, misc pages; compare a few raw Wayback captures per page type
   (`private/notes/wayback-v3/`).
3. Clean-up: forms, scripts, third parties, e-mails; assets copied (only referenced files).
4. Other skins.
5. Search indexes, skin chooser page, `/archived.htm`, `hosting/404.html`.
6. Checks adapted from 2k: `verify_build.py` (forms, macros left, links and assets, charset,
   e-mails, birth years, departments, private photos), `check_excludes.py`; two builds with the
   same hashes.
7. Local preview for approval; then infra (`infra/`, Edge Script), repository, deploy.
