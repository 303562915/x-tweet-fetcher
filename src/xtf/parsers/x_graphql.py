"""Parser for X's web GraphQL timeline responses.

The signed-in web app fetches a profile timeline with the
``UserOriginalsTimeline`` operation and paginates with an opaque cursor. Both
the response shape and the tweet objects are documented here so a plain-HTTP
call can reproduce what the browser does.

Query ids and feature flags rotate on X's schedule; they live in
``xtf/data/x_graphql.json`` and can be refreshed with
``scripts/refresh_graphql_template.py``.

All functions here are pure: dict in, data out. No network, no I/O.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .syndication_json import _int, relative_time

_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "data" / "x_graphql.json"

#: How deep to walk a response looking for entries (X nests them several ways).
_MAX_DEPTH = 15


@dataclass
class TimelinePage:
    """One page of a GraphQL timeline."""

    tweets: list[dict[str, Any]]
    bottom_cursor: str | None = None

    @property
    def has_more(self) -> bool:
        return bool(self.bottom_cursor)


def load_template(path: str | Path | None = None) -> dict[str, Any]:
    """Load the captured queryId/features template."""
    raw = json.loads(Path(path or _TEMPLATE_PATH).read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items()
            if not k.startswith("_") and isinstance(v, dict) and "queryId" in v}


def build_variables(operation: str, template: dict[str, Any],
                    overrides: dict[str, Any]) -> dict[str, Any]:
    """Template variables with the caller's values merged in."""
    base = dict((template.get(operation) or {}).get("variables") or {})
    base.update(overrides)
    return base


def _unwrap_tweet(result: Any) -> dict[str, Any] | None:
    """Unwrap ``TweetWithVisibilityResults`` and reject non-tweet results."""
    if not isinstance(result, dict):
        return None
    if result.get("__typename") == "TweetWithVisibilityResults":
        result = result.get("tweet") or {}
    if isinstance(result, dict) and (result.get("legacy") or result.get("__typename") == "Tweet"):
        return result
    return None


def parse_page(data: dict[str, Any]) -> TimelinePage:
    """Extract tweets + the Bottom cursor from a UserOriginalsTimeline response."""
    tweets: list[dict[str, Any]] = []
    cursors: list[tuple[str, str]] = []
    seen: set[str] = set()

    def walk(node: Any, depth: int = 0) -> None:
        if depth > _MAX_DEPTH:
            return
        if isinstance(node, list):
            for item in node:
                walk(item, depth + 1)
            return
        if not isinstance(node, dict):
            return

        content = node.get("content")
        if isinstance(content, dict):
            cursor_type = content.get("cursorType")
            value = content.get("value")
            if isinstance(cursor_type, str) and isinstance(value, str) and value:
                cursors.append((cursor_type, value))
            item_content = content.get("itemContent")
            if isinstance(item_content, dict):
                tweet_results = item_content.get("tweet_results")
                if isinstance(tweet_results, dict):
                    tweet = _unwrap_tweet(tweet_results.get("result"))
                    if tweet:
                        tid = str(tweet.get("rest_id") or "")
                        if tid and tid not in seen:
                            seen.add(tid)
                            tweets.append(tweet)

        for value in node.values():
            walk(value, depth + 1)

    walk(data.get("data") if isinstance(data.get("data"), dict) else data)
    bottom = next((value for kind, value in cursors if kind == "Bottom"), None)
    return TimelinePage(tweets=tweets, bottom_cursor=bottom)


def parse_user_id(data: dict[str, Any]) -> str:
    """``rest_id`` from a UserByScreenName response ("" when absent)."""
    found: list[str] = []

    def walk(node: Any, depth: int = 0) -> None:
        if depth > _MAX_DEPTH or found:
            return
        if isinstance(node, list):
            for item in node:
                walk(item, depth + 1)
            return
        if not isinstance(node, dict):
            return
        if node.get("__typename") == "User" and node.get("rest_id"):
            found.append(str(node["rest_id"]))
            return
        for value in node.values():
            walk(value, depth + 1)

    walk(data.get("data") if isinstance(data.get("data"), dict) else data)
    return found[0] if found else ""


def parse_user_profile(data: dict[str, Any]) -> dict[str, Any]:
    """Profile fields from a UserByScreenName response ({} when absent)."""
    result: dict[str, Any] = {}

    def walk(node: Any, depth: int = 0) -> None:
        if depth > _MAX_DEPTH or result:
            return
        if isinstance(node, list):
            for item in node:
                walk(item, depth + 1)
            return
        if not isinstance(node, dict):
            return
        if node.get("__typename") == "User":
            legacy = node.get("legacy") or {}
            core = node.get("core") or {}
            result.update({
                "rest_id": str(node.get("rest_id") or ""),
                "screen_name": core.get("screen_name") or "",
                "name": core.get("name") or "",
                "bio": legacy.get("description") or "",
                "tweets_count": _int(legacy.get("statuses_count")),
                "followers": _int(legacy.get("followers_count")),
                "following": _int(legacy.get("friends_count")),
                "joined": legacy.get("created_at") or "",
            })
            return
        for value in node.values():
            walk(value, depth + 1)

    walk(data.get("data") if isinstance(data.get("data"), dict) else data)
    return result


def _media_urls(tweet: dict[str, Any]) -> list[str]:
    legacy = tweet.get("legacy") or {}
    entities = legacy.get("extended_entities") or legacy.get("entities") or {}
    urls: list[str] = []
    for media in (entities.get("media") or []):
        if not isinstance(media, dict):
            continue
        url = media.get("media_url_https") or media.get("media_url")
        if isinstance(url, str) and url:
            urls.append(url)
        variants = ((media.get("video_info") or {}).get("variants") or [])
        best, best_bitrate = None, -1
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            if variant.get("content_type") != "video/mp4" or not variant.get("url"):
                continue
            bitrate = _int(variant.get("bitrate"))
            if bitrate > best_bitrate:
                best, best_bitrate = variant["url"], bitrate
        if best:
            urls.append(best)
    return list(dict.fromkeys(urls))


def _links(tweet: dict[str, Any]) -> list[str]:
    legacy = tweet.get("legacy") or {}
    entities = legacy.get("entities") or {}
    links: list[str] = []
    for item in (entities.get("urls") or []):
        if not isinstance(item, dict):
            continue
        link = item.get("expanded_url") or item.get("url")
        if isinstance(link, str) and link and link not in links:
            links.append(link)
    return links


def _full_text(tweet: dict[str, Any]) -> str:
    """Prefer the long-form ``note_tweet`` body when the tweet has one."""
    note = (((tweet.get("note_tweet") or {}).get("note_tweet_results") or {})
            .get("result") or {})
    text = note.get("text")
    if isinstance(text, str) and text.strip():
        return text
    legacy = tweet.get("legacy") or {}
    return legacy.get("full_text") or legacy.get("text") or ""


def tweet_to_entry(tweet: dict[str, Any]) -> dict[str, Any]:
    """GraphQL tweet -> model-friendly entry (same shape as syndication)."""
    legacy = tweet.get("legacy") or {}
    core = ((tweet.get("core") or {}).get("user_results") or {}).get("result") or {}
    screen = (core.get("core") or {}).get("screen_name") or ""
    created_at = legacy.get("created_at") or ""

    entry: dict[str, Any] = {
        "author": f"@{screen}" if screen else "",
        "author_name": (core.get("core") or {}).get("name") or screen,
        "text": _full_text(tweet),
        "time_ago": relative_time(created_at),
        "likes": _int(legacy.get("favorite_count")),
        "retweets": _int(legacy.get("retweet_count")),
        "replies": _int(legacy.get("reply_count")),
        "views": _int((tweet.get("views") or {}).get("count")
                      if isinstance(tweet.get("views"), dict) else tweet.get("views")),
        "tweet_id": str(tweet.get("rest_id") or ""),
        "media": _media_urls(tweet),
        "urls": _links(tweet),
        "created_at": created_at,
        "lang": legacy.get("lang") or "",
    }

    in_reply_to = legacy.get("in_reply_to_status_id_str")
    if in_reply_to:
        entry["in_reply_to_status_id"] = str(in_reply_to)

    rt_id = legacy.get("retweeted_status_id_str")
    if rt_id:
        entry["retweeted_status_id"] = str(rt_id)
    rt = tweet.get("retweeted_status_result")
    if isinstance(rt, dict):
        original = _unwrap_tweet((rt.get("result") if isinstance(rt, dict) else None))
        if original:
            original_entry = tweet_to_entry(original)
            entry["retweeted_status_id"] = original_entry.get("tweet_id", "")
            entry["retweeted_by"] = entry["author"]
            for key in ("author", "author_name", "text", "media", "created_at"):
                if original_entry.get(key):
                    entry[key if key != "created_at" else "created_at"] = original_entry[key]
            entry["likes"] = original_entry.get("likes", entry["likes"])
            entry["retweets"] = original_entry.get("retweets", entry["retweets"])

    quoted_id = legacy.get("quoted_status_id_str")
    quoted = tweet.get("quoted_status_result")
    if isinstance(quoted, dict):
        quoted_tweet = _unwrap_tweet(quoted.get("result"))
        if quoted_tweet:
            quoted_entry = tweet_to_entry(quoted_tweet)
            entry["quoted_status_id"] = quoted_entry.get("tweet_id", str(quoted_id or ""))
            entry["quoted_tweet"] = quoted_entry
    elif quoted_id:
        entry["quoted_status_id"] = str(quoted_id)

    return entry


def sort_entries_newest_first(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order by absolute time, newest first (mirrors the syndication parser)."""
    from datetime import datetime, timezone

    def stamp(entry: dict[str, Any]) -> float:
        raw = entry.get("created_at") or ""
        try:
            parsed = datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
        except (ValueError, TypeError):
            return float("-inf")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    return sorted(entries, key=stamp, reverse=True)
