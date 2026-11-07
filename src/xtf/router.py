"""Router — picks the right backend chain for each capability.

The whole v1 scatter of ``if use_nitter: ... else: ...`` becomes one loop:
try each candidate backend in priority order, fall through on
``NotSupported`` / ``BackendUnavailable`` / transient upstream errors,
and raise ``AllBackendsFailed`` (with per-backend causes) if none worked.

Priority per capability:
  timeline                      syndication -> nitter -> browser
  replies / search / list       nitter -> browser
  article                       browser only
  single tweet                  fxtwitter only
  user-info                     fxtwitter -> nitter -> syndication
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from . import config
from .backends.base import Backend
from .backends.browser import BrowserBackend
from .backends.fxtwitter import FxTwitterBackend
from .backends.nitter import NitterBackend
from .backends.syndication import SyndicationBackend
from .backends.xcom import XcomBackend
from .exceptions import (
    AllBackendsFailed,
    BackendUnavailable,
    NotSupported,
    RateLimited,
    UpstreamDown,
    XtfError,
)

_FALLTHROUGH = (NotSupported, BackendUnavailable, RateLimited, UpstreamDown)


class Router:
    def __init__(self, backend: str = "auto",
                 nitter_instances: list[str] | None = None,
                 browser_driver: str | None = None,
                 browser_port: int | None = None,
                 browser_nitter: str | None = None,
                 wait_rate_limit: int | None = None):
        """backend: 'auto' | 'syndication' | 'nitter' | 'browser'."""
        self.mode = backend
        self.fxtwitter = FxTwitterBackend()
        self.xcom = XcomBackend()
        self.syndication = SyndicationBackend(wait_budget=wait_rate_limit)
        self.nitter = NitterBackend(instances=nitter_instances)
        self.browser = BrowserBackend(driver=browser_driver, port=browser_port,
                                      nitter_instance=browser_nitter)

    # ── chain construction ───────────────────────────────────────────────
    def _chain(self) -> list[Backend]:
        if self.mode == "xcom":
            return [self.xcom]
        if self.mode == "nitter":
            return [self.nitter]
        if self.mode == "browser":
            return [self.browser]
        if self.mode == "syndication":
            return [self.syndication]
        # auto: xcom first (a short but *current* timeline), then syndication
        # (longer but possibly stale), then Nitter, then the browser.
        chain: list[Backend] = []
        if config.xcom_enabled():
            chain.append(self.xcom)
        if config.syndication_enabled():
            chain.append(self.syndication)
        chain += [self.nitter, self.browser]
        return chain

    def _run(self, op_name: str, call: Callable[[Backend], Any],
             chain: list[Backend] | None = None) -> Any:
        causes: dict[str, XtfError] = {}
        for backend in (chain if chain is not None else self._chain()):
            try:
                result = call(backend)
                # Record which backend served the request (for output metadata)
                self.last_backend = backend.name
                return result
            except _FALLTHROUGH as e:
                causes[backend.name] = e
                print(f"[router] {backend.name} failed for {op_name} "
                      f"({e.code}), falling through...", file=sys.stderr)
        raise AllBackendsFailed(f"all backends failed for {op_name}", causes=causes)

    # ── public API ───────────────────────────────────────────────────────
    last_backend: str = ""

    def fetch_tweet(self, username: str, tweet_id: str) -> dict[str, Any]:
        return self._run("fetch_tweet",
                         lambda b: b.fetch_tweet(username, tweet_id),
                         chain=[self.fxtwitter])

    def fetch_user_info(self, username: str) -> dict[str, Any]:
        # v1 behavior: FxTwitter first (rich fields), Nitter HTML fallback,
        # then the login-free syndication profile as the last resort.
        try:
            result = self.fxtwitter.fetch_user_info_dict(username)
            self.last_backend = "fxtwitter"
            return result
        except XtfError as e:
            print(f"[router] fxtwitter user-info failed ({e.code}), trying nitter...",
                  file=sys.stderr)
        profile = self._run("fetch_user_info",
                            lambda b: b.fetch_user_info(username),
                            chain=[self.nitter, self.syndication])
        return profile.to_dict()

    def fetch_timeline(self, username: str, limit: int = 20):
        return self._run("fetch_timeline",
                         lambda b: b.fetch_timeline(username, limit=limit))

    def fetch_replies(self, username: str, tweet_id: str):
        return self._run("fetch_replies",
                         lambda b: b.fetch_replies(username, tweet_id),
                         chain=[self.nitter, self.browser])

    def search(self, query: str, limit: int = 20):
        return self._run("search", lambda b: b.search(query, limit=limit),
                         chain=[self.nitter, self.browser])

    def fetch_list(self, list_id: str, limit: int = 20):
        return self._run("fetch_list",
                         lambda b: b.fetch_list(list_id, limit=limit),
                         chain=[self.nitter, self.browser])

    def fetch_article(self, article_id: str):
        return self._run("fetch_article",
                         lambda b: b.fetch_article(article_id),
                         chain=[self.browser])
