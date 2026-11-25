// Test of the legacy redirects of infra/edge/redirects.js (Node >= 18, no dependency):
//   node scripts/test_edge_script.mjs
// Loads the pure resolve() part of the script (between the BEGIN/END resolve markers), then checks
// every old query URL of build-meta/url-map.json, edge cases, and (when site/ exists) that every
// redirect target is a file of the build.
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(path.join(repo, "infra/edge/redirects.js"), "utf8");
const m = /\/\/ BEGIN resolve[^\n]*\n([\s\S]*)\/\/ END resolve/.exec(src);
if (!m) throw new Error("BEGIN/END resolve markers not found");
const resolve = new Function(m[1] + "\nreturn resolve;")();
const map = JSON.parse(readFileSync(path.join(repo, "build-meta/url-map.json"), "utf8"));
const site = path.join(repo, "site");
const hasSite = existsSync(path.join(site, "index.htm"));

let failures = 0;
const fail = (msg) => { failures++; if (failures <= 30) console.log("FAIL " + msg); };

function run(old) {
  const u = new URL(old, "https://example.test");
  return resolve(u.pathname, u.search);
}

function expect(old, want) {
  const got = run(old);
  const ok = want === null ? got === null
    : want === 404 ? got && got.status === 404
    : got && got.status === 301 && got.location === want;
  if (!ok) fail(`${old}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}

function exists(loc) {
  const p = decodeURIComponent(loc.split("?")[0]);
  return existsSync(path.join(site, p.endsWith("/") ? p + "index.html" : p));
}

// 1. Every old query URL of the build.
let n = 0;
for (const [old, want] of Object.entries(map.urls)) {
  n++;
  expect(old, want);
  if (hasSite && !exists(want)) fail(`${old}: target ${want} missing from site/`);
}

// 2. Edge cases.
const cases = [
  // static paths, pictures and the archive's own query URLs are never touched
  ["/", null], ["/index.htm", null], ["/16139/nick.htm", null], ["/z/o/p/1_A.gif", null],
  ["/js/oj.js?v=abc", null], ["/idx/news.json", null], ["/oj-search.htm?t=news&q=party", null],
  ["/index.htm?skin=o", null], ["/skin/light/index.htm", null], ["/skin/light/", null],
  // capitals (IIS was case-insensitive)
  ["/_A/nicklist.htm", "/_a/nicklist.htm"], ["/groups/L/links.htm", "/groups/l/links.htm"],
  ["/skin/light/_B/nicklist.htm", "/skin/light/_b/nicklist.htm"],
  // content parameters, in any case and order, extra and empty ones dropped
  ["/index.htm?idcat=3&OJ_NEWS_QRYpg=2", "/index-cat3-p2.htm"],
  ["/index.htm?OJ_NEWS_QRYpg=2&idcat=3&sn=", "/index-cat3-p2.htm"],
  ["/index.htm?oj_news_qrypg=1", "/index.htm"], ["/nicklist.htm?idgroup=12", "/nicklist-group12.htm"],
  ["/16139/nick.htm?x=1", "/16139/nick.htm"], ["/groups/a/links.htm?edit=1&OJ_LINKS_REPpg=4", "/groups/a/links-p4.htm"],
  // searches, counters, removed pages
  ["/nicklist.htm?snick=chandra", "/oj-search.htm?t=people&q=chandra"],
  ["/index.htm?idcat=1&sn=party%20time", "/oj-search.htm?t=news&q=party%20time"],
  ["/ftpsearch.htm?sftp=foo", "/archived.htm"], ["/login.htm?f=1", "/archived.htm"],
  ["/news/golink.htm?id=1&url=%2f1234%2farticle.htm", "/1234/article.htm"],
  ["/news/golink.htm?id=1&url=http%3a%2f%2fwww.ojuice.net%2f_A%2fnicklist.htm", "/_a/nicklist.htm"],
  ["/news/golink.htm?id=1&url=http%3a%2f%2fevil.example%2f", 404], ["/news/golink.htm?id=1", 404],
  // open redirect: a target path starting with // or /\ must not become a protocol-relative Location
  ["/golink.htm?url=http://www.ojuice.net//example.org/x", 404], ["/golink.htm?url=http://www.ojuice.net/\\example.org/x", 404],
  ["/golink.htm?url=http://www.ojuice.net/%5Cexample.org/x", 404], ["/golink.htm?url=http://www.ojuice.net///x", 404],
  ["/golink.htm?url=http://www.ojuice.net%2F%2Fexample.org/x", 404],
  ["/golink.htm?url=http://www.ojuice.net/ok.htm", "/ok.htm"],
  ["/skin/light/login.htm?f=1", "/skin/light/archived.htm"],
];
for (const [old, want] of cases) expect(old, want);

console.log(`${n} url-map entries and ${cases.length} edge cases checked${hasSite ? " (targets in site/)" : ""}: ` +
            (failures ? `${failures} failure(s)` : "OK"));
process.exit(failures ? 1 : 0);
