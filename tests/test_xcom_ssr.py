"""xcom SSR parser tests (pure, no network)."""
from __future__ import annotations

import json

from xtf.parsers.xcom_ssr import (
    extract_profile_user,
    extract_tweet_objects,
    parse_profile_timeline,
    unescape_payload,
)


def _tweet_obj(tid: str, screen: str = "alice", name: str = "Alice",
               ms: int = 1_760_000_000_000, text: str = "hello",
               fav: int = 5, media: bool = False) -> dict:
    obj = {
        "__isTweetResult": "Tweet",
        "__typename": "Tweet",
        "core": {"user_results": {"result": {"core": {"screen_name": screen, "name": name}}}},
        "counts": {"favorite_count": fav, "retweet_count": 1, "reply_count": 2,
                   "quote_count": 0, "bookmark_count": 3},
        "details": {"created_at_ms": ms, "full_text": text},
        "edit_control": {"edit_tweet_ids": [tid]},
        "rest_id": tid,
        "views": {"count": "1234"},
    }
    if media:
        obj["media_entities2"] = [{"media_url_https": "https://pbs.twimg.com/media/x.jpg"}]
    return obj


def _page(objects: list[dict]) -> str:
    """Build a page with the same double-escaped, compact payload x.com serves."""
    inner = json.dumps({"data": {"tweets": objects}}, separators=(",", ":"))
    escaped = inner.replace('"', '\\"')
    return f'<html><script>window.__payload="{escaped}"</script></body></html>'


# ── unescaping ───────────────────────────────────────────────────────────
def test_unescape_restores_inner_quotes_only():
    raw = 'a="{\\"k\\":\\"v\\"}"'
    assert unescape_payload(raw) == 'a="{"k":"v"}"'


# ── object extraction ────────────────────────────────────────────────────
def test_extract_finds_tweet_objects():
    html = _page([_tweet_obj("1"), _tweet_obj("2")])
    assert len(extract_tweet_objects(html)) == 2


def test_extract_dedupes_across_anchors():
    """The same tweet matches both anchors but must be reported once."""
    html = _page([_tweet_obj("42")])
    assert len(extract_tweet_objects(html)) == 1


def test_extract_survives_braces_inside_text():
    html = _page([_tweet_obj("7", text="a {weird} body with } braces {")])
    objs = extract_tweet_objects(html)
    assert len(objs) == 1
    assert parse_profile_timeline(html)[0]["text"] == "a {weird} body with } braces {"


# ── normalization ────────────────────────────────────────────────────────
def test_entry_fields_mapped():
    html = _page([_tweet_obj("1001", text="hi", fav=9, media=True)])
    entry = parse_profile_timeline(html)[0]
    assert entry["author"] == "@alice"
    assert entry["author_name"] == "Alice"
    assert entry["text"] == "hi"
    assert entry["likes"] == 9
    assert entry["views"] == 1234
    assert entry["tweet_id"] == "1001"
    assert entry["media"] == ["https://pbs.twimg.com/media/x.jpg"]
    assert entry["created_at"].endswith("2025") or entry["created_at"].endswith("2026")


def test_newlines_and_entities_are_restored():
    html = _page([_tweet_obj("1", text="line1\\nline2 &gt; ok &amp; more")])
    text = parse_profile_timeline(html)[0]["text"]
    assert text == "line1\nline2 > ok & more"


def test_sorted_newest_first_and_limited():
    html = _page([
        _tweet_obj("1", ms=1_700_000_000_000),
        _tweet_obj("3", ms=1_800_000_000_000),
        _tweet_obj("2", ms=1_750_000_000_000),
    ])
    assert [e["tweet_id"] for e in parse_profile_timeline(html)] == ["3", "2", "1"]
    assert [e["tweet_id"] for e in parse_profile_timeline(html, limit=2)] == ["3", "2"]


# ── author filtering ─────────────────────────────────────────────────────
def test_author_filter_keeps_only_that_account():
    """Quoted/replied tweets by others must not pollute a profile timeline."""
    html = _page([
        _tweet_obj("1", screen="alice"),
        _tweet_obj("2", screen="bob", name="Bob"),
    ])
    entries = parse_profile_timeline(html, author="alice")
    assert [e["tweet_id"] for e in entries] == ["1"]
    # case-insensitive, @-tolerant
    assert len(parse_profile_timeline(html, author="@ALICE")) == 1
    # no filter -> both
    assert len(parse_profile_timeline(html)) == 2


def test_quoted_tweet_kept_as_context():
    quoted = _tweet_obj("5", screen="carol", text="quoted body")
    outer = _tweet_obj("6", screen="alice", text="my take")
    outer["quoted_tweet_results"] = quoted
    entry = parse_profile_timeline(_page([outer]), author="alice")[0]
    assert entry["quoted_status_id"] == "5"
    assert entry["quoted_tweet"]["text"] == "quoted body"


def test_entries_without_id_are_dropped():
    broken = _tweet_obj("9")
    broken["edit_control"] = {}
    broken.pop("rest_id")
    html = _page([broken, _tweet_obj("10")])
    assert [e["tweet_id"] for e in parse_profile_timeline(html)] == ["10"]


def test_no_payload_yields_empty():
    assert parse_profile_timeline("<html>nothing here</html>") == []
    assert extract_tweet_objects("") == []


# ── profile extraction ───────────────────────────────────────────────────
def test_extract_profile_user_reads_counts():
    html = _page([_tweet_obj("1", screen="serenity")])
    user = extract_profile_user(html)
    assert user.get("screen_name") == "serenity"


def test_extract_profile_user_empty_when_absent():
    assert extract_profile_user("<html></html>") == {}
