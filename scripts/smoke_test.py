#!/usr/bin/env python3
"""Check the public site: sample pages of every kind, legacy redirects, read-only hardening.

    python3 -I scripts/smoke_test.py               # CI (no site/): statuses, headers, redirects
    python3 -I scripts/smoke_test.py --sample 300  # on the Mac: also compares 300 random pages with site/

The checked pages and old URLs are fixed or read from build-meta/url-map.json (versioned), so the
test also runs in GitHub Actions, where site/ (git-ignored) does not exist. When site/ exists, each
fetched page is compared byte for byte with it. Retries for a while, since a deployment or purge
takes some time to reach every CDN edge. No secret needed.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # python3 -I leaves the script folder out
import bunny  # noqa: E402

ATTEMPTS = 6
DELAY = 20  # seconds between attempts
WORKERS = 16
UA = {"User-Agent": "smoke-test", "Cache-Control": "no-cache"}
NOT_FOUND_MARKER = b"not in the archive"  # hosting/404.html

# One page of each kind, plus the light skin: (path, expected content type prefix).
PAGES = [
    ("/", "text/html"),
    ("/index.htm", "text/html"),
    ("/index-p2.htm", "text/html"),
    ("/index-cat3.htm", "text/html"),
    ("/145/nick.htm", "text/html"),
    ("/_a/nicklist.htm", "text/html"),
    ("/groups/a/links.htm", "text/html"),
    ("/next/parties.htm", "text/html"),
    ("/past/parties.htm", "text/html"),
    ("/pics/piclib.htm", "text/html"),
    ("/birthdates.htm", "text/html"),
    ("/faq.htm", "text/html"),
    ("/skins.htm", "text/html"),
    ("/oj-search.htm", "text/html"),
    ("/archived.htm", "text/html"),
    ("/skin/light/index.htm", "text/html"),
    ("/skin/light/145/nick.htm", "text/html"),
    ("/js/oj.js", ""),
    ("/idx/news.json", "application/json"),
]

# Old URL -> expected Location (None: 404 with the custom page).
REDIRECTS = [
    ("/_A/nicklist.htm", "/_a/nicklist.htm"),
    ("/index.htm?OJ_NEWS_QRYpg=2", "/index-p2.htm"),
    ("/index.htm?idcat=3&OJ_NEWS_QRYpg=2", "/index-cat3-p2.htm"),
    ("/nicklist.htm?snick=chandra", "/oj-search.htm?t=people&q=chandra"),
    ("/login.htm?f=1", "/archived.htm"),
    ("/news/golink.htm?id=1&url=http%3a%2f%2fevil.example%2f", None),
    ("/no-such-page.htm", None),
]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def fetch(url: str, method: str = "GET", data: bytes | None = None) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, headers=UA, method=method, data=data)
    try:
        with OPENER.open(req, timeout=30) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()
    except OSError as e:
        return 0, {}, str(e).encode()


def url_map_samples() -> list[tuple[str, str]]:
    """First old query URL of each kind from build-meta/url-map.json (paging, groups)."""
    urls = json.loads((bunny.REPO_ROOT / "build-meta" / "url-map.json").read_text())["urls"]
    out = []
    for needle in ("/nicklist.htm?idgroup=", "/index.htm?idcat=", "/boo.htm?", "/piclib.htm?"):
        old = next((k for k in sorted(urls) if needle in k), None)
        if old:
            out.append((old, urls[old]))
    return out


def local_file(path: str) -> Path | None:
    rel = "index.html" if path == "/" else path.lstrip("/")
    p = bunny.SITE_DIR / rel
    return p if p.is_file() else None


def random_pages(n: int) -> list[tuple[str, str]]:
    if not n or not bunny.SITE_DIR.is_dir():
        return []
    every = [p for p in bunny.SITE_DIR.rglob("*.htm*") if p.is_file()]
    return [("/" + p.relative_to(bunny.SITE_DIR).as_posix(), "text/html")
            for p in random.sample(every, min(n, len(every)))]


def check(base: str, extra: list[tuple[str, str]]) -> list[str]:
    errors = []
    pages = PAGES + [(new, "text/html") for _old, new in url_map_samples()] + extra

    def verify_page(target: tuple[str, str]) -> str | None:
        path, ctype = target
        status, h, body = fetch(base + urllib.parse.quote(path))
        if status != 200:
            return f"{path}: HTTP {status}"
        if ctype and not h.get("content-type", "").startswith(ctype):
            return f"{path}: Content-Type {h.get('content-type')!r}, expected {ctype}"
        if path.endswith((".html", ".htm")) and "iso-8859-1" not in h.get("content-type", "").lower():
            return f"{path}: Content-Type {h.get('content-type')!r} lacks charset iso-8859-1"
        local = local_file(path)
        if local is not None and body != local.read_bytes():
            return f"{path}: content differs from site/{local.relative_to(bunny.SITE_DIR)}"
        return None

    def verify_redirect(target: tuple[str, str | None]) -> str | None:
        old, want = target
        status, h, body = fetch(base + old)
        if want is None:
            if status != 404 or NOT_FOUND_MARKER not in body:
                return f"{old}: HTTP {status}, expected 404 with the custom page"
            return None
        loc = urllib.parse.urlsplit(h.get("location", ""))
        got = loc.path + ("?" + loc.query if loc.query else "")
        if status != 301 or got != want:
            return f"{old}: HTTP {status} Location {h.get('location')!r}, expected 301 {want}"
        return None

    with ThreadPoolExecutor(WORKERS) as pool:
        errors += [e for e in pool.map(verify_page, pages) if e]
        errors += [e for e in pool.map(verify_redirect, REDIRECTS + url_map_samples()) if e]
    errors += check_hardening(base)
    return errors


def check_hardening(base: str) -> list[str]:
    """The site must stay read-only: no write method, security headers, custom 404."""
    errors = []
    status, _h, _b = fetch(base + "/", method="POST", data=b"x=1")
    if status not in (403, 405):
        errors.append(f"POST /: HTTP {status}, expected 403/405")
    status, h, _b = fetch(base + "/index.htm")
    for name in ("content-security-policy", "x-content-type-options", "x-frame-options", "referrer-policy",
                 "strict-transport-security", "cross-origin-opener-policy"):
        if name not in h:
            errors.append(f"/index.htm: missing header {name}")
    if "form-action 'none'" not in h.get("content-security-policy", ""):
        errors.append("/index.htm: CSP lacks form-action 'none'")
    if h.get("x-content-type-options", "").lower() != "nosniff":
        errors.append("/index.htm: X-Content-Type-Options is not nosniff")
    status, _h, body = fetch(base + "/no-such-page-404-check.html")
    if status != 404:
        errors.append(f"missing page: HTTP {status}, expected 404")
    elif NOT_FOUND_MARKER not in body:
        errors.append("missing page: custom 404 page not served")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sample", type=int, default=0, help="also fetch N random pages of site/ and compare them")
    args = ap.parse_args()
    base = "https://" + bunny.site_config()["hostname"]
    extra = random_pages(args.sample)  # the same sample on every attempt
    for attempt in range(1, ATTEMPTS + 1):
        errors = check(base, extra)
        if not errors:
            print(f"{base}: sample pages, legacy redirects and hardening OK"
                  + (" (pages compared with site/)" if bunny.SITE_DIR.is_dir() else "") + ".")
            return 0
        print(f"Attempt {attempt}/{ATTEMPTS}: {len(errors)} problem(s), e.g. {errors[0]}", flush=True)
        if attempt < ATTEMPTS:
            time.sleep(DELAY)
    print("\n".join(errors))
    return 1


if __name__ == "__main__":
    sys.exit(main())
