"""Compare two MDF schemas and the ASP-expected schema: python3 -I diff.py A.MDF B.MDF OUT.txt"""
import difflib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mdf  # noqa: E402

# Columns the ASP code references (docs/schema_report.md section 2)
EXPECTED = {
    "news": "id dt id_people title url target smalltxt bigtxt active isarticle clicks category",
    "categories": "id catname urlcat",
    "people": "id nick groups id_pic uin interview id_country sex birthyear birthmd lastname firstname "
              "email homepage functions status department department_num id_computer scene irc_chans "
              "irc_net parties pref_party pref_demo pref_group pref_coder pref_zik pref_gfx pref_tart "
              "pref_movies pref_musics sex_size fun1 fun2 image_infos letter mailing password",
    "countries": "id country flag",
    "computers": "id computer",
    "oneliner": "id dt user_agent ip hostname sentence",
    "parties": "id id_people id_country dt_begin dt_end name place price url canceled active infofile results",
    "parties_modify": "id_party id_country dt_begin dt_end name place price url canceled infofile results id_people",
    "parties_coming": "id_people id_party",
    "groups": "id name id_country url letter active id_people dtadded",
    "groups_modify": "id_group name id_country url letter id_people",
    "pictures": "isactive dtadded isphoto filename filenameori width height nbcolors picmonth picyear "
                "who event place rank author description legend",
    "charts": "id_people id_chart id_item rank",
    "banners": "id picture alt border target_blank url width clicks",
    "width": "",
    "ix_servers": "id host path active isup id_country",
    "ix_paths": "id id_server path",
    "ix_files": "id id_path name size dt categories",
    "badurls_people": "", "badurls_groups": "", "badurls_parties": "",
}


def catalog(db):
    tabs = {}
    rows = {}
    for o in db.objects_of("U"):
        tabs[o["name"].lower()] = {c.name.lower(): c.decl() for c in db.columns.get(o["id"], [])}
        rows[o["name"].lower()] = db.count_rows(o["id"])[0]
    mods = {o["name"]: db.module_text(o["id"]) for o in db.objects_of("P", "V", "TR", "FN")
            if not o["name"].startswith("dt_")}
    return tabs, rows, mods


def main(pa, pb, out):
    A, B = mdf.Database(pa), mdf.Database(pb)
    ta, ra, ma = catalog(A)
    tb, rb, mb = catalog(B)
    L = ["A = %s" % pa, "B = %s" % pb, ""]
    L.append("Tables only in A: %s" % sorted(set(ta) - set(tb)))
    L.append("Tables only in B: %s" % sorted(set(tb) - set(ta)))
    L.append("")
    L.append("%-18s %10s %10s  column differences" % ("table", "rows A", "rows B"))
    for t in sorted(set(ta) | set(tb)):
        ca, cb = ta.get(t, {}), tb.get(t, {})
        d = []
        if t in ta and t in tb:
            if list(ca) != list(cb):
                d += ["+A:%s" % c for c in ca if c not in cb] + ["+B:%s" % c for c in cb if c not in ca]
                if not d:
                    d.append("column order differs")
            d += ["%s: A %s / B %s" % (c, ca[c], cb[c]) for c in ca if c in cb and ca[c] != cb[c]]
        L.append("%-18s %10s %10s  %s" % (t, ra.get(t, "-"), rb.get(t, "-"), "; ".join(d) or "same"))
    L.append("")
    L.append("Modules only in A: %s" % sorted(set(ma) - set(mb)))
    L.append("Modules only in B: %s" % sorted(set(mb) - set(ma)))
    changed = [m for m in sorted(set(ma) & set(mb)) if (ma[m] or "").strip() != (mb[m] or "").strip()]
    L.append("Modules with different text: %s" % changed)
    for m in changed:
        L += list(difflib.unified_diff((mb[m] or "").splitlines(), (ma[m] or "").splitlines(),
                                       "B/" + m, "A/" + m, lineterm=""))
    L.append("Unreadable (encrypted/missing) module text: A %s / B %s"
             % ([m for m, v in ma.items() if not v], [m for m, v in mb.items() if not v]))
    L.append("")
    L.append("ASP-expected schema (docs/schema_report.md) vs A:")
    for t, cols in EXPECTED.items():
        if t not in ta:
            L.append("  %-16s MISSING in A%s" % (t, "" if t not in tb else " (present in B)"))
            continue
        miss = [c for c in cols.split() if c not in ta[t]]
        extra = [c for c in ta[t] if c not in cols.split()] if cols else []
        L.append("  %-16s %s%s" % (t, "missing cols %s" % miss if miss else "all expected cols present",
                                   "; extra cols %s" % extra if extra else ""))
    L.append("  tables in A not referenced by the ASP report: %s"
             % sorted(t for t in ta if t not in EXPECTED))
    with open(out, "w") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main(*sys.argv[1:])
