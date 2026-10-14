"""Attach OpenAlex records to works found via PubMed / Europe PMC / other sources.

Batches of 100 PMIDs or DOIs per 1-credit request. Misses are remembered in
status/openalex_lookup.json so they are not re-requested.
"""
from harvest.common import load_status, save_status, log
from harvest.openalex import batch_lookup


def run(conn):
    st = load_status("openalex_lookup")
    tried = {"pmid": set(st.get("pmid", [])), "doi": set(st.get("doi", []))}
    found = 0
    for field, col, fmt in (("ids.pmid", "pmid", str), ("doi", "doi", str)):
        rows = [r[0] for r in conn.execute(
            f"SELECT {col} FROM works WHERE openalex_id IS NULL AND {col} IS NOT NULL")]
        todo = [v for v in rows if v not in tried[col] and "|" not in v and "," not in v]
        log("openalex_lookup", f"{col}: {len(todo)} to look up")
        for i in range(0, len(todo), 100):
            chunk = todo[i:i + 100]
            try:
                found += len(batch_lookup(conn, field, chunk))
            finally:
                tried[col] |= set(chunk)
                conn.commit()
                save_status("openalex_lookup", {k: sorted(v) for k, v in tried.items()})
            if (i // 100) % 20 == 0:
                log("openalex_lookup", f"{col} {i + len(chunk)}/{len(todo)}, matched {found}")
    return {"matched": found, "tried_pmid": len(tried["pmid"]), "tried_doi": len(tried["doi"])}
