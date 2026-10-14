"""OpenAlex title/abstract search per query family (10 credits per 200-record page).

Order is by value per credit: the DPDR union minus server-side burnout terms
first, then time perception and foreign-language families, then the
burnout-heavy complement last (it only feeds excluded_reason='burnout').
"""
from harvest import queries as Q
from harvest.common import load_status, save_status, log
from harvest.families import families, family_tag
from harvest.openalex import cursor_walk

YEAR_FILTER = "publication_year:<2027"


def plan():
    fams = dict(families())
    out = [("dpdr_clean", Q.openalex(fams["dpdr"], exclude_burnout=True), "dpdr")]
    for key, q in fams.items():
        if key.startswith("time") or key in ("ru", "de", "ketamine_dissoc"):
            v = Q.openalex(q)
            if v:
                out.append((key, v, family_tag(key)))
    burn = " OR ".join(f'"{t}"' for t in Q.BURNOUT_NOT["openalex"])
    out.append(("dpdr_burnout", f"({Q.openalex(fams['dpdr'])}) AND ({burn})", "dpdr"))
    return out


def run(conn):
    st = load_status("openalex_seed")
    summary = {}
    for key, value, tag in plan():
        if st.get(key, {}).get("done"):
            summary[key] = st[key]
            continue

        def on_page(page, count, key=key):
            st[key] = {"pages": page, "count": count, "done": False}
            save_status("openalex_seed", st)
            conn.commit()

        n, count = cursor_walk(conn, f"title_and_abstract.search:{value},{YEAR_FILTER}",
                               f"seed_{key}", 0.001, tags=[tag], on_page=on_page)
        st[key] = {"records": n, "count": count, "done": True}
        save_status("openalex_seed", st)
        conn.commit()
        summary[key] = st[key]
        log("openalex_seed", f"{key}: {n}/{count}")
    return summary
