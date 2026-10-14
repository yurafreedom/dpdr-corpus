#!/bin/sh
# Daily Reddit Data API fetch + deletion purge (harvest/reddit_api.py). Idle until all REDDIT_* vars are set.
# Purge: API-fetched items are re-checked every run for their first 30 days; older ones and the Arctic Shift
# history 1/7 per day (rolling 7-day cycle). Run once a day so every item is re-checked at least weekly.
# A run is a few hundred requests.
# Example crontab (03:30 UTC): 30 3 * * * /path/to/dpdr-corpus/scripts/daily_reddit.sh >> /path/to/dpdr-corpus/status/reddit.log 2>&1
cd "$(dirname "$0")/.." || exit 1
.venv/bin/python pipeline.py run --module reddit_api
