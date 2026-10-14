"""One citation hop (spec stage 2) from the top-150 most-cited in-scope works and
all in-scope 2022-2026 works: backward via referenced_works (batched, 100 per
credit) and forward via filter=cites:W (200 per credit).

Snowballed works get tags snowball_ref / snowball_cite and snowball_from; the
normalize step flags the ones that never mention a DPDR/time term.
"""
import json

from harvest.common import load_status, save_status, log
from harvest.openalex import batch_lookup, cursor_walk

SEED_SQL = """
SELECT id, openalex_id, referenced_works, cited_by_count, year FROM works
WHERE openalex_id IS NOT NULL AND excluded_reason IS NULL
  AND (query_tags LIKE '%"dpdr"%' OR query_tags LIKE '%"time"%')
  AND (snowball_from IS NULL)
"""


def seeds(conn):
    rows = conn.execute(SEED_SQL).fetchall()
    top = sorted(rows, key=lambda r: -(r["cited_by_count"] or 0))[:150]
    recent = [r for r in rows if r["year"] and 2022 <= r["year"] <= 2026]
    by_id = {r["openalex_id"]: r for r in top + recent}
    return list(by_id.values())


def run(conn):
    from harvest import normalize  # make sure excluded_reason is current before choosing seeds
    normalize.apply_filters(conn)
    st = load_status("openalex_snowball")
    seed_rows = seeds(conn)
    log("openalex_snowball", f"{len(seed_rows)} seeds")
    known = {r[0] for r in conn.execute("SELECT openalex_id FROM works WHERE openalex_id IS NOT NULL")}

    # Backward: references of all seeds, fetched in batches.
    refs = {}
    for r in seed_rows:
        for ref in json.loads(r["referenced_works"] or "[]"):
            refs.setdefault(ref, r["openalex_id"])
            conn.execute("INSERT OR IGNORE INTO citations VALUES(?,?,?)", (r["openalex_id"], ref, "openalex"))
    todo = sorted(set(refs) - known - set(st.get("refs_done", [])))
    log("openalex_snowball", f"backward: {len(refs)} refs, {len(todo)} new")
    done = set(st.get("refs_done", []))
    for i in range(0, len(todo), 100):
        chunk = todo[i:i + 100]
        batch_lookup(conn, "openalex", chunk, tags=["snowball_ref"],
                     snowball_from="ref", key_prefix="snowball_ref")
        done |= set(chunk)
        st["refs_done"] = sorted(done)
        save_status("openalex_snowball", st)
        conn.commit()

    # Forward: works citing each seed.
    fwd_done = set(st.get("cites_done", []))
    for n, r in enumerate(seed_rows):
        oid = r["openalex_id"]
        if oid in fwd_done or not r["cited_by_count"]:
            continue
        cursor_walk(conn, f"cites:{oid}", f"snowball_cites_{oid}", 0.0001,
                    tags=["snowball_cite"], snowball_from=f"cites:{oid}")
        # Edges come from each citing work's referenced_works (normalize.build_citations).
        fwd_done.add(oid)
        st["cites_done"] = sorted(fwd_done)
        save_status("openalex_snowball", st)
        conn.commit()
        if n % 25 == 0:
            log("openalex_snowball", f"forward {n}/{len(seed_rows)}")
    return {"seeds": len(seed_rows), "refs": len(refs), "refs_fetched": len(done), "cites_walked": len(fwd_done)}
