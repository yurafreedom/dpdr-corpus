"""Pseudonymize Reddit pages cached before harvest/pseudonym.py existed.

Rewrites raw/arctic_shift/*.json and raw/reddit/*.json in place with
scrub_item (account fields replaced by author_hash, u/name mentions redacted).
Already-scrubbed pages are left as they are. Needs REDDIT_HASH_SALT. Dry run by
default; run it while no harvest is writing to raw/:

    .venv/bin/python scripts/scrub_reddit_cache.py           # count only
    .venv/bin/python scripts/scrub_reddit_cache.py --apply
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from harvest.pseudonym import salt, scrub_item  # noqa: E402


def main(argv):
    apply = "--apply" in argv
    if not salt():
        sys.exit("REDDIT_HASH_SALT is not set")
    changed = total = 0
    for source in ("arctic_shift", "reddit"):
        for p in sorted((config.RAW / source).glob("*.json")):
            total += 1
            before = p.read_text()
            after = json.dumps(scrub_item(json.loads(before)), ensure_ascii=False)
            if json.loads(after) == json.loads(before):
                continue
            changed += 1
            if apply:
                tmp = p.with_suffix(".tmp")
                tmp.write_text(after)
                tmp.replace(p)
    print(f"{changed} of {total} cached pages {'rewritten' if apply else 'would be rewritten (dry run)'}")


if __name__ == "__main__":
    main(sys.argv[1:])
