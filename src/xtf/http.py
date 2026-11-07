"""HTTP layer: single choke point for all upstream calls.

Replaces the v1 ``http_get -> dict | str | None`` tri-state with:
  - success  -> body (str) or parsed JSON (via get_json)
  - failure  -> typed exception (RateLimited / NotFound / UpstreamDown)

Retries transient failures (429, 5xx, network errors) with exponential
backoff. 404 and 4xx are not retried.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .config import RETRY_ATTEMPTS, RETRY_BACKOFF_BASE, USER_AGENT
from .exceptions import NotFound, RateLimited, UpstreamDown

_MAX_BODY = 10 * 1024 * 1024


def _header_number(headers, name: str) -> float | None:
    """Read a numeric response header, tolerating absence and junk."""
    try:
        raw = headers.get(name) if headers else None
    except Exception:  # exotic header containers
        return None
    if raw in (None, ""):
        return None
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _rate_limit_error(url: str, code: int, headers) -> RateLimited:
    """Build a RateLimited carrying whatever quota hints the upstream sent.

    ``x-rate-limit-reset`` (X/Twitter, Unix timestamp) and ``Retry-After``
    (seconds) are what let a caller wait for a refill instead of giving up.
    """
    reset_at = _header_number(headers, "x-rate-limit-reset")
    retry_after = _header_number(headers, "Retry-After")
    return RateLimited(
        f"HTTP {code} — {url}",
        reset_at=reset_at,
        retry_after=int(retry_after) if retry_after is not None else None,
    )


def get_text(url: str, headers: dict[str, str] | None = None,
             timeout: int = 15, retries: int = RETRY_ATTEMPTS) -> str:
    """GET a URL, return the response body as text. Raises typed errors."""
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read(_MAX_BODY).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(f"HTTP 404 — {url}") from e
            if e.code in (403, 429):
                last_exc = _rate_limit_error(url, e.code, e.headers)
            elif e.code >= 500:
                last_exc = UpstreamDown(f"HTTP {e.code} — {url}")
            else:
                raise UpstreamDown(f"HTTP {e.code}: {e.reason} — {url}") from e
        except urllib.error.URLError as e:
            last_exc = UpstreamDown(f"network error — {url}: {e.reason}")
        except TimeoutError:
            last_exc = UpstreamDown(f"timeout — {url}")

        if attempt < retries:
            time.sleep(RETRY_BACKOFF_BASE * (2 ** attempt))

    assert last_exc is not None
    raise last_exc


def get_json(url: str, headers: dict[str, str] | None = None,
             timeout: int = 15, retries: int = RETRY_ATTEMPTS) -> Any:
    """GET a URL and parse JSON. Raises UpstreamDown on malformed JSON."""
    raw = get_text(url, headers=headers, timeout=timeout, retries=retries)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise UpstreamDown(f"invalid JSON from {url}") from e


def probe(url: str, timeout: int = 3) -> bool:
    """Lightweight reachability check. Never raises."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(1024)
        return True
    except Exception:
        return False
