"""Embedding-based relevance score for DPDR-tagged works (works.dpdr_relevance).

Logistic regression on SPECTER v2 embeddings. Positives: unambiguous clinical
DPDR papers (MeSH Depersonalization, or depersonalization(-derealization)
disorder / DPDR / Cambridge Depersonalisation Scale in title or abstract).
Negatives: records the rule-based filters already put out of scope (burnout,
non-clinical usage, no term match). normalize flags DPDR-only records below
THRESHOLD as low_relevance; nothing is deleted.
"""
import json
import re

import numpy as np

from harvest.common import save_status, log

THRESHOLD = 0.2
ANCHOR = re.compile(r"depersonali[sz]ation[- /]+(derealis|derealiz)|depersonali[sz]ation disorder|\bDPDR\b|"
                    r"derealization disorder|cambridge depersonali", re.I)


def is_anchor(r):
    return not r["excluded_reason"] and (ANCHOR.search(f"{r['title'] or ''} {r['abstract'] or ''}")
                                         or "Depersonalization" in (r["mesh"] or ""))


def run(conn):
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score
    if "dpdr_relevance" not in [r[1] for r in conn.execute("PRAGMA table_info(works)")]:
        conn.execute("ALTER TABLE works ADD COLUMN dpdr_relevance REAL")
    rows = conn.execute("""SELECT id, title, abstract, mesh, excluded_reason, scope_flags, embedding FROM works
                           WHERE query_tags LIKE '%"dpdr"%' AND embedding IS NOT NULL""").fetchall()
    pos = [r for r in rows if is_anchor(r)]
    neg = [r for r in rows if r["excluded_reason"] == "burnout" or any(
        f in (r["scope_flags"] or "") for f in ("nonclinical", "no_term_match"))]
    if len(pos) < 100 or len(neg) < 100:
        return {"skipped": f"too few examples (pos {len(pos)}, neg {len(neg)})"}
    emb = lambda rs: np.array([json.loads(r["embedding"]) for r in rs], dtype=np.float32)  # noqa: E731
    X, y = np.vstack([emb(pos), emb(neg)]), np.array([1] * len(pos) + [0] * len(neg))
    clf = LogisticRegression(C=1.0, max_iter=2000, class_weight="balanced")
    auc = float(cross_val_score(clf, X, y, cv=5, scoring="roc_auc").mean())
    clf.fit(X, y)
    p = clf.predict_proba(emb(rows))[:, 1]
    for r, pr in zip(rows, p):
        conn.execute("UPDATE works SET dpdr_relevance=? WHERE id=?", (round(float(pr), 4), r["id"]))
    conn.commit()
    from harvest import normalize
    normalize.apply_filters(conn)
    out = {"positives": len(pos), "negatives": len(neg), "scored": len(rows), "cv_auc": round(auc, 3),
           "threshold": THRESHOLD}
    save_status("relevance", out)
    log("relevance", str(out))
    return out
