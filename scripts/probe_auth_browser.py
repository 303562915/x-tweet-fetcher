"""Load a profile in a browser with the X session injected.

Reads credentials from $X_AUTH_TOKEN / $X_CT0. Captures any GraphQL calls the
page makes — if a paginated UserTweets request shows up, the timeline can be
reproduced over plain HTTP afterwards.
"""
from __future__ import annotations

import os
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
PROXY = "http://127.0.0.1:3067"
USER = sys.argv[1] if len(sys.argv) > 1 else "aleabitoreddit"
HEADLESS = os.environ.get("X_HEADLESS", "1") != "0"

AUTH = os.environ.get("X_AUTH_TOKEN", "")
CT0 = os.environ.get("X_CT0", "")
if not AUTH:
    raise SystemExit("X_AUTH_TOKEN missing")

graphql: list[str] = []
other_api: list[str] = []

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=HEADLESS,
        proxy={"server": PROXY},
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx = browser.new_context(user_agent=UA, locale="en-US",
                              viewport={"width": 1400, "height": 1000})
    ctx.add_cookies([
        {"name": "auth_token", "value": AUTH, "domain": ".x.com", "path": "/",
         "httpOnly": True, "secure": True, "sameSite": "None"},
        {"name": "ct0", "value": CT0, "domain": ".x.com", "path": "/",
         "secure": True, "sameSite": "Lax"},
    ])
    page = ctx.new_page()

    def on_request(r):
        url = r.url
        if "/graphql/" in url:
            graphql.append(url)
        elif "/i/api/" in url or "api.x.com" in url:
            other_api.append(url)

    page.on("request", on_request)

    print(f"headless={HEADLESS} goto https://x.com/{USER}")
    try:
        resp = page.goto(f"https://x.com/{USER}", wait_until="domcontentloaded", timeout=90000)
        print("status:", getattr(resp, "status", None))
    except Exception as e:  # noqa: BLE001
        print("goto FAIL:", type(e).__name__, str(e)[:120])

    page.wait_for_timeout(9000)

    def count() -> int:
        try:
            return page.locator('article[data-testid="tweet"]').count()
        except Exception:  # noqa: BLE001
            return -1

    print(f"tweets in DOM: {count()}")
    body = page.inner_text("body")[:120].replace("\n", " | ")
    print("body head:", repr(body))

    for i in range(1, 5):
        page.mouse.wheel(0, 5000)
        page.wait_for_timeout(4000)
        print(f"after scroll {i}: {count()}")

    print(f"\ngraphql calls: {len(graphql)}")
    seen = set()
    for url in graphql:
        parsed = urllib.parse.urlparse(url)
        op = parsed.path.rstrip("/").split("/")[-1]
        qid = parsed.path.split("/graphql/")[-1].split("/")[0]
        if (op, qid) in seen:
            continue
        seen.add((op, qid))
        qs = urllib.parse.parse_qs(parsed.query)
        print(f"  op={op} queryId={qid} vars={(qs.get('variables') or [''])[0][:150]}")

    print(f"other api calls: {len(other_api)}")
    for url in list(dict.fromkeys(other_api))[:6]:
        print("  ", url[:170])

    browser.close()
