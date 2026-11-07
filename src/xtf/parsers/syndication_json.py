"""Parser for X's public syndication timeline payload.

``https://syndication.twitter.com/srv/timeline-profile/screen-name/<user>``
is a login-free X-owned endpoint: it renders a Next.js page whose
``__NEXT_DATA__`` JSON carries a ``timeline.entries`` list of full tweet
objects (text, stats, media, quotes, retweets, absolute timestamps).

All functions here are pure: string in, dict/list out. No network, no I/O.

Notes on the payload:
  * ``id`` is always ``0``; the real id lives in ``id_str``.
  * A retweet wraps the original under ``retweeted_status``; the outer tweet
    is then just a shell, so the original supplies text and stats while the
    outer author is reported as ``retweeted_by``.
  * ``created_at`` is a Twitter-format string ("Fri Nov 06 15:47:47 +0000
    2026"); ``time_ago`` is derived from it so output stays comparable with
    the Nitter backend.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from ..exceptions import UpstreamDown

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL
)

#: Twitter's absolute timestamp format, e.g. "Fri Nov 06 15:47:47 +0000 2026"
_TWITTER_TIME = "%a %b %d %H:%M:%S %z %Y"

#: Guards against pathological nesting in quoted/retweeted chains.
_MAX_DEPTH = 3


def parse_next_data(html: str) -> dict[str, Any]:
    """Extract the ``__NEXT_DATA__`` JSON blob from a syndication page.

    Raises ``UpstreamDown`` when the blob is missing (blocked, error page, or
    an upstream markup change) or is not valid JSON.
    """
    match = _NEXT_DATA_RE.search(html or "")
    if not match:
        raise UpstreamDown(
            "syndication: __NEXT_DATA__ not found (blocked, rate limited, or markup changed)"
        )
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        raise UpstreamDown(f"syndication: invalid __NEXT_DATA__ JSON: {e}") from e
    if not isinstance(data, dict):
        raise UpstreamDown("syndication: unexpected __NEXT_DATA__ shape")
    return data


def extract_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the raw tweet objects from ``props.pageProps.timeline.entries``."""
    page_props = ((data.get("props") or {}).get("pageProps") or {})
    timeline = page_props.get("timeline") or {}
    entries = timeline.get("entries") or []
    if not isinstance(entries, list):
        return []
    tweets: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("type") != "tweet":
            continue
        tweet = (entry.get("content") or {}).get("tweet")
        if isinstance(tweet, dict):
            tweets.append(tweet)
    return tweets


def extract_timeline_user(data: dict[str, Any]) -> dict[str, Any]:
    """Best-effort profile dict for the timeline owner (empty dict if absent)."""
    for tweet in extract_entries(data):
        user = tweet.get("user")
        if isinstance(user, dict) and user.get("screen_name"):
            return user
    header = ((data.get("props") or {}).get("pageProps") or {}).get("headerProps") or {}
    if isinstance(header, dict):
        user = header.get("user")
        if isinstance(user, dict):
            return user
    return {}


def relative_time(created_at: str, now: datetime | None = None) -> str:
    """Twitter timestamp -> compact age ("42s" / "17m" / "3h" / "2d" / "5w")."""
    if not created_at:
        return ""
    try:
        stamp = datetime.strptime(created_at, _TWITTER_TIME)
    except (ValueError, TypeError):
        return ""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    seconds = int((now - stamp).total_seconds())
    if seconds < 0:
        return "now"
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    if days < 7:
        return f"{days}d"
    weeks = days // 7
    if weeks < 5:
        return f"{weeks}w"
    return stamp.strftime("%Y-%m-%d")


def _int(value: Any) -> int:
    """Coerce counts that arrive as int, numeric string, or None."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        digits = value.replace(",", "").strip()
        if digits.isdigit():
            return int(digits)
    return 0


def extract_views(tweet: dict[str, Any]) -> int:
    """View count from ``ext_views``/``views`` (widget payloads vary)."""
    for key in ("ext_views", "views"):
        value = tweet.get(key)
        if isinstance(value, dict):
            for sub in ("count", "views_count"):
                if value.get(sub) is not None:
                    return _int(value[sub])
        elif value is not None:
            return _int(value)
    return 0


def extract_media(tweet: dict[str, Any]) -> list[str]:
    """Media URLs (images at full size, best-bitrate mp4 for videos/GIFs)."""
    entities = tweet.get("extended_entities") or tweet.get("entities") or {}
    if not isinstance(entities, dict):
        return []
    urls: list[str] = []
    for media in entities.get("media") or []:
        if not isinstance(media, dict):
            continue
        url = media.get("media_url_https") or media.get("media_url")
        if isinstance(url, str) and url:
            urls.append(url)
        variants = ((media.get("video_info") or {}).get("variants") or [])
        best = None
        best_bitrate = -1
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
    return urls


def extract_links(tweet: dict[str, Any]) -> list[str]:
    """Expanded t.co targets from ``entities.urls``."""
    entities = tweet.get("entities") or {}
    if not isinstance(entities, dict):
        return []
    links: list[str] = []
    for item in entities.get("urls") or []:
        if not isinstance(item, dict):
            continue
        link = item.get("expanded_url") or item.get("url")
        if isinstance(link, str) and link and link not in links:
            links.append(link)
    return links


def _screen_name(tweet: dict[str, Any]) -> str:
    user = tweet.get("user")
    if isinstance(user, dict):
        return user.get("screen_name") or ""
    return ""


def tweet_to_entry(tweet: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """Normalize one syndication tweet into a model-friendly entry dict.

    Retweets are unwrapped: text/stats/author come from the original tweet and
    the outer account is reported through ``retweeted_by``.
    """
    original = tweet.get("retweeted_status")
    core = original if isinstance(original, dict) else tweet

    screen = _screen_name(core) or _screen_name(tweet)
    user = core.get("user") if isinstance(core.get("user"), dict) else {}
    entry: dict[str, Any] = {
        "author": f"@{screen}" if screen else "",
        "author_name": user.get("name") or screen,
        "text": core.get("full_text") or core.get("text") or "",
        "time_ago": relative_time(core.get("created_at") or tweet.get("created_at") or ""),
        "likes": _int(core.get("favorite_count")),
        "retweets": _int(core.get("retweet_count")),
        "replies": _int(core.get("reply_count")),
        "views": extract_views(core),
        "tweet_id": str(core.get("id_str") or core.get("id") or ""),
        "media": extract_media(core),
        "urls": extract_links(core),
        "created_at": core.get("created_at") or tweet.get("created_at") or "",
        "lang": core.get("lang") or tweet.get("lang") or "",
    }

    retweeted_id = str(core.get("id_str") or "") if isinstance(original, dict) else ""
    if retweeted_id:
        entry["retweeted_status_id"] = retweeted_id
        outer = _screen_name(tweet)
        if outer:
            entry["retweeted_by"] = f"@{outer}"

    in_reply_to = tweet.get("in_reply_to_status_id_str") or tweet.get("in_reply_to_status_id")
    if in_reply_to:
        entry["in_reply_to_status_id"] = str(in_reply_to)

    quoted = tweet.get("quoted_status")
    if isinstance(quoted, dict) and depth < _MAX_DEPTH:
        entry["quoted_status_id"] = str(quoted.get("id_str") or quoted.get("id") or "")
        entry["quoted_tweet"] = tweet_to_entry(quoted, depth + 1)

    return entry


def parse_timeline(html: str, limit: int | None = None) -> list[dict[str, Any]]:
    """syndication timeline HTML -> list of normalized entry dicts (newest first)."""
    return parse_entries(parse_next_data(html), limit=limit)


def sort_entries_newest_first(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order entries by absolute time, newest first.

    Most profiles return a strict reverse-chronological page already, but a
    small number of very-high-engagement accounts are served a "top tweets"
    mix instead. Sorting makes the output ordering deterministic either way;
    entries with an unparseable timestamp keep their relative position at the
    end rather than being dropped.
    """
    def stamp(entry: dict[str, Any]) -> float:
        raw = entry.get("created_at") or ""
        try:
            parsed = datetime.strptime(raw, _TWITTER_TIME)
        except (ValueError, TypeError):
            return float("-inf")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    ordered = sorted(range(len(entries)), key=lambda i: stamp(entries[i]), reverse=True)
    return [entries[i] for i in ordered]


def parse_entries(data: dict[str, Any], limit: int | None = None) -> list[dict[str, Any]]:
    """Already-parsed ``__NEXT_DATA__`` blob -> normalized entries, newest first."""
    entries = sort_entries_newest_first(
        [tweet_to_entry(tw) for tw in extract_entries(data)]
    )
    if limit is not None and limit >= 0:
        return entries[:limit]
    return entries
