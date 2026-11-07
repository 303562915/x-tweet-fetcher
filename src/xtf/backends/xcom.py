"""x.com backend — recent tweets from the logged-out profile page.

``https://x.com/<user>`` server-renders the top of a profile even for an
anonymous visitor, and that payload is a *current* read of the timeline
(measured: the account's newest post, minutes old). It is the only login-free
source found that stays accurate for accounts the syndication widget mishandles
— ``aleabitoreddit`` returned two stale 2025 tweets through syndication while
the profile page carried seven tweets from the last day.

Limits (measured, not assumed):
  * One screen only — about 5-7 tweets — and no pagination cursor in the
    payload, so a single fetch cannot reach 30/50.
  * Use ``xtf --ledger`` to accumulate across runs; polling a few times a day
    covers accounts posting a handful of times daily.

Because the count is small, the router keeps this backend ahead of
``syndication`` (accurate but short beats long but stale) and the auto chain
does not fall through to syndication when this one succeeds — mixing them would
silently append old viral tweets to the result.
"""
from __future__ import annotations

import sys
import time

from .. import config, http
from ..exceptions import InvalidInput, RateLimited, UpstreamDown, XtfError
from ..models import Profile, Tweet
from ..parsers.xcom_ssr import extract_profile_user, parse_profile_timeline
from .base import Backend

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}


class XcomBackend(Backend):
    name = "xcom"

    def __init__(self, timeout: int = 30, wait_budget: int | None = None):
        self.timeout = timeout
        #: x.com rate limits anonymous profile views too, and datacenter IPs
        #: (CI runners) hit that ceiling quickly — so the same opt-in wait as
        #: the syndication backend applies here.
        self.wait_budget = config.rate_limit_wait() if wait_budget is None else wait_budget

    @staticmethod
    def _clean(username: str) -> str:
        clean = (username or "").strip().lstrip("@")
        # Path segments only — a username never carries a slash.
        if not clean or "/" in clean:
            raise InvalidInput(f"xcom: invalid username {username!r}")
        return clean

    def _get_profile_html(self, username: str) -> str:
        url = f"https://x.com/{username}"
        budget = self.wait_budget
        while True:
            try:
                return http.get_text(url, headers=_HEADERS, timeout=self.timeout, retries=1)
            except RateLimited as e:
                wait = e.wait_seconds()
                if budget and wait is not None and wait <= budget:
                    print(f"[xcom] rate limited, {wait}s until reset — waiting",
                          file=sys.stderr)
                    time.sleep(wait)
                    budget -= wait
                    continue
                hint = f" (refills in ~{wait}s)" if wait else ""
                raise RateLimited(
                    f"xcom: x.com rate limited the profile page{hint} — "
                    "retry later or raise --wait-rate-limit",
                    reset_at=e.reset_at, retry_after=e.retry_after,
                ) from e
            except XtfError as e:
                raise UpstreamDown(f"xcom: cannot load {url}: {e}") from e

    def available(self) -> bool:
        # Reachability is decided per call so a probe never costs a request.
        return True

    def fetch_timeline(self, username: str, limit: int = 20) -> list[Tweet]:
        clean = self._clean(username)
        html = self._get_profile_html(clean)
        entries = parse_profile_timeline(html, limit=limit or None, author=clean)
        if not entries:
            raise UpstreamDown(
                f"xcom: no embedded tweets for @{clean} "
                "(account protected, renamed, or page markup changed)"
            )
        tweets = [Tweet.from_syndication_entry(entry) for entry in entries]
        print(f"[xcom] @{clean}: {len(tweets)} tweets embedded in the profile page "
              "(first screen only — use --ledger to accumulate more)",
              file=sys.stderr)
        return tweets

    def fetch_user_info(self, username: str) -> Profile:
        clean = self._clean(username)
        html = self._get_profile_html(clean)
        user = extract_profile_user(html)
        if not user:
            raise UpstreamDown(f"xcom: no profile payload for @{clean}")
        return Profile(
            username=user.get("screen_name", clean),
            display_name="",
            bio="",
            tweets_count=user.get("tweets", 0),
            followers=user.get("followers", 0),
        )


def enabled() -> bool:
    """Whether the x.com backend joins the auto chain."""
    return config.xcom_enabled()
