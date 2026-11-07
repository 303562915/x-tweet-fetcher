"""Parser for tweets embedded in a logged-out x.com profile page.

When nobody is logged in, x.com still server-renders the first screen of a
profile as an escaped JSON payload embedded in the HTML. Measured on
``x.com/aleabitoreddit`` that payload carried 5-7 full tweet objects including
the account's most recent post — i.e. a genuinely *current* view of the
timeline, which the syndication widget is not (it returned two stale 2025
tweets for the same account).

Trade-offs, measured rather than assumed:
  * Only the first screen is embedded — about 5-7 tweets — and the payload has
    no pagination cursor, so a single fetch cannot reach 50.
  * Combine it with ``xtf --ledger`` to accumulate across runs; polling every
    few hours covers an account publishing a handful of posts a day.

All functions here are pure: string in, dict/list out. No network, no I/O.
"""
from __future__ import annotations

import html as html_module
import json
import re
from datetime import datetime, timezone
from typing import Any

from .syndication_json import _TWITTER_TIME, _int, relative_time

#: Anchors marking one tweet object inside the SSR payload. ``__isTweetResult``
#: is the object's first key, so brace-matching from it lands exactly on the
#: opening brace; ``__typename`` is kept as a fallback for payloads that omit it.
_TWEET_ANCHORS = ('"__isTweetResult":"Tweet"', '"__typename":"Tweet"')

#: Hard cap on how far a single object may extend (protects against a payload
#: that never closes its braces).
_MAX_OBJECT = 80_000

_SCREEN_NAME_RE = re.compile(r'"screen_name":"([A-Za-z0-9_]{1,20})"')


def unescape_payload(html: str) -> str:
    """Unescape exactly one level of the SSR payload.

    The page embeds JSON inside JSON, so every inner quote arrives as ``\\"``.
    Only that level is removed: inner ``\\n`` escapes must stay intact or the
    inner document stops being valid JSON.
    """
    return (html or "").replace('\\"', '"')


def _iter_objects(flat: str, anchor: str) -> list[dict[str, Any]]:
    """Find each ``anchor`` occurrence and brace-match the object around it."""
    found: list[dict[str, Any]] = []
    for match in re.finditer(re.escape(anchor), flat):
        start = flat.rfind("{", 0, match.start())
        if start == -1:
            continue
        depth = 0
        for i in range(start, min(len(flat), start + _MAX_OBJECT)):
            char = flat[i]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(flat[start:i + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(obj, dict):
                        found.append(obj)
                    break
    return found


def extract_tweet_objects(html: str) -> list[dict[str, Any]]:
    """Raw tweet objects embedded in a logged-out profile page (deduped by id)."""
    flat = unescape_payload(html)
    objects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in _TWEET_ANCHORS:
        for obj in _iter_objects(flat, anchor):
            ids = ((obj.get("edit_control") or {}).get("edit_tweet_ids") or [])
            key = str(ids[0]) if ids else str(obj.get("rest_id") or id(obj))
            if key in seen:
                continue
            seen.add(key)
            objects.append(obj)
    return objects


def _ms_to_twitter_time(value: Any) -> str:
    """Unix milliseconds -> Twitter's absolute timestamp string."""
    if value in (None, ""):
        return ""
    try:
        seconds = int(value) / 1000
    except (TypeError, ValueError):
        return ""
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime(_TWITTER_TIME)
    except (OverflowError, OSError, ValueError):
        return ""


def _views(obj: dict[str, Any]) -> int:
    value = obj.get("views")
    if isinstance(value, dict):
        return _int(value.get("count"))
    return _int(value)


def _media_urls(obj: dict[str, Any]) -> list[str]:
    """Media URLs from ``media_entities2`` (shape varies, so probe keys)."""
    urls: list[str] = []
    entities = obj.get("media_entities2")
    if not isinstance(entities, list):
        return urls
    for media in entities:
        if not isinstance(media, dict):
            continue
        for key in ("media_url_https", "media_url", "url", "image_url"):
            value = media.get(key)
            if isinstance(value, str) and value.startswith("http"):
                urls.append(value)
                break
        else:
            # nested under media_info / original_info on some payloads
            for nested in (media.get("media_info"), media.get("original_info")):
                if isinstance(nested, dict):
                    value = (nested.get("original_img_url")
                             or nested.get("media_url_https"))
                    if isinstance(value, str) and value:
                        urls.append(value)
                        break
    return list(dict.fromkeys(urls))


def _clean_text(raw: Any) -> str:
    """Body text as plain text.

    The payload keeps one extra escape level, so newlines arrive as a literal
    backslash-n, and a few HTML entities survive serialisation.
    """
    if not isinstance(raw, str):
        return ""
    return html_module.unescape(raw.replace("\\n", "\n").replace("\\r", "\r"))


def tweet_object_to_entry(obj: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """One SSR tweet object -> model-friendly entry dict (syndication shape)."""
    author_result = ((obj.get("core") or {}).get("user_results") or {}).get("result") or {}
    author_core = author_result.get("core") or {}
    screen = author_core.get("screen_name") or ""
    details = obj.get("details") or {}
    counts = obj.get("counts") or {}

    ids = ((obj.get("edit_control") or {}).get("edit_tweet_ids") or [])
    tweet_id = str(ids[0]) if ids else str(obj.get("rest_id") or "")
    created_at = _ms_to_twitter_time(details.get("created_at_ms"))

    entry: dict[str, Any] = {
        "author": f"@{screen}" if screen else "",
        "author_name": author_core.get("name") or screen,
        "text": _clean_text(details.get("full_text")),
        "time_ago": relative_time(created_at),
        "likes": _int(counts.get("favorite_count")),
        "retweets": _int(counts.get("retweet_count")),
        "replies": _int(counts.get("reply_count")),
        "views": _views(obj),
        "tweet_id": tweet_id,
        "media": _media_urls(obj),
        "urls": [],
        "created_at": created_at,
        "lang": "",
    }

    quoted = obj.get("quoted_tweet_results")
    if isinstance(quoted, dict) and depth < 2:
        entry["quoted_status_id"] = str(quoted.get("rest_id") or "")
        quoted_entry = tweet_object_to_entry(quoted, depth + 1)
        # The referenced tweet is surfaced as context; its own text is often
        # not embedded, so keep the shell only when it carries something.
        if quoted_entry.get("text") or quoted_entry.get("author"):
            entry["quoted_tweet"] = quoted_entry

    return entry


def parse_profile_timeline(html: str, limit: int | None = None,
                           author: str | None = None) -> list[dict[str, Any]]:
    """Logged-out profile HTML -> normalized entries, newest first.

    ``author`` keeps only that account's own posts: the payload also carries
    tweets the account quoted or replied to, and mixing them in would make a
    "latest N posts" result wrong.
    """
    entries = [tweet_object_to_entry(obj) for obj in extract_tweet_objects(html)]
    entries = [e for e in entries if e.get("tweet_id")]

    if author:
        wanted = "@" + author.strip().lstrip("@").lower()
        entries = [e for e in entries if (e.get("author") or "").lower() == wanted]

    def stamp(entry: dict[str, Any]) -> float:
        raw = entry.get("created_at") or ""
        try:
            parsed = datetime.strptime(raw, _TWITTER_TIME)
        except (ValueError, TypeError):
            return float("-inf")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    entries.sort(key=stamp, reverse=True)
    # same tweet can appear twice in the payload (e.g. quoted + timeline entry)
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for entry in entries:
        if entry["tweet_id"] in seen:
            continue
        seen.add(entry["tweet_id"])
        unique.append(entry)

    if limit is not None and limit >= 0:
        return unique[:limit]
    return unique


def extract_profile_user(html: str) -> dict[str, Any]:
    """Best-effort profile dict (empty when the payload carries none)."""
    flat = unescape_payload(html)
    match = _SCREEN_NAME_RE.search(flat)
    if not match:
        return {}
    screen = match.group(1)
    followers = re.search(r'"followers":(\d+)', flat)
    tweets = re.search(r'"tweet_counts":\{"[^"]*":\d+,"tweets":(\d+)\}', flat)
    return {
        "screen_name": screen,
        "followers": _int(followers.group(1)) if followers else 0,
        "tweets": _int(tweets.group(1)) if tweets else 0,
    }
