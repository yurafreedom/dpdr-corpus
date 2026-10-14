"""OpenCitations Index v2: open citation links into the 500 most-cited core works (by DOI)."""
from harvest.common import fetch_json, load_status, save_status, log
from harvest.scope import CORE_SQL

API = "https://api.opencitations.net/index/v2/citations/doi:"
LIMIT = 500


def run(conn):
    st = load_status("opencitations")
    done = set(st.get("done", []))
    rows = conn.execute(f"""SELECT doi FROM works WHERE doi IS NOT NULL AND {CORE_SQL}
                            ORDER BY cited_by_count DESC LIMIT {LIMIT}""").fetchall()
    edges = 0
    failed = []
    for n, (doi,) in enumerate(rows):
        if doi in done:
            continue
        try:
            d = fetch_json("opencitations", f"citations_{doi}", API + doi, ok_statuses=(200,))
        except Exception as e:  # very large citation lists sometimes break mid-transfer; retried next run
            failed.append(doi)
            log("opencitations", f"{doi}: {type(e).__name__}")
            continue
        for c in d:
            citing = next((x[4:] for x in c.get("citing", "").split() if x.startswith("doi:")), None)
            if citing:
                conn.execute("INSERT OR IGNORE INTO citations VALUES(?,?,?)", (f"doi:{citing}", f"doi:{doi}", "opencitations"))
                edges += 1
        done.add(doi)
        if n % 50 == 0:
            st["done"] = sorted(done)
            save_status("opencitations", st)
            conn.commit()
            log("opencitations", f"{n}/{len(rows)}")
    st["done"] = sorted(done)
    save_status("opencitations", st)
    return {"dois": len(done), "edges_this_run": edges, "failed": failed}
