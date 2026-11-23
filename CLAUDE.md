# OrangeJuiceV3: context for Claude sessions

Static archive of Orange Juice v3 (www.ojuice.net, 2000-11-20 to 2006-09, engines id3 / id3x).
Same approach and hosting as `~/Code/OrangeJuice2k` (template: read its README first). GitHub
repo `ChanOJ/OrangeJuiceV3` (public), hostname `orangejuicev3.sauthier.family`, bunny storage
and pull zone `orangejuicev3`, Proton Pass item `OrangeJuiceV3-tofu-state-passphrase` (vault
`OldSites`). Context of the whole id3/id3x family: `~/Code/id3x/CLAUDE.md`.

## Hard rules

- Inputs come only from the shared raw store `~/Code/id3x/raw` (setting `RAW` in
  `scripts/statify.py`, or `$ID3X_RAW`), never from the NAS. It holds the engine sources,
  connection strings and the full database (e-mails, passwords, IPs): never copy any of it into
  the repository, a commit, a PR or a page, except rendered public data.
- Never publish engine code (`Id3.*`, `Poliris.*` DLLs, `.cs`, `.aspx`, `.asp`), nor sensitive
  columns (password, logins, IPs, host names, departments, birth years, e-mail domains).
- `site/` is generated and git-ignored; `private/` (notes, Wayback captures) is git-ignored.

## Layout

- `scripts/id3render/engine.py`: the id3 template engine (port of `id3/cgi/_f.asp`).
- `scripts/id3render/oj.py`: the OJ data macros over SQLite (port of `id3x/macros/oj/*.cs`),
  `redact()` strips private columns before any template sees them.
- `scripts/statify.py`: crawl, static names, link rewriting, forms, assets, search indexes.
- `docs/plan.md`: inventory, decisions, order of work; `docs/data-model.md`: what is rendered.
- Checks: `scripts/verify_build.py`, `scripts/check_excludes.py`; build twice, same hashes.
