"""Unified configuration. All knobs are environment variables.

  XTF_NITTER        Comma-separated Nitter base URLs, tried in order.
                    Accepts full URLs (http://127.0.0.1:8788) or bare
                    hosts (nitter.example.com -> https:// assumed).
                    Default: http://127.0.0.1:8788 (self-hosted).
                    NITTER_URL (v1 name) is honored as a fallback.
  XTF_BROWSER       Browser driver for snapshot mode: "camofox" (default)
                    or "playwright".
  XTF_BROWSER_PORT  Camofox HTTP port (default 9377).
  XTF_LANG          Default message language: zh (default) or en.
  XTF_CACHE_DIR     Mentions-monitor cache dir (default ~/.x-tweet-fetcher).
  XTF_SYNDICATION   Set to 0/false/off to drop the login-free syndication
                    backend from the auto chain (default: on).
  XTF_SYNDICATION_URL
                    Comma-separated syndication base URLs, tried in order.
                    Default: https://syndication.twitter.com (X-owned).
  XTF_WAIT_RATE_LIMIT
                    Seconds a backend may sleep waiting for an upstream quota
                    to refill when the response carries a reset hint
                    (default 0 = fail fast; CI sets several hundred).
  XTF_XCOM          Set to 0/false/off to drop the logged-out x.com profile
                    backend from the auto chain (default: on).
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_NITTER = "http://127.0.0.1:8788"
DEFAULT_BROWSER_PORT = 9377
DEFAULT_SYNDICATION = "https://syndication.twitter.com"
DEFAULT_SYNDICATION_TTL = 90  # seconds a fetched timeline is reused in-process
DEFAULT_RATE_LIMIT_WAIT = 0   # seconds allowed to wait out a quota reset
USER_AGENT = "x-tweet-fetcher/3.0"

# Retry / backoff for upstream HTTP calls
RETRY_ATTEMPTS = 2          # retries after the first attempt
RETRY_BACKOFF_BASE = 1.0    # seconds; doubles each retry


def _normalize_instance(raw: str) -> str:
    raw = raw.strip().rstrip("/")
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    return raw


def nitter_instances() -> list[str]:
    """Ordered list of Nitter base URLs to try. Never empty."""
    raw = os.environ.get("XTF_NITTER") or os.environ.get("NITTER_URL") or DEFAULT_NITTER
    out = []
    for part in raw.split(","):
        norm = _normalize_instance(part)
        if norm and norm not in out:
            out.append(norm)
    return out or [DEFAULT_NITTER]


def browser_driver() -> str:
    d = os.environ.get("XTF_BROWSER", "camofox").strip().lower()
    return d if d in ("camofox", "playwright") else "camofox"


def browser_port() -> int:
    try:
        return int(os.environ.get("XTF_BROWSER_PORT", DEFAULT_BROWSER_PORT))
    except ValueError:
        return DEFAULT_BROWSER_PORT


def default_lang() -> str:
    lang = os.environ.get("XTF_LANG", "zh").strip().lower()
    return lang if lang in ("zh", "en") else "zh"


def cache_dir() -> Path:
    return Path(os.environ.get("XTF_CACHE_DIR", "")) if os.environ.get("XTF_CACHE_DIR") else Path.home() / ".x-tweet-fetcher"


def syndication_enabled() -> bool:
    """Whether the login-free syndication backend joins the auto chain."""
    raw = os.environ.get("XTF_SYNDICATION", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def syndication_hosts() -> list[str]:
    """Ordered syndication base URLs. Never empty."""
    raw = os.environ.get("XTF_SYNDICATION_URL") or DEFAULT_SYNDICATION
    out: list[str] = []
    for part in raw.split(","):
        norm = _normalize_instance(part)
        if norm and norm not in out:
            out.append(norm)
    return out or [DEFAULT_SYNDICATION]


def syndication_ttl() -> int:
    """In-process timeline cache TTL in seconds (0 disables caching)."""
    try:
        return max(0, int(os.environ.get("XTF_SYNDICATION_TTL", DEFAULT_SYNDICATION_TTL)))
    except ValueError:
        return DEFAULT_SYNDICATION_TTL


def rate_limit_wait() -> int:
    """Seconds a backend may sleep for a quota refill (0 = fail fast)."""
    try:
        return max(0, int(os.environ.get("XTF_WAIT_RATE_LIMIT", DEFAULT_RATE_LIMIT_WAIT)))
    except ValueError:
        return DEFAULT_RATE_LIMIT_WAIT


def xcom_enabled() -> bool:
    """Whether the logged-out x.com profile backend joins the auto chain."""
    raw = os.environ.get("XTF_XCOM", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def auth_token() -> str:
    """The X ``auth_token`` cookie, if configured."""
    return (os.environ.get("XTF_AUTH_TOKEN") or os.environ.get("X_AUTH_TOKEN") or "").strip()


def ct0() -> str:
    """The X ``ct0`` cookie (sent as x-csrf-token), if configured."""
    return (os.environ.get("XTF_CT0") or os.environ.get("X_CT0") or "").strip()


def xauth_enabled() -> bool:
    """Whether the authenticated GraphQL backend may join the auto chain."""
    raw = os.environ.get("XTF_XAUTH", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")
