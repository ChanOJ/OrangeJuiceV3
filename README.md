# OrangeJuiceV3

**Orange Juice v3 (www.ojuice.net, 2000-11-20 to 2006-09).** Orange Juice was an online magazine
about the demoscene: news and articles, 25,000 people sheets, groups and links, parties with
results and info files, picture library, oneliner, charts and switchable skins. This is its third
version, served by the in-house engines id3 (classic ASP) and id3x (ASP.NET) over SQL Server.

Published as a **100 % static, read-only archive** on bunny.net at the hostname of
[`site.json`](site.json) (`orangejuicev3.sauthier.family`), like
[OrangeJuice97](../OrangeJuice97), [OrangeJuice98](../OrangeJuice98) and
[OrangeJuice2k](../OrangeJuice2k) (the era before 2000-11-20). The engine is not ported nor
published: a Python renderer pre-renders the `$$MACRO$$` templates of the skins over a SQLite
extraction of the database.

## Layout

| Path | Content |
| --- | --- |
| `site/` | The generated static site (never edit by hand; **git-ignored**): skin `o` at the root, `light` under `/skin/light/`, `/js/oj.js`, search indexes in `/idx/` |
| `hosting/404.html` | Custom 404 page (generated with `site/`) |
| `build-meta/` | `url-map.json` (old query URL -> static file, source of the Edge Script), `unlinked.tsv` |
| `site.json` | Hosting settings: owner, repository, storage zone, pull zone, hostname, 404 page |
| `docs/plan.md` | Inventory (templates, macros, skins), decisions, order of work |
| `docs/data-model.md` | Engine, tables and columns used, URL scheme, removed features, redactions |
| `scripts/statify.py` | Raw store -> `site/` + `hosting/404.html` + `build-meta/` (crawl of the rendered pages) |
| `scripts/id3render/engine.py` | The id3 template engine, reimplemented |
| `scripts/id3render/oj.py` | The Orange Juice data macros over SQLite, with the redaction of private columns |
| `scripts/oj.js` | Source of `site/js/oj.js`: skin chooser, client-side search |
| `scripts/verify_build.py`, `scripts/check_excludes.py` | Checks of `site/` (see below) |
| `scripts/mdf/` | Pure-Python reader of SQL Server 2000 `.MDF` files and the MDF to SQLite extractor |
| `infra/`, `scripts/deploy.py`, `scripts/publish.sh`, `scripts/smoke_test.py`, `scripts/bootstrap.sh` | Hosting, as OrangeJuice2k |
| `private/` | Notes and Wayback captures, **git-ignored** |

Inputs are read from the shared raw store of the id3/id3x sites, `~/Code/id3x/raw` (one setting:
`RAW` in `scripts/statify.py`, or the `ID3X_RAW` environment variable): skins `id3/z/`, media
`id3/zi/`, database `db/OJUICE.sqlite`. It holds personal data and credentials and is never
published; only rendered pages and the files they reference are copied.

## Regenerating `site/`

`site/` is generated: never edit it by hand, fix the scripts and regenerate (about 15 minutes for
the two skins, rendered in parallel; `--skins light` renders `o` plus the listed skins, for work only):

```bash
python3 -I scripts/statify.py
```

```bash
python3 -I scripts/verify_build.py
```

```bash
python3 -I scripts/check_excludes.py
```

Local preview (the `site` entry of `.claude/launch.json`):

```bash
python3 -m http.server 3417 --directory site
```

## Hosting (bunny.net, `infra/main.tf`)

- Edge rule 1: any method other than GET, HEAD, OPTIONS is blocked at the edge, before the
  origin (bunny.net answers 403; edge rules cannot return 405). `block_post_requests` is also on.
- Edge rule 2: `Content-Security-Policy`, `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: no-referrer`, HSTS, `Permissions-Policy`.
  COOP/CORP `same-origin`.
- CSP: `default-src 'self'`; scripts and styles same-origin plus `'unsafe-inline'` (every page
  loads `/js/oj.js`, and the v3 HTML keeps inline `<script>` blocks, `on*` handlers and
  `javascript:` popup links that no hash or nonce can cover); no `'unsafe-eval'`, nothing from
  another origin; `connect-src 'self'` (search indexes); `img-src 'self'` (third-party images
  were removed by the build); `object-src 'none'`;
  frames same-origin; `form-action 'none'`; `base-uri 'none'`; no workers or manifests.
- Edge rule 3: `Content-Type: text/html; charset=iso-8859-1` for `.htm/.html` (the pages are
  ISO-8859-1) and browser cache 1 hour.
- Edge rule 4: `zip` and the old picture formats of the picture library (`iff lbm pcx tga`):
  `application/octet-stream` + `Content-Disposition: attachment`.
- Edge rule 5: browser cache 7 days for images and archives; CDN cache 30 days,
  purged on each deployment. The cache key keeps the query string (`cache_vary = querystring`).
- Cookies are stripped before the origin; TLS "Compatible"; forced HTTPS. Search engines are
  allowed (no `X-Robots-Tag`, no `robots.txt`).
- Custom 404: `hosting/404.html` (`not_found_page` in `site.json`), deployed as `/404.html`.

### Legacy redirects (Edge Script)

Orange Juice v3 URLs are path-only (`/16139/nick.htm`, `/groups/a/links.htm`) and the archive
keeps them, lower-cased (IIS was case-insensitive, bunny.net is not). Old links with a query
string (`/index.htm?idcat=3&OJ_NEWS_QRYpg=2`, `/nicklist.htm?idgroup=12`, searches, the
`golink.htm` click counters) and old capitalised links (`/_A/nicklist.htm`) are answered by
`infra/edge/redirects.js`, a **middleware Edge Script** attached to the pull zone
(`bunnynet_compute_script` + `origin.middleware_script`, managed by OpenTofu):

- Generated by `scripts/gen_edge_script.py` from the rules of `scripts/statify.py` (removed
  pages, content parameters, search parameters): no list of URLs, 5 KB. It folds the content
  parameters into the static file name exactly as the build does (`/index-cat3-p2.htm`), drops
  the other parameters, sends searches to `/oj-search.htm?t=<type>&q=<words>` and write, login
  and admin pages to `/archived.htm`; all with a `301` cached one day by browsers.
- `golink.htm?url=` resolves only `ojuice.net` targets (no open redirect): others get a **404
  with `/404.html`** (the script asks the storage zone for a path that never exists, so the
  status and page are the origin's). A target missing from the archive is a 404 too.
- Static paths pass through untouched (fast path: no query string, no capital), and the script
  only runs on cache misses. `scripts/test_edge_script.mjs` checks every old query URL of
  `build-meta/url-map.json` (2875), edge cases, and that each target exists in `site/`.

## Removal requests

Pages that list or describe people end with a small footer, "To be removed from this page, contact me." (a `mailto:` link). `scripts/statify.py` adds it (`is_people_page`), so it survives rebuilds; `scripts/verify_build.py` leaves it out of its `mailto:` and e-mail checks.

## Deploying

`site/` is generated on the Mac (it needs the raw store `~/Code/id3x/raw`) and is not in the repository, so the
**website is deployed from the Mac**, never by GitHub Actions. One command builds, checks,
deploys and tests (Proton Pass CLI logged in):

```bash
scripts/publish.sh
```

It runs `statify.py`, checks that the Edge Script is up to date and passes its test, then
`scripts/deploy.py`, which **refuses to deploy unless `verify_build.py` and `check_excludes.py`
pass** on this exact build (it runs them, about 90 s, and keeps a stamp in `tmp/` so a re-run of
the same build skips them), uploads only new or changed files (SHA-256 against the remote
listing, 24 in parallel), deletes removed ones (guarded against mass deletion), purges the CDN;
then `smoke_test.py --sample 300`. Interrupted or failed uploads: run it again, only what is
missing is sent. Dry run, or deploy the existing `site/` without rebuilding:

```bash
scripts/publish.sh --dry-run
```

```bash
scripts/publish.sh --no-build
```

The steps one by one:

```bash
pass-cli run --env-file infra/secrets.env -- python3 -I scripts/deploy.py --dry-run
```

```bash
pass-cli run --env-file infra/secrets.env -- python3 -I scripts/deploy.py
```

```bash
python3 -I scripts/smoke_test.py --sample 300
```

Without network or secret (checks, local manifest, unit test of the diff logic):

```bash
python3 -I scripts/deploy.py --local-check
```

```bash
python3 -I scripts/deploy.py --self-test
```

- **Infra**: pull requests touching `infra/`, `site.json` or `build-meta/url-map.json` show the
  plan (after checking that the Edge Script is up to date and passes its test); apply is a
  manual run of *Infra: bunny.net* (action `apply`, from `main`), which commits the encrypted
  state and runs the smoke test (untick it for the first apply, before the first deploy).
- **Monitoring**: *Monitoring: weekly smoke test* checks the public site every Monday (status,
  headers, redirects; pages are compared with `site/` only locally).

GitHub secrets: `BUNNY_API_KEY` (bunny.net account API key) and `TF_VAR_STATE_PASSPHRASE`
(state encryption), both from Proton Pass vault `OldSites`. Local plan:

```bash
cd infra && pass-cli run --env-file secrets.env -- tofu plan
```

## First setup

1. Validate offline and create the state passphrase item `OrangeJuiceV3-tofu-state-passphrase`
   in Proton Pass (generated, never shown):

   ```bash
   scripts/bootstrap.sh new OrangeJuiceV3 orangejuicev3 orangejuicev3 orangejuicev3.sauthier.family
   ```

2. Initialise git and commit on a branch, then create the GitHub repository
   `ChanOJ/OrangeJuiceV3` and its secrets (also adds the `origin` remote):

   ```bash
   scripts/bootstrap.sh github
   ```

3. Push the branch, open the PR (the plan runs), merge.
4. Run the *Infra: bunny.net* workflow from `main` with action `apply` and **smoke test unticked**:
   it creates the storage zone, the Edge Script, the pull zone, the hostname and the edge rules,
   and commits the encrypted state.
5. At Infomaniak (Manager > Domaines > sauthier.family > Zone DNS), add a CNAME
   `orangejuicev3` -> `orangejuicev3.b-cdn.net`. The certificate is issued once DNS answers.
6. Pull the state commit, then deploy from the Mac (about 69 800 files, 1.4 GB; the smoke test
   runs at the end):

   ```bash
   git pull
   ```

   ```bash
   scripts/publish.sh --no-build
   ```

## Manual steps in the bunny.net dashboard (only if the apply cannot do it)

If the apply rejects an edge rule, the 404 path or the Edge Script, set it by hand:

1. CDN > pull zone `orangejuicev3` > *Edge rules* > *Add Edge Rule*: condition *Request Method*
   is none of `GET`, `HEAD`, `OPTIONS` -> action *Block Request*.
2. *Edge rules* > *Add Edge Rule*: condition *URL* `*` -> one *Set Response Header* action per
   header listed above (values in `infra/main.tf`, `local.csp` and the rule 2 block).
3. *Edge rules*: condition *URL Extension* `htm`, `html` -> *Set Response Header*
   `Content-Type: text/html; charset=iso-8859-1`.
4. Storage > storage zone `orangejuicev3` > *Error pages*: set the 404 file to `/404.html`.
5. Edge Scripting > *Add Script* > *Middleware*, name `orangejuicev3-legacy-redirects`, paste
   `infra/edge/redirects.js`, *Publish*; link it to the pull zone `orangejuicev3`. Then CDN >
   pull zone > *Caching* > *Vary cache*: *Query string* on.
