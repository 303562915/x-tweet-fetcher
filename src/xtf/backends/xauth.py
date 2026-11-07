"""Authenticated GraphQL backend — the full timeline, over plain HTTP.

Uses the same web endpoint the signed-in browser calls, so it can paginate
past the first screen: one page carries 20 tweets and the cursor walks back as
far as you ask. Measured on ``aleabitoreddit``: 40 tweets across two pages.

Credentials come from the environment (never from code):

  XTF_AUTH_TOKEN   the ``auth_token`` cookie
  XTF_CT0          the ``ct0`` cookie (used as x-csrf-token)

Both expire on X's schedule — ``auth_token`` typically lasts months.

Query ids and feature flags rotate and live in ``xtf/data/x_graphql.json``.
When X changes them the calls fail with a ``rate_limited``/``upstream_down``
error and the template can be recaptured with
``scripts/refresh_graphql_template.py`` (needs a browser).

Unlike the anonymous backends this one is a *signed-in* call, which is what
makes it viable from datacenter IPs where the anonymous endpoints answer 429.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request

from .. import config, http
from ..exceptions import BackendUnavailable, InvalidInput, NotFound, UpstreamDown, XtfError
from ..models import Profile, Tweet
from ..parsers.x_graphql import (
    build_variables,
    load_template,
    parse_page,
    parse_user_id,
    parse_user_profile,
    sort_entries_newest_first,
    tweet_to_entry,
)
from .base import Backend

API_ROOT = "https://x.com/i/api/graphql"
BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_MAX_PAGES = 12


class XAuthBackend(Backend):
    name = "xauth"

    def __init__(self, auth_token: str | None = None, ct0: str | None = None,
                 timeout: int = 30, template_path: str | None = None):
        self.auth_token = auth_token or config.auth_token()
        self.ct0 = ct0 or config.ct0()
        self.timeout = timeout
        self.template_path = template_path
        self._template: dict | None = None

    # ── setup ────────────────────────────────────────────────────────────
    @property
    def template(self) -> dict:
        if self._template is None:
            self._template = load_template(self.template_path)
        return self._template

    def available(self) -> bool:
        """Only usable when both cookies are present."""
        return bool(self.auth_token and self.ct0)

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": _UA,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Authorization": f"Bearer {BEARER}",
            "x-csrf-token": self.ct0,
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "en",
            "Content-Type": "application/json",
            "Cookie": f"auth_token={self.auth_token}; ct0={self.ct0}",
        }

    # ── transport ────────────────────────────────────────────────────────
    def _call(self, operation: str, variables: dict) -> dict:
        entry = self.template.get(operation)
        if not entry:
            raise BackendUnavailable(f"xauth: no template for {operation}")
        params = {
            "variables": json.dumps(variables, separators=(",", ":")),
            "features": entry.get("features") or "{}",
            "fieldToggles": entry.get("fieldToggles") or "{}",
        }
        url = (f"{API_ROOT}/{entry['queryId']}/{operation}"
               f"?{urllib.parse.urlencode(params)}")
        try:
            raw = http.get_text(url, headers=self._headers(), timeout=self.timeout, retries=1)
        except XtfError as e:
            raise UpstreamDown(
                f"xauth: {operation} failed ({e.code}) — cookies may be expired, "
                "or X rotated its query ids (refresh the template)"
            ) from e
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise UpstreamDown(f"xauth: {operation} returned non-JSON") from e
        errors = data.get("errors")
        if errors:
            message = errors[0].get("message", "") if isinstance(errors, list) else str(errors)
            if "not found" in message.lower():
                raise NotFound(f"xauth: {operation}: {message}")
            raise UpstreamDown(f"xauth: {operation} error: {message}")
        return data

    # ── capabilities ─────────────────────────────────────────────────────
    def _user_id(self, username: str) -> str:
        variables = build_variables("UserByScreenName", self.template,
                                    {"screen_name": username,
                                     "withGrokTranslatedBio": True})
        data = self._call("UserByScreenName", variables)
        user_id = parse_user_id(data)
        if not user_id:
            raise NotFound(f"xauth: user @{username} not found")
        return user_id

    def fetch_timeline(self, username: str, limit: int = 20) -> list[Tweet]:
        clean = (username or "").strip().lstrip("@")
        if not clean:
            raise InvalidInput("xauth: empty username")
        user_id = self._user_id(clean)

        collected: dict[str, dict] = {}
        cursor: str | None = None
        page = 0
        while len(collected) < limit and page < _MAX_PAGES:
            page += 1
            overrides: dict = {"userId": user_id, "count": 20}
            if cursor:
                overrides["cursor"] = cursor
            variables = build_variables("UserOriginalsTimeline", self.template, overrides)
            response = self._call("UserOriginalsTimeline", variables)
            page_data = parse_page(response)
            for tweet in page_data.tweets:
                entry = tweet_to_entry(tweet)
                tid = entry.get("tweet_id")
                if tid and tid not in collected:
                    collected[tid] = entry
            print(f"[xauth] @{clean} page {page}: +{len(page_data.tweets)} "
                  f"(total {len(collected)})", file=sys.stderr)
            cursor = page_data.bottom_cursor
            if not cursor or not page_data.tweets:
                break
            if len(collected) < limit:
                time.sleep(0.4)

        entries = sort_entries_newest_first(list(collected.values()))
        return [Tweet.from_syndication_entry(e) for e in entries[:limit]]

    def fetch_user_info(self, username: str) -> Profile:
        clean = (username or "").strip().lstrip("@")
        variables = build_variables("UserByScreenName", self.template,
                                    {"screen_name": clean, "withGrokTranslatedBio": True})
        data = self._call("UserByScreenName", variables)
        info = parse_user_profile(data)
        if not info:
            raise NotFound(f"xauth: user @{clean} not found")
        return Profile(
            username=info.get("screen_name", clean),
            display_name=info.get("name", ""),
            bio=info.get("bio", ""),
            tweets_count=info.get("tweets_count", 0),
            followers=info.get("followers", 0),
            following=info.get("following", 0),
            joined=info.get("joined", ""),
        )
