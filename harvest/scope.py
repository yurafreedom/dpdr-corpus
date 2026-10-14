"""Which works count as in scope for costly enrichment steps."""


def _sql(alias, families):
    p = f"{alias}." if alias else ""
    tags = " OR ".join(f"{p}query_tags LIKE '%{f}%'" for f in families)
    return (f"{p}excluded_reason IS NULL AND ({p}scope_flags IS NULL OR "
            f"({p}scope_flags NOT LIKE '%offtopic%' AND {p}scope_flags NOT LIKE '%broad_only%' "
            f"AND {p}scope_flags NOT LIKE '%no_term_match%' AND {p}scope_flags NOT LIKE '%nonclinical%' "
            f"AND {p}scope_flags NOT LIKE '%low_relevance%' AND {p}scope_flags NOT LIKE '%burnout_soft%')) AND ({tags})")


def in_scope_sql(alias=""):
    return _sql(alias, ['"dpdr"', '"time"', '"ru"', '"de"', "snowball"])


def core_sql(alias=""):
    return _sql(alias, ['"dpdr"', '"time"'])


IN_SCOPE_SQL = in_scope_sql()
CORE_SQL = core_sql()
