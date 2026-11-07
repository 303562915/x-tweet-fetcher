"""Syndication backend parser + model tests (pure, no network)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from xtf.exceptions import RateLimited, UpstreamDown
from xtf.models import Tweet
from xtf.parsers.syndication_json import (
    extract_entries,
    extract_links,
    extract_media,
    extract_timeline_user,
    extract_views,
    parse_entries,
    parse_next_data,
    parse_timeline,
    relative_time,
    tweet_to_entry,
)


def _page(entries: list[dict], user: dict | None = None) -> str:
    """Wrap raw tweets in a minimal syndication-shaped __NEXT_DATA__ page."""
    data = {
        "props": {
            "pageProps": {
                "timeline": {"entries": [{"type": "tweet", "content": {"tweet": t}} for t in entries]},
                "headerProps": {"user": user} if user else {},
            }
        },
        "buildId": "test",
    }
    return (
        "<!DOCTYPE html><html><body>"
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
        "</body></html>"
    )


def _tweet(**over):
    base = {
        "id": 0,
        "id_str": "1001",
        "conversation_id_str": "1001",
        "created_at": "Fri Nov 06 15:47:47 +0000 2026",
        "full_text": "hello world",
        "text": "hello world",
        "favorite_count": 12,
        "retweet_count": 3,
        "reply_count": 4,
        "quote_count": 1,
        "lang": "en",
        "permalink": "/someone/status/1001",
        "entities": {"media": [], "urls": []},
        "ext_views": {"count": "1234"},
        "user": {"screen_name": "someone", "name": "Some One", "statuses_count": 9,
                 "followers_count": 10, "friends_count": 11},
    }
    base.update(over)
    return base


# ── page parsing ─────────────────────────────────────────────────────────
def test_parse_next_data_roundtrip():
    data = parse_next_data(_page([_tweet()]))
    assert len(extract_entries(data)) == 1


def test_parse_next_data_missing_blob_raises():
    with pytest.raises(UpstreamDown):
        parse_next_data("<html><body>no data here</body></html>")


def test_parse_next_data_bad_json_raises():
    html = '<script id="__NEXT_DATA__" type="application/json">{not json}</script>'
    with pytest.raises(UpstreamDown):
        parse_next_data(html)


def test_extract_entries_skips_non_tweet_entries():
    data = {
        "props": {"pageProps": {"timeline": {"entries": [
            {"type": "cursor", "content": {}},
            {"type": "tweet", "content": {"tweet": _tweet()}},
        ]}}}
    }
    assert len(extract_entries(data)) == 1


# ── entry normalization ──────────────────────────────────────────────────
def test_tweet_to_entry_maps_stats_and_author():
    entry = tweet_to_entry(_tweet())
    assert entry["author"] == "@someone"
    assert entry["author_name"] == "Some One"
    assert entry["text"] == "hello world"
    assert (entry["likes"], entry["retweets"], entry["replies"]) == (12, 3, 4)
    assert entry["views"] == 1234
    assert entry["tweet_id"] == "1001"
    assert entry["lang"] == "en"
    assert entry["created_at"] == "Fri Nov 06 15:47:47 +0000 2026"
    assert "retweeted_by" not in entry


def test_retweet_unwraps_original_and_marks_retweeter():
    original = _tweet(id_str="2002", full_text="original text",
                      user={"screen_name": "author", "name": "Author"})
    outer = _tweet(id_str="3003", full_text="RT @author: original text",
                   user={"screen_name": "sharer", "name": "Sharer"},
                   retweeted_status=original)
    entry = tweet_to_entry(outer)
    assert entry["author"] == "@author"
    assert entry["text"] == "original text"
    assert entry["tweet_id"] == "2002"
    assert entry["retweeted_by"] == "@sharer"
    assert entry["retweeted_status_id"] == "2002"


def test_quote_embeds_quoted_tweet():
    quoted = _tweet(id_str="5005", full_text="quoted body")
    outer = _tweet(id_str="6006", quoted_status=quoted)
    entry = tweet_to_entry(outer)
    assert entry["quoted_status_id"] == "5005"
    assert entry["quoted_tweet"]["text"] == "quoted body"
    assert entry["quoted_tweet"]["tweet_id"] == "5005"


def test_reply_target_recorded():
    entry = tweet_to_entry(_tweet(in_reply_to_status_id_str="777"))
    assert entry["in_reply_to_status_id"] == "777"


def test_media_and_links_extracted():
    tweet = _tweet(
        extended_entities={"media": [
            {"media_url_https": "https://pbs.twimg.com/media/a.jpg"},
            {"media_url_https": "https://pbs.twimg.com/media/b.jpg",
             "video_info": {"variants": [
                 {"content_type": "video/mp4", "bitrate": 256000, "url": "https://v/low.mp4"},
                 {"content_type": "video/mp4", "bitrate": 2176000, "url": "https://v/high.mp4"},
             ]}},
        ]},
        entities={"urls": [{"url": "https://t.co/x", "expanded_url": "https://example.com/a"}]},
    )
    assert extract_media(tweet) == [
        "https://pbs.twimg.com/media/a.jpg",
        "https://pbs.twimg.com/media/b.jpg",
        "https://v/high.mp4",
    ]
    assert extract_links(tweet) == ["https://example.com/a"]


def test_views_accepts_int_string_and_missing():
    assert extract_views({"ext_views": {"count": "42"}}) == 42
    assert extract_views({"views": 7}) == 7
    assert extract_views({"ext_views": {"count": "1,234"}}) == 1234
    assert extract_views({}) == 0


# ── relative time ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("minutes_ago", "expected"),
    [(0, "0s"), (5, "5m"), (180, "3h"), (2880, "2d")],
)
def test_relative_time_buckets(minutes_ago, expected):
    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    stamp = now.timestamp() - minutes_ago * 60
    created = datetime.fromtimestamp(stamp, tz=timezone.utc).strftime(
        "%a %b %d %H:%M:%S +0000 %Y"
    )
    assert relative_time(created, now=now) == expected


def test_relative_time_handles_garbage():
    assert relative_time("") == ""
    assert relative_time("not a date") == ""


# ── model integration ────────────────────────────────────────────────────
def test_parse_timeline_respects_limit():
    html = _page([_tweet(id_str=str(i)) for i in range(10)])
    assert len(parse_timeline(html)) == 10
    assert len(parse_timeline(html, limit=3)) == 3


def test_parse_entries_sorts_newest_first():
    """A "top tweets" style page must still come out in strict time order."""
    html = _page([
        _tweet(id_str="1", created_at="Fri Nov 06 15:47:47 +0000 2026"),
        _tweet(id_str="2", created_at="Thu Apr 28 00:56:58 +0000 2022"),
        _tweet(id_str="3", created_at="Wed Nov 06 05:01:15 +0000 2024"),
    ])
    entries = parse_entries(parse_next_data(html))
    assert [e["tweet_id"] for e in entries] == ["1", "3", "2"]
    # the limit applies after sorting
    assert [e["tweet_id"] for e in parse_entries(parse_next_data(html), limit=2)] == ["1", "3"]


def test_parse_entries_keeps_unparseable_timestamps_last():
    html = _page([
        _tweet(id_str="bad", created_at="not a date"),
        _tweet(id_str="good", created_at="Fri Nov 06 15:47:47 +0000 2026"),
    ])
    assert [e["tweet_id"] for e in parse_entries(parse_next_data(html))] == ["good", "bad"]


def test_tweet_model_carries_metadata_into_dict():
    tweet = Tweet.from_syndication_entry(tweet_to_entry(
        _tweet(entities={"urls": [{"expanded_url": "https://example.com/a"}], "media": []})
    ))
    d = tweet.to_dict()
    assert d["tweet_id"] == "1001"
    assert d["lang"] == "en"
    assert d["created_at"] == "Fri Nov 06 15:47:47 +0000 2026"
    assert d["urls"] == ["https://example.com/a"]


def test_plain_tweet_dict_omits_metadata_fields():
    plain = Tweet(author="@x", text="t").to_dict()
    for key in ("created_at", "lang", "urls", "in_reply_to_status_id",
                "retweeted_status_id", "quoted_status_id"):
        assert key not in plain


def test_extract_timeline_user_prefers_entry_user():
    data = parse_next_data(_page([_tweet()], user={"screen_name": "header"}))
    assert extract_timeline_user(data)["screen_name"] == "someone"
    assert extract_timeline_user({"props": {"pageProps": {}}}) == {}


# ── rate-limit waiting ───────────────────────────────────────────────────
def test_rate_limited_wait_seconds():
    now = 1_000.0
    assert RateLimited("x", reset_at=1_030.0).wait_seconds(now) == 31
    assert RateLimited("x", retry_after=5).wait_seconds(now) == 5
    # already past the reset — no sleep needed
    assert RateLimited("x", reset_at=900.0).wait_seconds(now) == 0
    # nothing to go on
    assert RateLimited("x").wait_seconds(now) is None


def test_syndication_waits_for_quota_then_succeeds(monkeypatch, capsys):
    """A 429 carrying x-rate-limit-reset is slept out when a budget allows."""
    from xtf import http as http_mod
    from xtf.backends.syndication import SyndicationBackend

    calls = {"n": 0}
    slept: list[float] = []

    def fake_get(url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RateLimited("HTTP 429", reset_at=time.time() + 7)
        return _page([_tweet()])

    monkeypatch.setattr(http_mod, "get_text", fake_get)
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))

    backend = SyndicationBackend(wait_budget=60)
    tweets = backend.fetch_timeline("someone", limit=5)

    assert calls["n"] == 2
    assert len(slept) == 1 and 1 <= slept[0] <= 10
    assert len(tweets) == 1 and tweets[0].tweet_id == "1001"


def test_syndication_fails_fast_without_budget(monkeypatch):
    """Without a wait budget the backend must not sleep at all."""
    from xtf import http as http_mod
    from xtf.backends.syndication import SyndicationBackend

    slept: list[float] = []
    monkeypatch.setattr(http_mod, "get_text", lambda url, **kw: (_ for _ in ()).throw(
        RateLimited("HTTP 429", reset_at=time.time() + 600)))
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))

    backend = SyndicationBackend(wait_budget=0)
    with pytest.raises(UpstreamDown) as ei:
        backend.fetch_timeline("someone", limit=5)
    assert not slept
    assert "quota exhausted" in str(ei.value)


def test_syndication_does_not_wait_beyond_budget(monkeypatch):
    """A reset further out than the budget fails immediately instead."""
    from xtf import http as http_mod
    from xtf.backends.syndication import SyndicationBackend

    slept: list[float] = []
    monkeypatch.setattr(http_mod, "get_text", lambda url, **kw: (_ for _ in ()).throw(
        RateLimited("HTTP 429", reset_at=time.time() + 900)))
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))

    backend = SyndicationBackend(wait_budget=60)
    with pytest.raises(UpstreamDown):
        backend.fetch_timeline("someone", limit=5)
    assert not slept
