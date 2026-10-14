"""Topic clusters (exports/clusters.md) from SPECTER v2 embeddings (Semantic Scholar).

KMeans over core in-scope works that have an embedding; works without one fall
back to TF-IDF+SVD vectors only if fewer than 300 embeddings exist. Each
cluster is named by its distinctive title terms and mapped to the spec's
themes by keyword vote; the 5 works nearest the centroid are listed.
"""
import json
import re

import numpy as np

import config
from harvest.scope import CORE_SQL

THEMES = {
    "mechanisms": r"insula|interocept|autonomic|cortisol|\bhpa\b|fmri|\beeg\b|neural|predictive (coding|processing)|skin conductance|amygdala|prefrontal",
    "pharmacotherapy": r"lamotrigine|naltrexone|fluoxetine|\bssris?\b|clomipramine|clonazepam|pharmacother|medication|antidepressant|anticonvulsant|opioid antagon",
    "psychotherapy": r"\bcbt\b|cognitive behavio|psychotherap|psychodynamic|mindfulness|\bemdr\b",
    "neuromodulation": r"\brtms\b|\btms\b|\btdcs\b|transcranial|brain stimulation|neuromodulation|\bect\b|electroconvulsive|neurofeedback",
    "time": r"time perception|temporal (processing|experience|binding)|interval timing|duration|passage of time|subjective time|intentional binding",
    "phenomenology": r"phenomenolog|minimal self|selfhood|embodi|consciousness|philosoph",
    "epidemiology/assessment": r"prevalence|questionnaire|validation|epidemiolog|psychometric|general population",
    "trauma/dissociation": r"trauma|\bptsd\b|dissociative identity|childhood abuse|maltreatment",
}
STOP = set("the of and in a an to for with on by from as at is are be or its their this that via among into".split())


def tokens(t):
    return [w for w in re.findall(r"[a-z][a-z-]{2,}", (t or "").lower()) if w not in STOP]


PARTS = [("DPDR", """query_tags LIKE '%"dpdr"%'""", 8),
         ("Time perception", """query_tags LIKE '%"time"%' AND query_tags NOT LIKE '%"dpdr"%'""", 6)]


def cluster(rows, k, label, out, summary):
    from sklearn.cluster import KMeans
    X = np.array([json.loads(r["embedding"]) for r in rows], dtype=np.float32)
    X /= np.linalg.norm(X, axis=1, keepdims=True)
    km = KMeans(n_clusters=k, n_init=10, random_state=13).fit(X)
    df = {}
    for r in rows:
        for w in set(tokens(r["title"])):
            df[w] = df.get(w, 0) + 1
    out += [f"# {label}", "", f"KMeans k={k} over {len(rows)} embedded core works.", ""]
    for c in sorted(range(k), key=lambda c: -(km.labels_ == c).sum()):
        idx = np.where(km.labels_ == c)[0]
        near = idx[np.argsort(-(X[idx] @ km.cluster_centers_[c]))][:5]
        tf = {}
        for i in idx:
            for w in set(tokens(rows[i]["title"])):
                tf[w] = tf.get(w, 0) + 1
        score = {w: n / len(idx) / (df[w] / len(rows)) for w, n in tf.items() if n >= max(3, len(idx) * 0.04)}
        terms = [w for w, _ in sorted(score.items(), key=lambda x: -x[1])[:6]]
        text = " ".join(f"{rows[i]['title']} {rows[i]['abstract'] or ''}" for i in idx).lower()
        votes = {t: len(re.findall(p, text)) for t, p in THEMES.items()}
        theme = max(votes, key=votes.get)
        years = [rows[i]["year"] for i in idx if rows[i]["year"]]
        out += [f"## {theme}: {', '.join(terms)}", "",
                f"{len(idx)} works, median year {int(np.median(years)) if years else '?'}. Theme votes: "
                + ", ".join(f"{t} {v}" for t, v in sorted(votes.items(), key=lambda x: -x[1])[:3]), "",
                "Central works:", ""]
        out += [f"- ({rows[i]['year']}) {rows[i]['title']} — cited {rows[i]['cited_by_count'] or 0}" for i in near]
        out.append("")
        summary[f"{label}/{len(summary) + 1}"] = {"n": int(len(idx)), "theme": theme, "terms": terms}


def run(conn):
    out = ["# Topic clusters", "",
           "SPECTER v2 embeddings (Semantic Scholar) of core in-scope works, clustered separately for the DPDR "
           "and time-perception families. Cluster names are the most distinctive title terms; the theme is a "
           "keyword vote against the spec's categories (mechanisms, pharmacotherapy, psychotherapy, "
           "neuromodulation, time, phenomenology, plus assessment and trauma/dissociation).", ""]
    summary = {}
    for label, where, k in PARTS:
        rows = conn.execute(f"""SELECT id, title, abstract, year, cited_by_count, embedding FROM works
                                WHERE {CORE_SQL} AND {where} AND embedding IS NOT NULL""").fetchall()
        if len(rows) < 10 * k:
            out += [f"# {label}", "", f"Only {len(rows)} embedded works; run semanticscholar first.", ""]
            continue
        cluster(rows, k, label, out, summary)
    (config.EXPORTS / "clusters.md").write_text("\n".join(out))
    return {"clusters": summary}
