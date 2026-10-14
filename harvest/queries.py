"""Render config query DSL to PubMed, Europe PMC and OpenAlex syntax."""

# Server-side approximation of the burnout exclusion (exact filter runs locally).
BURNOUT_NOT = {
    "pubmed": ["burnout", "burn-out", "maslach", "MBI", "cynicism", '"emotional exhaustion"',
               '"job satisfaction"', "workload", "nurs*", "teacher*", "customer*", "dehumaniz*", "dehumanis*"],
    "openalex": ["burnout", "burn-out", "maslach", "MBI", "cynicism", "emotional exhaustion",
                 "job satisfaction", "workload", "nurse", "nurses", "nursing", "teacher", "teachers",
                 "customer", "customers", "dehumanization", "dehumanisation", "dehumanizing"],
}


def _q(p):
    return f'"{p}"'


def pubmed(query, exclude_burnout=False):
    groups = []
    for g in query:
        alts = []
        for a in g:
            if isinstance(a, dict):
                alts.append(f'"{a["mesh"]}"[MeSH Terms]')
            else:
                alts.append(f"{_q(a)}[tiab]")
        groups.append("(" + " OR ".join(alts) + ")")
    s = " AND ".join(groups)
    if exclude_burnout:
        s = f"({s}) NOT (" + " OR ".join(f"{t}[tiab]" for t in BURNOUT_NOT["pubmed"]) + ")"
    return s


def _epmc_field(term):
    return f"(TITLE:{term} OR ABSTRACT:{term} OR KW:{term})"


def europepmc(query, exclude_burnout=False):
    groups = []
    for g in query:
        # Europe PMC has no MeSH search field; an unknown field inside an OR
        # silently degrades to a full-text match, so MeSH alternatives are dropped.
        alts = [_epmc_field(_q(a)) for a in g if not isinstance(a, dict)]
        if not alts:
            return None
        groups.append("(" + " OR ".join(alts) + ")")
    s = " AND ".join(groups)
    if exclude_burnout:
        s = f"({s}) NOT (" + " OR ".join(_epmc_field(t) for t in BURNOUT_NOT["pubmed"]) + ")"
    return s


def openalex(query, exclude_burnout=False):
    """Value for filter=title_and_abstract.search:<value>. MeSH alternatives are dropped."""
    groups = []
    for g in query:
        alts = [_q(a) for a in g if not isinstance(a, dict)]
        if not alts:
            return None
        groups.append("(" + " OR ".join(alts) + ")")
    s = " AND ".join(groups)
    if exclude_burnout:
        s = f"({s}) NOT (" + " OR ".join(_q(t) for t in BURNOUT_NOT["openalex"]) + ")"
    return s
