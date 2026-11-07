"""xauth GraphQL parser tests (pure, no network)."""
from __future__ import annotations

import json

from xtf.parsers.x_graphql import (
    build_variables,
    load_template,
    parse_page,
    parse_user_id,
    parse_user_profile,
    sort_entries_newest_first,
    tweet_to_entry,
)


def _tweet(tid="100", screen="alice", name="Alice", text="hello", fav=5, rt=1, rp=2,
           created="Sat Nov 07 12:05:47 +0000 2026", media=None, note=None,
           in_reply_to=None, quoted=None, retweeted=None):
    legacy = {
        "full_text": text,
        "created_at": created,
        "favorite_count": fav,
        "retweet_count": rt,
        "reply_count": rp,
        "lang": "en",
        "entities": {},
    }
    if in_reply_to:
        legacy["in_reply_to_status_id_str"] = in_reply_to
    if quoted:
        legacy["quoted_status_id_str"] = quoted
    if media:
        legacy["extended_entities"] = {"media": media}
    result = {
        "__typename": "Tweet",
        "rest_id": tid,
        "legacy": legacy,
        "core": {"user_results": {"result": {"core": {"screen_name": screen, "name": name}}}},
        "views": {"count": "1234"},
    }
    if note:
        result["note_tweet"] = {"note_tweet_results": {"result": {"text": note}}}
    return result


def _timeline_response(tweets, cursor="CURSOR_BOTTOM"):
    """Shape mirrors X: data.user.result.timeline.timeline.instructions."""
    entries = [
        {"entryId": f"tweet-{t['rest_id']}",
         "content": {"entryType": "TimelineTimelineItem",
                     "itemContent": {"itemType": "TimelineTweet",
                                     "tweet_results": {"result": t}}}}
        for t in tweets
    ]
    if cursor:
        entries.append({
            "entryId": "cursor-bottom",
            "content": {"entryType": "TimelineTimelineCursor",
                        "cursorType": "Bottom", "value": cursor},
        })
    return {"data": {"user": {"result": {"timeline": {"timeline": {
        "instructions": [{"type": "TimelineAddEntries", "entries": entries}]
    }}}}}}


# ── template ─────────────────────────────────────────────────────────────
def test_bundled_template_loads():
    template = load_template()
    assert "UserByScreenName" in template
    assert "UserOriginalsTimeline" in template
    for op in ("UserByScreenName", "UserOriginalsTimeline"):
        entry = template[op]
        assert entry["queryId"]
        json.loads(entry["features"])          # must be valid JSON
        json.loads(entry["fieldToggles"] or "{}")


def test_build_variables_merges_overrides():
    template = load_template()
    variables = build_variables("UserOriginalsTimeline", template,
                                {"userId": "42", "count": 20, "cursor": "abc"})
    assert variables["userId"] == "42"
    assert variables["cursor"] == "abc"
    # template defaults survive
    assert "withVoice" in variables


# ── page parsing ─────────────────────────────────────────────────────────
def test_parse_page_extracts_tweets_and_cursor():
    page = parse_page(_timeline_response([_tweet("1"), _tweet("2")]))
    assert [t["rest_id"] for t in page.tweets] == ["1", "2"]
    assert page.bottom_cursor == "CURSOR_BOTTOM"
    assert page.has_more is True


def test_parse_page_dedupes_and_handles_missing_cursor():
    page = parse_page(_timeline_response([_tweet("1"), _tweet("1")], cursor=None))
    assert len(page.tweets) == 1
    assert page.bottom_cursor is None and page.has_more is False


def test_parse_page_unwraps_visibility_wrapper():
    wrapped = {"__typename": "TweetWithVisibilityResults", "tweet": _tweet("7")}
    response = _timeline_response([])
    response["data"]["user"]["result"]["timeline"]["timeline"]["instructions"][0]["entries"] = [
        {"content": {"itemContent": {"tweet_results": {"result": wrapped}}}}
    ]
    page = parse_page(response)
    assert [t["rest_id"] for t in page.tweets] == ["7"]


def test_parse_page_ignores_promoted_noise():
    entry = {"content": {"itemContent": {"tweet_results": {"result": {"__typename": "User"}}}}}
    response = {"data": {"entries": [entry]}}
    assert parse_page(response).tweets == []


# ── tweet normalization ──────────────────────────────────────────────────
def test_tweet_to_entry_maps_fields():
    entry = tweet_to_entry(_tweet())
    assert entry["author"] == "@alice"
    assert entry["author_name"] == "Alice"
    assert entry["text"] == "hello"
    assert entry["likes"] == 5
    assert entry["retweets"] == 1
    assert entry["replies"] == 2
    assert entry["views"] == 1234
    assert entry["tweet_id"] == "100"
    assert entry["created_at"].startswith("Sat Nov 07")
    assert entry["lang"] == "en"


def test_note_tweet_text_wins():
    entry = tweet_to_entry(_tweet(text="short…", note="the full long-form body"))
    assert entry["text"] == "the full long-form body"


def test_reply_and_quote_ids_recorded():
    quoted = _tweet("9", text="quoted body")
    entry = tweet_to_entry(_tweet("8", in_reply_to="777", quoted="555"))
    assert entry["in_reply_to_status_id"] == "777"
    assert entry["quoted_status_id"] == "555"
    # a quoted result is carried through as context
    tweet = _tweet("10")
    tweet["quoted_status_result"] = {"result": quoted}
    assert tweet_to_entry(tweet)["quoted_tweet"]["text"] == "quoted body"


def test_media_urls_include_best_video():
    media = [{"media_url_https": "https://pbs.twimg.com/media/a.jpg",
              "video_info": {"variants": [
                  {"content_type": "video/mp4", "bitrate": 100, "url": "https://v/low.mp4"},
                  {"content_type": "video/mp4", "bitrate": 900, "url": "https://v/high.mp4"},
              ]}}]
    assert tweet_to_entry(_tweet(media=media))["media"] == [
        "https://pbs.twimg.com/media/a.jpg", "https://v/high.mp4"]


def test_retweet_unwraps_original():
    original = _tweet("20", screen="author", text="original text", fav=99)
    shell = _tweet("21", screen="sharer", text="RT @author: original text")
    shell["retweeted_status_result"] = {"result": original}
    entry = tweet_to_entry(shell)
    assert entry["author"] == "@author"
    assert entry["text"] == "original text"
    assert entry["retweeted_by"] == "@sharer"


def test_sort_entries_newest_first():
    entries = [
        tweet_to_entry(_tweet("1", created="Mon Nov 02 13:59:35 +0000 2026")),
        tweet_to_entry(_tweet("3", created="Sat Nov 07 12:05:47 +0000 2026")),
        tweet_to_entry(_tweet("2", created="Wed Nov 04 10:00:00 +0000 2026")),
    ]
    assert [e["tweet_id"] for e in sort_entries_newest_first(entries)] == ["3", "2", "1"]


# ── user lookups ─────────────────────────────────────────────────────────
def test_parse_user_id_and_profile():
    response = {"data": {"user": {"result": {
        "__typename": "User",
        "rest_id": "1940360837547565056",
        "core": {"screen_name": "serenity", "name": "Serenity"},
        "legacy": {"description": "bio", "statuses_count": 7899,
                   "followers_count": 1066100, "friends_count": 214,
                   "created_at": "Wed Jul 02 10:44:15 +0000 2025"},
    }}}}
    assert parse_user_id(response) == "1940360837547565056"
    profile = parse_user_profile(response)
    assert profile["screen_name"] == "serenity"
    assert profile["tweets_count"] == 7899
    assert profile["followers"] == 1066100


def test_parse_user_id_missing_returns_empty():
    assert parse_user_id({"data": {}}) == ""
    assert parse_user_profile({"data": {}}) == {}
