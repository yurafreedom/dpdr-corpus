"""Reddit author pseudonymization, shared by reddit_api and arctic_shift.

An author is stored only as HMAC-SHA256(REDDIT_HASH_SALT, author_fullname),
falling back to the username when the fullname is absent. Both modules hash the
same input, so one account gets the same pseudonym from either source, and
without the salt the pseudonym cannot be reversed by hashing candidate names.

scrub_item() removes every field that names a Reddit account (author, flair,
moderator fields) and redacts u/name mentions in text. It runs on API data
before anything is written to raw/ or the database.
"""
import hashlib
import hmac
import os
import re

SALT_VAR = "REDDIT_HASH_SALT"
# Fields that carry an account name. author_* (flair, premium, patreon, ...) is dropped as a prefix.
_ACCOUNT_FIELDS = {"author", "author_fullname", "approved_by", "banned_by", "mod_reason_by", "removed_by"}
_TEXT_FIELDS = ("title", "selftext", "body")
# u/name or /u/name; Reddit usernames are 3-20 characters of [A-Za-z0-9_-].
_MENTION = re.compile(r"(?<![\w/])/?u/[A-Za-z0-9_-]{3,20}\b")
_GONE = {None, "", "[deleted]"}


def salt():
    return os.environ.get(SALT_VAR, "")


def author_hash(fullname=None, name=None, key=None):
    """Pseudonym for an account, or None for a deleted author."""
    key = key if key is not None else salt()
    if not key:
        raise RuntimeError(f"{SALT_VAR} is not set; refusing to handle author fields")
    value = fullname if fullname not in _GONE else name
    if value in _GONE:
        return None
    return hmac.new(key.encode(), str(value).encode(), hashlib.sha256).hexdigest()


def redact(text):
    return _MENTION.sub("u/[user]", text) if isinstance(text, str) else text


def scrub_item(d, key=None):
    """Return a copy of a Reddit JSON value with account fields replaced by author_hash."""
    if isinstance(d, list):
        return [scrub_item(v, key) for v in d]
    if not isinstance(d, dict):
        return d
    out = {}
    for k, v in d.items():
        if k in _ACCOUNT_FIELDS or (k.startswith("author_") and k != "author_hash"):
            continue
        out[k] = redact(v) if k in _TEXT_FIELDS else scrub_item(v, key)
    if "author" in d or "author_fullname" in d:
        out["author_hash"] = author_hash(d.get("author_fullname"), d.get("author"), key)
    return out
