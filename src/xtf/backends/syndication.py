"""Syndication backend — login-free timelines via X's public widget endpoint.

``syndication.twitter.com/srv/timeline-profile/screen-name/<user>`` is the
X-owned endpoint that powers embedded profile timelines. It needs no login,
no API key, and no Nitter instance, and one request returns up to ~100 recent
tweets including absolute timestamps, media, quotes, and retweets.

Reachability notes:
  * The endpoint is rate limited per IP. Every call therefore goes through a
    short in-process TTL cache, and a 429 degrades to ``RateLimited`` so the
    router can fall through to Nitter / browser instead of failing the fetch.
  * Only the most recent page is served; ``limit`` above the page size simply
    returns everything the page carried.
"""
from __future__ import annotations

import sys
import time

from .. import config, http
from ..exceptions import InvalidInput, RateLimited, UpstreamDown, XtfError
from ..models import Profile, Tweet
from ..parsers.syndication_json import (
    extract_entries,
    extract_timeline_user,
    parse_entries,
    parse_next_data,
)
from .base import Backend

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

#: The widget page ships ~100 entries; it is the only page we can request.
MAX_PAGE = 100


class SyndicationBackend(Backend):
    name = "syndication"

    def __init__(self, hosts: list[str] | None = None, timeout: int = 30,
                 cache_ttl: int | None = None, wait_budget: int | None = None):
        self.hosts = hosts or config.syndication_hosts()
        self.timeout = timeout
        self.cache_ttl = config.syndication_ttl() if cache_ttl is None else cache_ttl
        #: Seconds this backend may sleep waiting for the shared quota to
        #: refill. X reports the reset time in ``x-rate-limit-reset``; a CI job
        #: can afford to wait, an interactive call usually cannot.
        self.wait_budget = config.rate_limit_wait() if wait_budget is None else wait_budget
        self._cache: dict[str, tuple[float, dict]] = {}

    # ── page fetching ────────────────────────────────────────────────────
    @staticmethod
    def _clean(username: str) -> str:
        clean = (username or "").strip().lstrip("@")
        if not clean:
            raise InvalidInput("syndication: empty username")
        return clean

    def _get_html(self, url: str) -> str:
        """GET a page, sleeping out a quota reset when we are allowed to.

        The syndication endpoint answers ``429`` with ``x-rate-limit-reset``
        once its shared per-IP quota (30/window) is gone. Waiting that long is
        pointless interactively, but exactly right in CI — so the wait is
        opt-in through ``XTF_WAIT_RATE_LIMIT`` / ``--wait-rate-limit``.
        """
        budget = self.wait_budget
        while True:
            try:
                return http.get_text(url, headers=_HEADERS, timeout=self.timeout, retries=1)
            except RateLimited as e:
                wait = e.wait_seconds()
                if not budget or wait is None or wait > budget:
                    raise
                print(f"[syndication] quota exhausted, {wait}s until reset — waiting",
                      file=sys.stderr)
                time.sleep(wait)
                budget -= wait

    def _fetch_page(self, username: str) -> dict:
        """GET and parse the timeline page, with failover across hosts."""
        cached = self._cache.get(username)
        if cached and self.cache_ttl and (time.time() - cached[0]) < self.cache_ttl:
            print(f"[syndication] cache hit for @{username}", file=sys.stderr)
            return cached[1]

        errors: dict[str, str] = {}
        for host in self.hosts:
            url = f"{host}/srv/timeline-profile/screen-name/{username}"
            try:
                html = self._get_html(url)
            except XtfError as e:
                if isinstance(e, RateLimited):
                    # Reported as "30 requests per window" by X; the quota is
                    # keyed per IP, so a busy shared egress (CI runners, cloud
                    # proxies) can exhaust it while other clients keep working.
                    hint = e.wait_seconds()
                    when = f" (refills in ~{hint}s)" if hint else ""
                    errors[host] = (
                        f"rate_limited: syndication quota exhausted{when} — retry later, "
                        "raise --wait-rate-limit to sleep it out, or set "
                        "XTF_SYNDICATION=0 to use Nitter instead"
                    )
                else:
                    errors[host] = f"{e.code}: {e}"
                print(f"[syndication] {host} failed ({e.code}), trying next...", file=sys.stderr)
                continue
            data = parse_next_data(html)
            if not data.get("props", {}).get("pageProps", {}).get("timeline"):
                errors[host] = "upstream_down: page carries no timeline"
                print(f"[syndication] {host} returned no timeline, trying next...", file=sys.stderr)
                continue
            if self.cache_ttl:
                self._cache[username] = (time.time(), data)
            return data

        raise UpstreamDown(
            f"syndication: no host usable for @{username}. Tried: {errors}"
        )

    # ── capabilities ─────────────────────────────────────────────────────
    def available(self) -> bool:
        # Public endpoint with per-IP rate limits: a probe would burn quota, so
        # reachability is decided per call and surfaced through the router.
        return True

    def fetch_timeline(self, username: str, limit: int = 20) -> list[Tweet]:
        clean = self._clean(username)
        data = self._fetch_page(clean)
        entries = parse_entries(data, limit=limit or None)
        tweets = [Tweet.from_syndication_entry(entry) for entry in entries]
        page_count = len(extract_entries(data))
        print(f"[syndication] @{clean}: {len(tweets)} tweets (page had "
              f"{page_count} entries, newest first)", file=sys.stderr)
        return tweets

    def fetch_user_info(self, username: str) -> Profile:
        clean = self._clean(username)
        data = self._fetch_page(clean)
        user = extract_timeline_user(data)
        if not user:
            raise UpstreamDown(f"syndication: no profile payload for @{clean}")
        return Profile(
            username=user.get("screen_name", clean),
            display_name=user.get("name", ""),
            bio=user.get("description", ""),
            tweets_count=user.get("statuses_count", 0) or 0,
            following=user.get("friends_count", 0) or 0,
            followers=user.get("followers_count", 0) or 0,
            joined=user.get("created_at", ""),
        )
