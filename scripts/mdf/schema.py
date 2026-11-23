"""Dump schema + row counts of a SQL Server 2000 MDF: python3 -I schema.py FILE.MDF OUT.txt [OUT.json]"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mdf  # noqa: E402


def main(path, out, out_json=None):
    db = mdf.Database(path)
    rowcnt = {ix["id"]: ix["rowcnt"] for ix in db.indexes if ix["indid"] in (0, 1)}
    lines = ["# %s" % path, "pages: %d" % db.f.page_count, ""]
    summary = {"tables": {}, "procs": [], "views": [], "news": None}
    for o in db.objects_of("U"):
        oid = o["id"]
        live, ghost, stubs, _ = db.count_rows(oid)
        scan_live = db.count_rows(oid, use_iam=False)[0]
        cols = [c for c in db.columns.get(oid, [])]
        lines.append("TABLE %s  (id %d, created %s)  rows: iam=%d ghost=%d fwd_stubs=%d "
                     "header_scan=%d sysindexes.rowcnt=%s"
                     % (o["name"], oid, o["crdate"], live, ghost, stubs, scan_live, rowcnt.get(oid)))
        for c in cols:
            lines.append("    %-28s %-18s %s%s" % (c.name, c.decl(), "NULL" if c.nullable else "NOT NULL",
                                                 "  [computed]" if c.computed else ""))
        lines.append("")
        summary["tables"][o["name"].lower()] = {
            "rows": live, "cols": [(c.name.lower(), c.decl()) for c in cols]}
    for label, xt, key in (("PROCEDURES", "P", "procs"), ("VIEWS", "V", "views"),
                           ("TRIGGERS", "TR", None), ("FUNCTIONS", "FN", None)):
        objs = [o for o in db.objects_of(xt) if not o["name"].startswith("dt_")]
        lines.append("%s (%d, dt_* tooling excluded):" % (label, len(objs)))
        for o in objs:
            lines.append("    " + o["name"])
        if key:
            summary[key] = [o["name"] for o in objs]
        lines.append("")

    # news date/id range
    news = next((o for o in db.objects_of("U") if o["name"].lower() == "news"), None)
    if news:
        rows = list(db.scan_object(news["id"]))
        ids = [r["id"] for r in rows if r.get("id") is not None]
        dts = [r["dt"] for r in rows if r.get("dt") is not None]
        act = [r for r in rows if r.get("active") == 1]
        info = dict(n=len(rows), id_min=min(ids), id_max=max(ids), dt_min=str(min(dts)),
                    dt_max=str(max(dts)), active=len(act),
                    active_dt_max=str(max(r["dt"] for r in act if r.get("dt"))) if act else None)
        summary["news"] = info
        lines.append("NEWS range: %s" % info)
        lines.append("")

    # stored procedure text
    sp = next((o for o in db.objects_of("P") if o["name"] == "oj_listnews"), None)
    if sp:
        lines.append("syscomments text of oj_listnews:")
        lines.append(db.module_text(sp["id"]) or "(none)")
    with open(out, "w") as f:
        f.write("\n".join(lines) + "\n")
    if out_json:
        with open(out_json, "w") as f:
            json.dump(summary, f, indent=1, default=str)


if __name__ == "__main__":
    main(*sys.argv[1:])
