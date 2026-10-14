"""Unpaywall for every core DOI. Requires CONTACT_EMAIL (Unpaywall rejects requests without a real address).

Stores every PDF link Unpaywall lists (best location first, then the other OA
locations) in works.unpaywall_pdfs; the fulltext module tries each one it has
not tried before. works.oa_url is filled only where it was empty.
"""
import json
from urllib.parse import quote

import config
from harvest.common import SkipModule, fetch_json, log
from harvest.scope import CORE_SQL


def run(conn):
    if not config.CONTACT_EMAIL:
        raise SkipModule("needs CONTACT_EMAIL in .env (Unpaywall requires a real contact address)")
    if "unpaywall_pdfs" not in [c[1] for c in conn.execute("PRAGMA table_info(works)")]:
        conn.execute("ALTER TABLE works ADD COLUMN unpaywall_pdfs TEXT")
    rows = conn.execute(f"SELECT id, doi, oa_url FROM works WHERE doi IS NOT NULL AND {CORE_SQL}").fetchall()
    found = with_pdf = 0
    for n, r in enumerate(rows):
        d = fetch_json("unpaywall", f"doi_{r['doi']}", f"https://api.unpaywall.org/v2/{quote(r['doi'])}",
                       {"email": config.CONTACT_EMAIL}, ok_statuses=(200, 404))
        best = d.get("best_oa_location") or {}
        locs = [best] + [x for x in d.get("oa_locations") or [] if x and x != best]
        pdfs = list(dict.fromkeys(x["url_for_pdf"] for x in locs if x and x.get("url_for_pdf")))
        if best:
            found += 1
            conn.execute("""UPDATE works SET oa_url=COALESCE(oa_url, ?), oa_status=COALESCE(oa_status, ?),
                            license=COALESCE(license, ?) WHERE id=?""",
                         (best.get("url_for_pdf") or best.get("url"), d.get("oa_status"), best.get("license"), r["id"]))
        if pdfs:
            with_pdf += 1
            conn.execute("UPDATE works SET unpaywall_pdfs=? WHERE id=?", (json.dumps(pdfs), r["id"]))
        if n % 200 == 0:
            conn.commit()
            log("unpaywall", f"{n}/{len(rows)} oa={found} pdf_links={with_pdf}")
    conn.commit()
    return {"checked": len(rows), "oa_found": found, "with_pdf_links": with_pdf}
