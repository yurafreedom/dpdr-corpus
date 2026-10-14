"""Semantic Scholar Graph API batch: s2 id, tldr, influential citations, SPECTER v2 embeddings.

Keyless works on the shared pool (frequent 429s are handled by backoff);
S2_API_KEY raises the limit.
"""
import hashlib
import json

import config
from harvest.common import fetch_json, load_status, save_status, log
from harvest.scope import IN_SCOPE_SQL

API = "https://api.semanticscholar.org/graph/v1/paper/batch"
FIELDS = "paperId,externalIds,tldr,influentialCitationCount,citationCount,embedding.specter_v2"


def run(conn):
    st = load_status("semanticscholar")
    done = set(st.get("done", []))
    rows = conn.execute(f"""SELECT id, doi, pmid FROM works WHERE {IN_SCOPE_SQL}
                            AND (doi IS NOT NULL OR pmid IS NOT NULL)""").fetchall()
    # Negative examples for the relevance classifier (embeddings_clusters): a fixed
    # sample of records already judged out of scope by the rule-based filters.
    rows += conn.execute("""SELECT id, doi, pmid FROM works WHERE (doi IS NOT NULL OR pmid IS NOT NULL)
                            AND query_tags LIKE '%"dpdr"%' AND (excluded_reason='burnout'
                                 OR scope_flags LIKE '%nonclinical%' OR scope_flags LIKE '%no_term_match%')
                            ORDER BY (id * 2654435761) % 4294967296 LIMIT 6000""").fetchall()
    todo = [(r["id"], f"DOI:{r['doi']}" if r["doi"] else f"PMID:{r['pmid']}") for r in rows]
    todo = [t for t in todo if t[1] not in done]
    log("semanticscholar", f"{len(todo)} works")
    headers = {"x-api-key": config.KEYS["s2"]} if config.KEYS["s2"] else None
    got = 0
    for i in range(0, len(todo), 400):
        chunk = todo[i:i + 400]
        ids = [c[1] for c in chunk]
        h = hashlib.sha1(json.dumps(ids).encode()).hexdigest()[:14]
        res = fetch_json("s2", f"batch_{h}", API, {"fields": FIELDS}, method="POST",
                         json_body={"ids": ids}, headers=headers, max_tries=10)
        for (wid, _), p in zip(chunk, res):
            if not p:
                continue
            emb = (p.get("embedding") or {}).get("vector")
            conn.execute("""UPDATE works SET s2_id=COALESCE(s2_id, ?), tldr=COALESCE(?, tldr),
                            s2_influential=?, embedding=COALESCE(?, embedding) WHERE id=?""",
                         (p.get("paperId"), (p.get("tldr") or {}).get("text"), p.get("influentialCitationCount"),
                          json.dumps(emb) if emb else None, wid))
            got += 1
        done |= set(ids)
        st["done"] = sorted(done)
        save_status("semanticscholar", st)
        conn.commit()
        log("semanticscholar", f"{i + len(chunk)}/{len(todo)}")
    return {"matched": got, "requested": len(done)}
