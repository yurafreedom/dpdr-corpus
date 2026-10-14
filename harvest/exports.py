"""Exports (spec section 16): CSVs, reading list, stats, request-from-author, spot check."""
import csv
import json
import random
import time
from collections import Counter

import config
from harvest.common import load_status
from harvest.scope import core_sql

E = config.EXPORTS
WORK_COLS = ["id", "doi", "pmid", "pmcid", "openalex_id", "s2_id", "title", "year", "venue", "type", "language",
             "oa_status", "oa_url", "fulltext_path", "cited_by_count", "s2_influential", "is_review",
             "is_retracted", "excluded_reason", "scope_flags", "query_tags", "first_author", "keywords",
             "mesh", "tldr", "abstract"]


def write_csv(name, rows, cols):
    with open(E / name, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([r[c] if not isinstance(r, dict) else r.get(c) for c in cols])
    return len(rows)


def table_exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is not None


def reading_list(conn):
    """Separate DPDR and time-perception lists. A work qualifies only if the topic is in
    its title (or MeSH Depersonalization / Time Perception), so highly cited papers
    that mention depersonalization in passing do not dominate."""
    from harvest.normalize import DPDR_TERM, TIME_TERM
    core = core_sql()
    rows = conn.execute(f"SELECT * FROM works WHERE {core}").fetchall()
    fam = {
        "DPDR": [r for r in rows if '"dpdr"' in (r["query_tags"] or "") and (
            DPDR_TERM.search(r["title"] or "") or "Depersonalization" in (r["mesh"] or ""))],
        "Time perception": [r for r in rows if '"time"' in (r["query_tags"] or "") and (
            TIME_TERM.search(r["title"] or "") or "Time Perception" in (r["mesh"] or ""))],
    }
    cites = lambda r: r["cited_by_count"] or 0  # noqa: E731
    L = ["# Reading list", "",
         "Core in-scope works whose title names the topic (or carry the matching MeSH heading). Citations from "
         "OpenAlex/Europe PMC; influence = Semantic Scholar influential citations. "
         "Markers: · full text = stored in fulltext/, · OA = legal OA link known.", ""]
    for name, ws in fam.items():
        prim = [r for r in ws if not r["is_review"]]
        L += [f"# {name} ({len(ws)} qualifying works)", "", "## Top 10 by citations per five-year window", ""]
        old = sorted((r for r in prim if r["year"] and r["year"] < 1990), key=cites, reverse=True)[:10]
        if old:
            L += ["### Before 1990 (foundational)", ""] + [fmt(r) for r in old] + [""]
        for start in range(1990, 2030, 5):
            top = sorted((r for r in prim if r["year"] and start <= r["year"] <= start + 4), key=cites, reverse=True)[:10]
            if top:
                L += [f"### {start}–{start + 4}", ""] + [fmt(r) for r in top] + [""]
        recent = [r for r in ws if r["year"] and 2022 <= r["year"] <= 2026]
        L += ["## Top 30 of 2022–2026 by citations", ""] + [fmt(r) for r in sorted(recent, key=cites, reverse=True)[:30]] + [""]
        L += ["## Top 30 of 2022–2026 by Semantic Scholar influence", ""] + [
            fmt(r) for r in sorted(recent, key=lambda r: (r["s2_influential"] or 0, cites(r)), reverse=True)[:30]] + [""]
        revs = sorted((r for r in ws if r["is_review"]), key=cites, reverse=True)[:40]
        L += ["## Reviews and meta-analyses (top 40 by citations)", ""] + [fmt(r) for r in revs] + [""]
    (E / "reading_list.md").write_text("\n".join(L))


def fmt(r):
    link = f"https://doi.org/{r['doi']}" if r["doi"] else (
        f"https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}" if r["pmid"] else "")
    extra = f", influential {r['s2_influential']}" if r["s2_influential"] else ""
    ft = " · full text" if r["fulltext_path"] else (" · OA" if r["oa_url"] else "")
    return f"- {r['first_author'] or '?'} ({r['year']}). {r['title']} *{r['venue'] or ''}*. cited {r['cited_by_count'] or 0}{extra}{ft} {link}"


def stats(conn):
    core = core_sql("w")
    L = ["# Corpus statistics", "", f"Generated {time.strftime('%Y-%m-%d %H:%M')}.", ""]
    tot = conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
    excl = dict(conn.execute("SELECT excluded_reason, COUNT(*) FROM works GROUP BY excluded_reason").fetchall())
    flags = Counter()
    for (f,) in conn.execute("SELECT scope_flags FROM works WHERE scope_flags IS NOT NULL"):
        flags.update(json.loads(f))
    ncore = conn.execute(f"SELECT COUNT(*) FROM works w WHERE {core}").fetchone()[0]
    L += ["## Totals", "", f"- works: {tot}", f"- core in-scope: {ncore}",
          f"- excluded: " + ", ".join(f"{k}={v}" for k, v in excl.items() if k),
          f"- flagged (kept): " + ", ".join(f"{k}={v}" for k, v in flags.items()), ""]
    tags = Counter()
    for (t,) in conn.execute("SELECT query_tags FROM works"):
        tags.update(json.loads(t or "[]"))
    L += ["## Works by query family (a work can carry several)", "",
          "| family | works |", "|---|---:|"] + [f"| {k} | {v} |" for k, v in tags.most_common()] + [""]
    src = Counter()
    for (s,) in conn.execute("SELECT raw_sources FROM works"):
        src.update({x.split(":")[0] for x in json.loads(s or "[]")})
    L += ["## Works by source", "", "| source | works |", "|---|---:|"] + [f"| {k} | {v} |" for k, v in src.most_common()] + [""]
    rows = conn.execute(f"""SELECT year, COUNT(*), SUM(oa_url IS NOT NULL), SUM(fulltext_path IS NOT NULL)
                            FROM works w WHERE {core} GROUP BY year ORDER BY year""").fetchall()
    L += ["## Core works by year, OA and full-text coverage", "", "| year | works | OA link | full text |",
          "|---|---:|---:|---:|"]
    L += [f"| {y} | {n} | {oa} ({100 * oa / n:.0f}%) | {ft} ({100 * ft / n:.0f}%) |" for y, n, oa, ft in rows if y]
    s = conn.execute(f"SELECT COUNT(*), SUM(oa_url IS NOT NULL), SUM(fulltext_path IS NOT NULL) FROM works w WHERE {core}").fetchone()
    if s[0]:
        L += ["", f"Overall: OA link {100 * (s[1] or 0) / s[0]:.1f}%, full text stored {100 * (s[2] or 0) / s[0]:.1f}%."]
    for lo, hi in ((1990, 2010), (2011, 2014), (2015, 2026)):
        s = conn.execute(f"SELECT COUNT(*), SUM(fulltext_path IS NOT NULL) FROM works w WHERE {core} AND year BETWEEN ? AND ?",
                         (lo, hi)).fetchone()
        if s[0]:
            L.append(f"- {lo}–{hi}: full text {100 * (s[1] or 0) / s[0]:.1f}% of {s[0]}")
    L.append("")
    rows = conn.execute(f"""SELECT i.country, COUNT(DISTINCT w.id) FROM works w JOIN authorships s ON s.work_id=w.id AND s.position=0
                            JOIN institutions i ON i.id=s.institution_id WHERE {core} GROUP BY i.country
                            ORDER BY 2 DESC LIMIT 25""").fetchall()
    L += ["## Core works by first-author country (OpenAlex)", "", "| country | works |", "|---|---:|"]
    L += [f"| {c} | {n} |" for c, n in rows] + [""]
    rows = conn.execute(f"""SELECT i.name, i.country, COUNT(DISTINCT w.id) FROM works w JOIN authorships s ON s.work_id=w.id
                            JOIN institutions i ON i.id=s.institution_id WHERE {core} AND w.query_tags LIKE '%"dpdr"%'
                            GROUP BY i.id ORDER BY 3 DESC LIMIT 30""").fetchall()
    L += ["## Institutions with most DPDR works (any author position)", "", "| institution | country | works |",
          "|---|---|---:|"] + [f"| {a} | {b} | {c} |" for a, b, c in rows] + [""]
    if table_exists(conn, "trials"):
        rows = conn.execute("SELECT registry, status, COUNT(*) FROM trials GROUP BY 1, 2 ORDER BY 1, 3 DESC").fetchall()
        L += ["## Trials by registry and status", "", "| registry | status | n |", "|---|---|---:|"]
        L += [f"| {a} | {b} | {c} |" for a, b, c in rows] + [""]
    rows = conn.execute("""SELECT funder, institution, pi, COUNT(*) n, MAX("end") FROM grants
                           WHERE "end" >= '2023' GROUP BY funder, institution, pi ORDER BY n DESC LIMIT 30""").fetchall()
    L += ["## Active groups by grants (ending 2023 or later)", "", "| funder | institution | PI | grants | latest end |",
          "|---|---|---|---:|---|"] + [f"| {a} | {b} | {c} | {d} | {e} |" for a, b, c, d, e in rows] + [""]
    rows = conn.execute("SELECT platform, COUNT(*) FROM posts GROUP BY 1").fetchall()
    L += ["## Posts by platform", ""] + [f"- {a}: {b}" for a, b in rows] + [""]
    rows = conn.execute("SELECT kind, source, COUNT(*) FROM records GROUP BY 1, 2 ORDER BY 1").fetchall()
    L += ["## Other records (datasets, books, podcasts, archives…)", "", "| kind | source | n |", "|---|---|---:|"]
    L += [f"| {a} | {b} | {c} |" for a, b, c in rows] + [""]
    L += coverage_section(conn) + module_section()
    (E / "stats.md").write_text("\n".join(L))


def coverage_section(conn):
    rec = load_status("recon")
    L = ["## Source coverage for the same query (DPDR core union)", ""]
    if rec:
        for t in rec["terms"][:2]:
            L.append(f"- {t['term']}: PubMed {t['pubmed']}, Europe PMC {t['epmc']}, OpenAlex {t['openalex']}")
    pm = load_status("pubmed").get("counts", {})
    L += [f"- Unique PMIDs for the union (no date split): {pm.get('dpdr')}",
          "- PubMed per-year sums overcount by ~10%: `pdat` matches both the electronic and the print date.",
          "- OpenAlex is ~3x larger: it covers social science, education and nursing journals outside MEDLINE, "
          "most of them Maslach-burnout papers; after the burnout filter the excess is mostly non-MEDLINE "
          "psychology, philosophy and non-English journals.",
          "- Europe PMC has no MeSH search field, so MeSH-only PubMed hits are not mirrored there.", ""]
    return L


def module_section():
    L = ["## Module status", "", "| stage | module | status | note |", "|---|---|---|---|"]
    from pipeline import STAGES
    for stage, mods in STAGES:
        st = load_status(f"stage_{stage}")
        for m in mods:
            s = st.get(m, {})
            note = s.get("reason") or ""
            summ = s.get("summary")
            if isinstance(summ, dict) and summ.get("empty_finding"):
                note = summ["empty_finding"]
            L.append(f"| {stage} | {m} | {s.get('status', 'not run')} | {str(note)[:160].replace('|', '/')} |")
    b = load_status("openalex_budget")
    if b:
        L += ["", f"OpenAlex budget at last call: ${b['remaining_usd']:.4f} of ${b['limit_usd']} "
                  f"({'keyed' if b['keyed'] else 'keyless'}), checked {b['checked_at']}."]
    return L


def request_from_author(conn):
    core = core_sql("w")
    rows = conn.execute(f"""
        SELECT w.id, w.title, w.year, w.doi, w.pmid, w.venue, w.cited_by_count, w.first_author,
               (SELECT a.orcid FROM authorships s JOIN authors a ON a.id=s.author_id
                 WHERE s.work_id=w.id ORDER BY s.position DESC LIMIT 1) AS last_author_orcid,
               (SELECT a.display_name FROM authorships s JOIN authors a ON a.id=s.author_id
                 WHERE s.work_id=w.id ORDER BY s.position DESC LIMIT 1) AS last_author
        FROM works w WHERE {core} AND w.fulltext_path IS NULL AND w.query_tags LIKE '%"dpdr"%'
          AND (w.cited_by_count >= 25 OR w.year >= 2020)
        ORDER BY w.cited_by_count DESC""").fetchall()
    return write_csv("request_from_author.csv", rows,
                     ["id", "title", "year", "doi", "pmid", "venue", "cited_by_count", "first_author",
                      "last_author", "last_author_orcid"])


def spot_check(conn):
    random.seed(30)
    inc = conn.execute(f"SELECT * FROM works WHERE {core_sql()} AND query_tags LIKE '%\"dpdr\"%'").fetchall()
    exc = conn.execute("SELECT * FROM works WHERE excluded_reason='burnout'").fetchall()
    soft = conn.execute("SELECT * FROM works WHERE scope_flags LIKE '%burnout_soft%'").fetchall()
    sample = random.sample(inc, min(12, len(inc))) + random.sample(exc, min(12, len(exc))) + random.sample(soft, min(6, len(soft)))
    rows = [{"id": r["id"], "status": r["excluded_reason"] or (r["scope_flags"] or "included"), "year": r["year"],
             "title": r["title"], "doi": r["doi"], "pmid": r["pmid"], "abstract_head": (r["abstract"] or "")[:400],
             "verdict(correct/false_inclusion/false_exclusion)": ""} for r in sample]
    write_csv("spot_check.csv", rows, list(rows[0]) if rows else ["id"])


def posts_summary(conn):
    rows = conn.execute("""SELECT platform, COALESCE(community, '') community, substr(created, 1, 7) month,
                           COUNT(*) n, COUNT(DISTINCT author_id) authors FROM posts GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""").fetchall()
    return write_csv("posts_summary.csv", rows, ["platform", "community", "month", "n", "authors"])


def run(conn):
    E.mkdir(exist_ok=True)
    out = {"works.csv": write_csv("works.csv", conn.execute("SELECT * FROM works ORDER BY year, id").fetchall(), WORK_COLS)}
    out["people.csv"] = write_csv("people.csv", conn.execute("SELECT * FROM people ORDER BY seed DESC, corpus_works DESC").fetchall(),
                                  ["name", "openalex_id", "orcid", "s2_id", "works_total", "works_2022_2026",
                                   "current_institution", "institution_source", "institution_verified", "country",
                                   "top5_works", "coauthors", "channels", "clinician", "seed", "corpus_works",
                                   "needs_review", "orcid_employment"])
    out["trials.csv"] = write_csv("trials.csv", conn.execute("SELECT * FROM trials ORDER BY registry, start DESC").fetchall(),
                                  ["id", "registry", "title", "conditions", "interventions", "status", "phase", "sponsor",
                                   "pi", "sites", "start", "end", "enrollment", "has_results", "url", "query_tags"])
    out["grants.csv"] = write_csv("grants.csv", conn.execute('SELECT * FROM grants ORDER BY funder, "end" DESC').fetchall(),
                                  ["id", "funder", "title", "pi", "institution", "start", "end", "amount", "currency",
                                   "abstract", "url", "query_tags"])
    out["posts_summary.csv"] = posts_summary(conn)
    out["request_from_author.csv"] = request_from_author(conn)
    spot_check(conn)
    reading_list(conn)
    stats(conn)
    return out
