"""People and institutions (spec section 7).

Seed list + the most active corpus authors 2022-2026. For each: OpenAlex
author profile (single GET = 0 credits): works_count, counts_by_year,
last_known_institutions, year-by-year affiliations; ORCID public API
employments as an independent check of "where they are now"; Semantic Scholar
author search for the seed list. Channels and the clinician flag are manual.
"""
import json
import re
import time
from urllib.parse import quote

import config
import db
from harvest.common import BudgetExhausted, fetch_json, log
from harvest.openalex import get
from harvest.scope import core_sql

SEEDS = ["Mauricio Sierra", "Anthony David", "Elaine Hunter", "Nick Medford", "Daphne Simeon",
         "Antonio Mantovani", "Matthias Michal", "Fugen Neziroglu", "Anna Ciaunica", "Anil Seth",
         "Hugo Critchley", "Philip Gerrans", "Erwin Lemche", "Orna Guralnik", "Marc Wittmann",
         "Sylvie Droit-Volet", "Georg Northoff", "Thomas Fuchs", "Kai Vogeley", "Giovanni Stanghellini",
         "Virginie van Wassenhove", "Devin Terhune", "Ruth Ogden", "Dan Zakay", "Jennifer Coull",
         "Hugo Merchant"]
EXTRA_ACTIVE = 60  # most active non-seed core authors 2022-2026
ORCID = "https://pub.orcid.org/v3.0/"


def _n(s):
    return re.sub(r"[^a-z]", "", db.norm_title(s or "") or "")


def match_seed(conn, name):
    first, last = name.split()[0], name.split()[-1]
    rows = conn.execute(f"""
        SELECT a.openalex_id, a.display_name, COUNT(DISTINCT s.work_id) n
        FROM authors a JOIN authorships s ON s.author_id=a.id JOIN works w ON w.id=s.work_id
        WHERE a.display_name LIKE ? AND {core_sql('w')}
        GROUP BY a.id ORDER BY n DESC""", (f"%{last}%",)).fetchall()
    for r in rows:
        parts = r["display_name"].split()
        if _n(parts[-1]) == _n(last) and parts and _n(parts[0])[:1] == _n(first)[:1]:
            return r["openalex_id"], [x["openalex_id"] for x in rows if x["openalex_id"] != r["openalex_id"]
                                      and _n(x["display_name"].split()[-1]) == _n(last)]
    return None, []


def orcid_search(name):
    first, last = name.split()[0], name.split()[-1]
    q = f"family-name:{last} AND given-names:{first}"
    d = fetch_json("orcid", f"search_{last}_{first}", ORCID + "search/", {"q": q, "rows": 5},
                   headers={"Accept": "application/json"})
    res = d.get("result") or []
    return res[0]["orcid-identifier"]["path"] if len(res) == 1 else None


def orcid_employment(orcid):
    d = fetch_json("orcid", f"employments_{orcid}", ORCID + f"{orcid}/employments",
                   headers={"Accept": "application/json"}, ok_statuses=(200, 404))
    out = []
    for g in d.get("affiliation-group") or []:
        for s in g.get("summaries") or []:
            e = s.get("employment-summary") or {}
            org = (e.get("organization") or {})
            out.append({"org": org.get("name"), "country": (org.get("address") or {}).get("country"),
                        "role": e.get("role-title"),
                        "start": ((e.get("start-date") or {}).get("year") or {}).get("value"),
                        "end": ((e.get("end-date") or {}).get("year") or {}).get("value") if e.get("end-date") else None})
    return out


def s2_author(name):
    try:
        d = fetch_json("s2", f"author_search_{name}", "https://api.semanticscholar.org/graph/v1/author/search",
                       {"query": name, "fields": "name,paperCount,hIndex,affiliations", "limit": 5},
                       headers={"x-api-key": config.KEYS["s2"]} if config.KEYS["s2"] else None, max_tries=8)
    except Exception:
        return None
    data = sorted(d.get("data") or [], key=lambda a: -(a.get("paperCount") or 0))
    return data[0]["authorId"] if data else None


def corpus_stats(conn, oid):
    w = conn.execute("""SELECT w.id, w.title, w.year, w.cited_by_count FROM works w
                        JOIN authorships s ON s.work_id=w.id JOIN authors a ON a.id=s.author_id
                        WHERE a.openalex_id=? AND w.excluded_reason IS NULL ORDER BY w.cited_by_count DESC""",
                     (oid,)).fetchall()
    co = conn.execute("""SELECT a2.display_name, COUNT(*) n FROM authorships s1
                         JOIN authors a1 ON a1.id=s1.author_id AND a1.openalex_id=?
                         JOIN authorships s2 ON s2.work_id=s1.work_id AND s2.author_id<>s1.author_id
                         JOIN authors a2 ON a2.id=s2.author_id
                         GROUP BY a2.id ORDER BY n DESC LIMIT 8""", (oid,)).fetchall()
    top5 = [f"{r['year']} {r['title']} ({r['cited_by_count']})" for r in w[:5]]
    return len(w), top5, [f"{r[0]} ({r[1]})" for r in co]


def active_authors(conn, exclude):
    rows = conn.execute(f"""
        SELECT a.openalex_id, a.display_name, COUNT(DISTINCT w.id) n FROM authors a
        JOIN authorships s ON s.author_id=a.id JOIN works w ON w.id=s.work_id
        WHERE w.year BETWEEN 2022 AND 2026 AND {core_sql('w')}
        GROUP BY a.id HAVING n >= 2 ORDER BY n DESC LIMIT ?""", (EXTRA_ACTIVE + len(exclude),)).fetchall()
    return [(r["display_name"], r["openalex_id"]) for r in rows if r["openalex_id"] not in exclude][:EXTRA_ACTIVE]


def profile(conn, name, oid, seed, alternates=(), orcid=None):
    today = time.strftime("%Y-%m-%d")
    a = get(f"authors/{oid}", f"author_{oid}", 0) if oid else {}
    orcid = orcid or ((a.get("orcid") or "").rsplit("/", 1)[-1] or None)
    if not orcid and seed:
        orcid = orcid_search(name)
    if orcid and not oid:
        try:
            d = get("authors", f"author_by_orcid_{orcid}", 0.0001, filter=f"orcid:{orcid}")
        except BudgetExhausted:
            d = {}  # picked up on the next run once the OpenAlex budget resets
        if d.get("results"):
            a = d["results"][0]
            oid = db.short_oa(a["id"])
    lki = (a.get("last_known_institutions") or [{}])
    inst = lki[0] if lki else {}
    cby = {c["year"]: c["works_count"] for c in a.get("counts_by_year") or []}
    emp = orcid_employment(orcid) if orcid else []
    current_emp = [e for e in emp if not e["end"]]
    affs = [{"inst": (x.get("institution") or {}).get("display_name"), "years": x.get("years")}
            for x in a.get("affiliations") or []]
    if oid:
        db.upsert_author(conn, {"openalex_id": oid, "display_name": a.get("display_name") or name, "orcid": orcid,
                                "last_institution": inst.get("display_name"),
                                "last_institution_date": today, "country": inst.get("country_code"),
                                "works_count": a.get("works_count"), "cited_by_count": a.get("cited_by_count"),
                                "counts_by_year": cby, "affiliations": affs})
    n_corpus, top5, co = corpus_stats(conn, oid) if oid else (0, [], [])
    lki_names = [i.get("display_name") for i in lki if i.get("display_name")]
    review = []
    if not oid:
        review.append("no OpenAlex author resolved")
    if oid and not sum(v for y, v in cby.items() if 2022 <= y <= 2026):
        review.append("no 2022-2026 works: possibly a split or wrong OpenAlex identity")
    if seed and n_corpus < 3:
        review.append(f"only {n_corpus} corpus works: check the identity")
    if seed and alternates:
        review.append(f"{len(alternates)} other OpenAlex ids with the same name in the corpus")
    if current_emp and lki_names:
        toks = lambda x: set(re.findall(r"[a-z]{4,}", (x or "").lower())) - {"university", "universit", "institute"}  # noqa: E731
        if not any(toks(e["org"]) & toks(n) for e in current_emp for n in lki_names):
            review.append("OpenAlex and ORCID current employers disagree")
    db.upsert_person(conn, {
        "name": name, "openalex_id": oid, "orcid": orcid, "s2_id": s2_author(name) if seed else None,
        "works_total": a.get("works_count"),
        "works_2022_2026": sum(v for y, v in cby.items() if 2022 <= y <= 2026),
        "current_institution": "; ".join(lki_names) or None,
        "institution_source": "openalex.last_known_institutions" + (
            f"; orcid employment: {', '.join(e['org'] for e in current_emp)}" if current_emp else ""),
        "institution_verified": today, "country": inst.get("country_code"),
        "top5_works": top5, "coauthors": co, "channels": None, "clinician": None, "seed": int(seed),
        "corpus_works": n_corpus,
        "orcid_employment": {"all": emp, "alternate_openalex_ids": list(alternates)},
        "needs_review": "; ".join(review) or None,
    })


def run(conn):
    done = []
    seed_ids = set()
    for name in SEEDS:
        oid, alts = match_seed(conn, name)
        profile(conn, name, oid, True, alts)
        if oid:
            seed_ids.add(oid)
        done.append(name)
        conn.commit()
        log("people", f"{name}: {oid}")
    extra = active_authors(conn, seed_ids)
    for name, oid in extra:
        profile(conn, name, oid, False)
        conn.commit()
    return {"seeds": len(SEEDS), "seeds_matched_in_corpus": len(seed_ids), "active_added": len(extra)}
