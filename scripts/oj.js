/* Orange Juice v3 archive: skin chooser (default skin o), client-side search.
   Source: scripts/oj.js, copied to site/js/oj.js by scripts/statify.py (the skin list is filled in).
   No cookie: the chosen skin is kept in localStorage. No inline script needed (CSP friendly). */
(function () {
  "use strict";
  var SKINS = /*SKINS*/[];
  var DEFAULT = "o";
  var KEY = "oj_skin";

  function load(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } }
  function store(k, v) {
    try {
      if (v === null) { window.localStorage.removeItem(k); } else { window.localStorage.setItem(k, v); }
    } catch (e) { /* private mode, storage disabled: the choice is simply not remembered */ }
  }

  var m = window.location.pathname.match(/^\/skin\/([^\/]+)(\/.*)$/);
  var prefix = m ? "/skin/" + m[1] : "";
  var path = m ? m[2] : window.location.pathname;

  function skinPath(name, p) { return (name === DEFAULT ? "" : "/skin/" + name) + p; }
  function known(name) { return SKINS.indexOf(name) >= 0; }

  // Home page at the root only: the skin the visitor chose, if any (no redirect anywhere else,
  // new visitors always get the default skin).
  if (!m && (path === "/" || path === "/index.htm" || path === "/index.html")) {
    if (/[?&]skin=o\b/.test(window.location.search)) { store(KEY, null); }   // "full version" link of light
    var want = load(KEY);
    if (want && known(want) && want !== DEFAULT) { window.location.replace(skinPath(want, "/index.htm")); return; }
  }

  // Help popups ("INFO" buttons): common.js of 2006 only handled IE (document.all, falsy in
  // today's browsers) and Netscape 4; same placement rule, with the standard DOM.
  var mouse = { x: 0, y: 0 };
  document.addEventListener("mousemove", function (e) { mouse.x = e.clientX; mouse.y = e.clientY; });
  window.dhtmlpopup = function (id, mode, fx, fy, dx, dy) {
    var el = document.getElementById(id);
    if (!el) { return; }
    var ev = window.event;
    if (ev && ev.clientX != null) { mouse.x = ev.clientX; mouse.y = ev.clientY; }
    if (mode == 1 || mode == 2) {
      if (mode == 1) {
        el.style.position = "absolute";
        el.style.left = Math.max(0, mouse.x + window.pageXOffset - el.offsetWidth * (fx || 0) - (dx || 0)) + "px";
        el.style.top = Math.max(0, mouse.y + window.pageYOffset - el.offsetHeight * (fy || 0) - (dy || 0)) + "px";
      }
      el.style.visibility = "visible";
    } else {
      el.style.visibility = "hidden";
    }
  };

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function fold(s) {
    s = String(s || "").toLowerCase();
    return s.normalize ? s.normalize("NFD").replace(/[̀-ͯ]/g, "") : s;
  }
  function param(name) {
    var r = new RegExp("[?&]" + name + "=([^&]*)").exec(window.location.search);
    return r ? decodeURIComponent(r[1].replace(/\+/g, " ")) : "";
  }
  function ready(fn) {
    if (document.readyState !== "loading") { fn(); } else { document.addEventListener("DOMContentLoaded", fn); }
  }

  ready(function () {
    // Skin chooser (/skins.htm)
    var go = document.getElementById("oj-skin-go");
    if (go) {
      var chosen = load(KEY) || "";
      var radios = document.querySelectorAll('#oj-skins input[name="skin"]');
      for (var i = 0; i < radios.length; i++) { if (radios[i].value === chosen) { radios[i].checked = true; } }
      go.addEventListener("click", function () {
        var v = null;
        for (var j = 0; j < radios.length; j++) { if (radios[j].checked) { v = radios[j].value; } }
        if (v === null) { return; }
        store(KEY, v === "" || v === DEFAULT ? null : v);
        var target = v === "" ? DEFAULT : v;
        var back = "/index.htm";
        try {
          var ref = document.referrer ? new URL(document.referrer) : null;
          if (ref && ref.origin === window.location.origin) {
            var rp = ref.pathname.replace(/^\/skin\/[^\/]+/, "");
            if (rp !== "/skins.htm" && rp !== "/oj-search.htm" && rp !== "/") { back = rp; }
          }
        } catch (e) { /* old browser: back to the home page */ }
        window.location.href = skinPath(target, back);
      });
    }

    // Search fields: Enter or "go" opens the results page in the current skin
    // ("search in" box of every page: a keyword field and the type list of the same table)
    var boxes = Array.prototype.slice.call(document.querySelectorAll(".oj-search"))
      .concat(Array.prototype.slice.call(document.querySelectorAll("input[data-oj-kw]")));
    for (var b = 0; b < boxes.length; b++) {
      (function (box) {
        var kw = box.tagName === "INPUT";
        var scope = kw ? (box.closest ? box.closest("table") : null) || document : box;
        var input = kw ? box : box.querySelector('input[type="TEXT"], input[type="text"]');
        var button = kw ? null : box.querySelector('input[type="BUTTON"], input[type="button"]');
        var select = scope.querySelector("select.oj-type");
        if (!input) { return; }
        function run() {
          var q = input.value.replace(/^\s+|\s+$/g, "");
          if (!q) { return; }
          var t = select && select.value ? select.value : (box.getAttribute("data-t") || "news");
          window.location.href = prefix + "/oj-search.htm?t=" + encodeURIComponent(t) + "&q=" + encodeURIComponent(q);
        }
        input.addEventListener("keydown", function (e) { if (e.keyCode === 13) { e.preventDefault(); run(); } });
        if (button) { button.addEventListener("click", run); }
      })(boxes[b]);
    }

    // Results page: every index row is [url, title, subtitle, more text, thumbnail]
    var out = document.getElementById("oj-results");
    if (!out) { return; }
    var t = param("t") || "news";
    var q = param("q");
    var box = document.querySelector("#oj-box");
    if (box) {
      box.querySelector('input[type="TEXT"], input[type="text"]').value = q;
      var sel = box.querySelector("select.oj-type");
      if (sel) { sel.value = t; }
    }
    if (!q) { out.innerHTML = ""; return; }
    var file = { news: "news", pictures: "pictures", people: "people", groups: "groups", parties: "parties" }[t] || "news";
    var words = fold(q).split(/\s+/).filter(function (w) { return w; });
    out.innerHTML = "searching...";
    var xhr = new XMLHttpRequest();
    xhr.open("GET", "/idx/" + file + ".json");
    xhr.onload = function () {
      var rows = JSON.parse(xhr.responseText);
      var hits = rows.filter(function (r) {
        var hay = fold(r[1] + " " + r[2] + " " + r[3]);
        return words.every(function (w) { return hay.indexOf(w) >= 0; });
      });
      var max = 300, html = [];
      html.push("<P><FONT class=smtxt>" + hits.length + " result" + (hits.length === 1 ? "" : "s") +
                (hits.length > max ? " (first " + max + " shown)" : "") + "</FONT></P>");
      hits.slice(0, max).forEach(function (r) {
        var internal = r[0].charAt(0) === "/";
        var href = internal ? prefix + r[0] : r[0];
        var title = r[0] ? '<A href="' + esc(href) + '"' + (internal ? "" : ' target="_blank"') + ">" + esc(r[1]) + "</A>"
                         : esc(r[1]);
        var thumb = r[4] ? '<IMG src="' + esc(r[4]) + '" border=0 align=left hspace=4>' : "";
        html.push("<P>" + thumb + "<FONT class=title2>" + title + "</FONT>" +
                  (r[2] ? " <FONT class=smtxt>" + esc(r[2]) + "</FONT>" : "") +
                  (r[3] && file === "news" ? "<BR>" + esc(r[3]) : "") + '<BR clear=left></P>');
      });
      out.innerHTML = html.join("\n");
    };
    xhr.onerror = function () { out.innerHTML = "The search index could not be loaded."; };
    xhr.send();
  });
})();
