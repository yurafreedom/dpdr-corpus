"""Normalization over the whole works table (idempotent; safe to rerun any time).

- excluded_reason: 'burnout' (section 1) for DPDR-family records, 'time_ux'
  for time-only records. The burnout filter is NOT applied to time-only
  records: "workload" and "nurses" are legitimate in timing research.
- scope_flags (never excludes, only flags for review):
    burnout_soft      occupational vocabulary, no clinical DPDR anchor
    broad_only        matched only the broad Dissociative-Disorders MeSH / ketamine
                      families and never mentions depersonalization/derealization
    snowball_offtopic citation-hop record with no DPDR/time vocabulary
    retracted / notice
- citations: edges from every work's OpenAlex referenced_works.
"""
import json
import re

from harvest.filters import burnout_hits, exclusion_reason

DPDR_FAMILY = {"dpdr", "dissoc_mesh", "ketamine_dissoc", "ru", "de"}
ANCHOR = re.compile(r"dereali[sz]|depersonali[sz]ation[- ]derealis|depersonali[sz]ation disorder|\bDPDR\b|"
                    r"dissociat|cambridge depersonali|feelings? of unreality|деперсонал|дереал", re.I)
DPDR_WORD = re.compile(r"depersonali[sz]|dereali[sz]|unreality|деперсонал|дереал|Entfremdung", re.I)
SOFT = re.compile(r"personal accomplishment|compassion fatigue|health ?care (workers?|professionals?|staff)|"
                  r"\bphysicians?\b|\bresidents?\b|medical students?|occupational|work engagement|"
                  r"\bemployees?\b|\bstaff\b|\bworkplace|\bjob\b|organi[sz]ational", re.I)
# Server-side search (OpenAlex stemming, OpenAIRE description search) returns
# records that never contain the query terms; a family tag only counts when
# the text, keywords or MeSH really match.
DPDR_TERM = re.compile(r"depersonali[sz]|dereali[sz]|\bDPDR\b|feelings? of unreality|dissociative detachment|"
                       r"cortico-?limbic disconnection|деперсонал|дереал|психическая анестезия|"
                       r"anaesthesia psychica|Entfremdungserleben", re.I)
TIME_TERM = re.compile(r"time perception|temporal (experience|perception|disintegration|binding|processing)|"
                       r"subjective time|lived time|passage of time|duration (estimation|judg)|time estimation|"
                       r"interval timing|sensorimotor synchroni[sz]ation|intentional binding|experience of time|"
                       r"perception of time|Zeiterleben|восприяти\w* времени", re.I)
# The third (non-clinical) sense: "depersonalized data", "depersonalized bureaucracy".
CLINICAL = re.compile(r"psychiatr|psycholog|psychopath|patient|clinic|symptom|disorder|syndrome|dissociat|"
                      r"anxiety|depress|panic|trauma|PTSD|interocept|schizo|psychos|cannabis|ketamine|"
                      r"hallucino|psychedelic|drug|neuro|brain|DSM|ICD|treatment|therap|dereali[sz]|"
                      r"selfhood|sense of self|self-disorder|phenomenolog|conscious|mindful|vestibular|"
                      r"migraine|epilep|dizziness|insula|unreality|психиатр|психопатолог|Psychiatr|Patient", re.I)
RELEVANCE_MIN = 0.2  # see harvest/relevance.py
ANCHOR_STRICT = re.compile(r"depersonali[sz]ation[- /]+(derealis|derealiz)|depersonali[sz]ation disorder|\bDPDR\b|"
                           r"derealization disorder|cambridge depersonali", re.I)
TOPIC = re.compile(r"depersonali[sz]|dereali[sz]|dissociat|unreal|time perception|perception of time|"
                   r"temporal (experience|processing|perception|binding)|interval timing|"
                   r"duration (estimation|judg)|subjective time|passage of time|lived time|interocept|"
                   r"sense of (self|agency)|деперсонал", re.I)


def _l(v):
    return json.loads(v) if v else []


def apply_filters(conn):
    stats = {"burnout": 0, "time_ux": 0, "burnout_soft": 0, "broad_only": 0, "snowball_offtopic": 0, "no_term_match": 0, "nonclinical_usage": 0, "low_relevance": 0}
    if "dpdr_relevance" not in [c[1] for c in conn.execute("PRAGMA table_info(works)")]:
        conn.execute("ALTER TABLE works ADD COLUMN dpdr_relevance REAL")
    rows = conn.execute("SELECT id, pmid, title, abstract, keywords, mesh, query_tags, snowball_from, is_retracted, "
                        "dpdr_relevance FROM works").fetchall()
    for r in rows:
        tags = {t.split(":")[0] for t in _l(r["query_tags"])}
        kws = _l(r["keywords"])
        text = " ".join(x for x in (r["title"], r["abstract"], " ".join(k for k in kws if k)) if x)
        reason = None
        flags = []
        if tags & DPDR_FAMILY or not tags & {"time"}:
            hits = burnout_hits(r["title"], r["abstract"], kws)
            if hits:
                reason = "burnout"
            elif SOFT.search(text) and not ANCHOR.search(text) and "dpdr" in tags:
                flags.append("burnout_soft")
        if reason is None and "time" in tags and not tags & DPDR_FAMILY:
            if exclusion_reason(r["title"], r["abstract"], kws, time_query=True):
                reason = "time_ux"
        if tags and tags <= {"dissoc_mesh", "ketamine_dissoc"} and not DPDR_WORD.search(text):
            flags.append("broad_only")
        if r["snowball_from"] and not tags & (DPDR_FAMILY | {"time"}) and not TOPIC.search(text):
            flags.append("snowball_offtopic")
        mesh = " ".join(_l(r["mesh"]))
        term_ok = (("dpdr" in tags or tags & {"ru", "de"}) and (DPDR_TERM.search(text) or "Depersonalization" in mesh)) or \
                  ("time" in tags and (TIME_TERM.search(text) or "Time Perception" in mesh))
        if tags & {"dpdr", "time", "ru", "de"} and not term_ok and not flags:
            flags.append("no_term_match")
        elif term_ok and "dpdr" in tags and "time" not in tags and "Depersonalization" not in mesh \
                and len({m.group(0).lower() for m in CLINICAL.finditer(text)}) < (1 if r["pmid"] else 2) and not flags:
            flags.append("nonclinical_usage")
        if (not flags and reason is None and "dpdr" in tags and "time" not in tags
                and r["dpdr_relevance"] is not None and r["dpdr_relevance"] < RELEVANCE_MIN
                and not ANCHOR_STRICT.search(text) and "Depersonalization" not in mesh):
            flags.append("low_relevance")
        if r["is_retracted"] == 1:
            flags.append("retracted")
        elif r["is_retracted"] == 2:
            flags.append("notice")
        for f in flags:
            stats[f] = stats.get(f, 0) + 1
        if reason:
            stats[reason] += 1
        conn.execute("UPDATE works SET excluded_reason=?, scope_flags=? WHERE id=?",
                     (reason, json.dumps(flags) if flags else None, r["id"]))
    conn.commit()
    return stats


def build_citations(conn):
    n = 0
    for oid, refs in conn.execute("SELECT openalex_id, referenced_works FROM works "
                                  "WHERE openalex_id IS NOT NULL AND referenced_works IS NOT NULL"):
        for ref in _l(refs):
            conn.execute("INSERT OR IGNORE INTO citations VALUES(?,?,?)", (oid, ref, "openalex"))
            n += 1
    conn.commit()
    return n


def dedup_report(conn):
    """Residual duplicates by normalized title+year that survived id-based merging."""
    rows = conn.execute("""SELECT title_norm, year, COUNT(*) c FROM works WHERE title_norm IS NOT NULL
                           AND length(title_norm) > 30 GROUP BY title_norm, year HAVING c > 1""").fetchall()
    return {"residual_title_year_dupes": len(rows)}


def flag_trials_grants(conn):
    """Same burnout filter for trials and grants (title + abstract/conditions); flagged, not deleted."""
    out = {}
    for table, text_cols in (("trials", ("title", "conditions", "interventions")), ("grants", ("title", "abstract"))):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if "excluded_reason" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN excluded_reason TEXT")
        n = 0
        for r in conn.execute(f"SELECT id, {', '.join(text_cols)} FROM {table}").fetchall():
            hit = burnout_hits(r[1], " ".join(str(r[c]) for c in text_cols[1:] if r[c]))
            conn.execute(f"UPDATE {table} SET excluded_reason=? WHERE id=?", ("burnout" if hit else None, r["id"]))
            n += bool(hit)
        out[f"{table}_burnout"] = n
    conn.commit()
    return out


def run(conn):
    stats = apply_filters(conn)
    stats.update(flag_trials_grants(conn))
    stats["citation_edges_seen"] = build_citations(conn)
    stats.update(dedup_report(conn))
    stats["works_total"] = conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
    stats["works_in_scope"] = conn.execute(
        "SELECT COUNT(*) FROM works WHERE excluded_reason IS NULL AND scope_flags IS NULL").fetchone()[0]
    return stats
