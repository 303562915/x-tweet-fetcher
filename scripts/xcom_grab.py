"""Grab the newest N posts of a profile using the signed-in profile browser.

Reads the rendered timeline (the signed-in page scrolls and lazily loads more),
so it can return far more than the first screen the anonymous HTML embeds.
Reuses G:\\x-profile, which keeps the X session alive on its own.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

PROFILE = r"G:\x-profile"
PROXY = "http://127.0.0.1:3067"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TWITTER_TIME = "%a %b %d %H:%M:%S +0000 %Y"

USER = sys.argv[1] if len(sys.argv) > 1 else "aleabitoreddit"
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 30
OUT = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("snapshots") / f"{USER}.json"

_NUM = re.compile(r"[\d.,]+[KMB]?", re.IGNORECASE)


def _to_int(raw: str) -> int:
    """'1,234' / '12.5K' / '3M' -> int."""
    if not raw:
        return 0
    match = _NUM.search(raw)
    if not match:
        return 0
    text = match.group(0).replace(",", "").upper()
    mult = 1
    if text.endswith("K"):
        mult, text = 1_000, text[:-1]
    elif text.endswith("M"):
        mult, text = 1_000_000, text[:-1]
    elif text.endswith("B"):
        mult, text = 1_000_000_000, text[:-1]
    try:
        return int(float(text) * mult)
    except ValueError:
        return 0


def _stat(article, testid: str) -> int:
    try:
        node = article.locator(f'[data-testid="{testid}"]')
        if node.count() == 0:
            return 0
        label = node.first.get_attribute("aria-label") or ""
        return _to_int(label) or _to_int(node.first.inner_text())
    except Exception:  # noqa: BLE001
        return 0


def _iso_to_twitter_time(value: str) -> str:
    if not value:
        return ""
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return stamp.astimezone(timezone.utc).strftime(TWITTER_TIME)


def _relative(created: str) -> str:
    try:
        stamp = datetime.strptime(created, TWITTER_TIME).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return ""
    seconds = int((datetime.now(timezone.utc) - stamp).total_seconds())
    if seconds < 60:
        return f"{max(seconds, 0)}s"
    for div, unit in ((60, "m"), (3600, "h"), (86400, "d"), (604800, "w")):
        if seconds < div * (60 if unit == "m" else (24 if unit == "h" else 7)):
            return f"{seconds // div}{unit}"
    return created[:10]


def main() -> int:
    posts: dict[str, dict] = {}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE,
            headless=False,
            proxy={"server": PROXY},
            user_agent=UA,
            locale="en-US",
            viewport={"width": 1400, "height": 1000},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"https://x.com/{USER}", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(7000)

        want = f"@{USER}".lower()
        for step in range(40):
            articles = page.locator('article[data-testid="tweet"]')
            count = articles.count()
            for i in range(count):
                article = articles.nth(i)
                try:
                    href = article.locator('a[href*="/status/"]').first.get_attribute("href") or ""
                except Exception:  # noqa: BLE001
                    continue
                match = re.search(r"/status/(\d+)", href)
                if not match:
                    continue
                tid = match.group(1)
                if tid in posts:
                    continue
                try:
                    author = article.locator('[data-testid="User-Name"]').first.inner_text()
                except Exception:  # noqa: BLE001
                    author = ""
                screen = author.split("@")[-1].split("\n")[0].strip() if "@" in author else ""
                if screen and screen.lower() != want.lstrip("@"):
                    continue
                try:
                    text = article.locator('[data-testid="tweetText"]').first.inner_text()
                except Exception:  # noqa: BLE001
                    text = ""
                try:
                    iso = article.locator("time").first.get_attribute("datetime") or ""
                except Exception:  # noqa: BLE001
                    iso = ""
                created = _iso_to_twitter_time(iso)
                posts[tid] = {
                    "author": f"@{USER}",
                    "author_name": author.split("\n")[0] if author else USER,
                    "text": text,
                    "time_ago": _relative(created),
                    "likes": _stat(article, "like"),
                    "retweets": _stat(article, "retweet"),
                    "replies": _stat(article, "reply"),
                    "views": _stat(article, "views") or _to_int(
                        (article.inner_text() or "").split("Views")[-1]),
                    "tweet_id": tid,
                    "url": f"https://x.com/{USER}/status/{tid}",
                    "created_at": created,
                }
            print(f"step {step}: dom={count} collected={len(posts)}", flush=True)
            if len(posts) >= LIMIT:
                break
            page.mouse.wheel(0, 4500)
            page.wait_for_timeout(2500)

        ctx.close()

    ordered = sorted(posts.values(), key=lambda t: t.get("created_at") or "", reverse=True)[:LIMIT]
    payload = {
        "username": USER,
        "count": len(ordered),
        "backend": "xcom-browser",
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tweets": ordered,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} ({len(ordered)} tweets)")
    return 0 if ordered else 1


if __name__ == "__main__":
    raise SystemExit(main())
