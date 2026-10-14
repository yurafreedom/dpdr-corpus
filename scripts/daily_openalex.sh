#!/bin/sh
# Continue the budget-limited OpenAlex work after the daily reset (00:00 UTC), then refresh outputs.
# Keyless: ~1,000 credits/day. Seed remainder ~750 credits, snowball ~1,000+, so 2-3 days without a key.
# Example crontab (00:15 UTC): 15 0 * * * /path/to/DPDR/scripts/daily_openalex.sh >> /path/to/DPDR/status/daily.log 2>&1
cd "$(dirname "$0")/.." || exit 1
for m in openalex_seed openalex_lookup normalize openalex_snowball openalex_lookup crossref semanticscholar people fulltext normalize embeddings_clusters exports; do
  .venv/bin/python pipeline.py run --module "$m"
done
