"""Capture the full UserOriginalsTimeline GraphQL request (params + headers).

Writes the captured template to snapshots/_graphql_probe.json so the request
can be replayed over plain HTTP. No credential is written to that file — only
the query string and the non-secret request headers.
"""
from __future__ import annotations

import json
import os
import pathlib
import urllib.parse

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
PROXY = "http://127.0.0.1:3067"
USER = os.environ.get("X_USER", "aleabitoreddit")
HEADLESS = os.environ.get("X_HEADLESS", "1") != "0"
AUTH = os.environ.get("X_AUTH_TOKEN", "")
CT0 = os.environ.get("X_CT0", "")
if not AUTH:
    raise SystemExit("X_AUTH_TOKEN missing")

WANTED = ("UserOriginalsTimeline", "UserTweets", "UserByScreenName")
captured: dict[str, dict] = {}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=HEADLESS, proxy={"server": PROXY},
                                args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(user_agent=UA, locale="en-US",
                              viewport={"width": 1400, "height": 1000})
    ctx.add_cookies([
        {"name": "auth_token", "value": AUTH, "domain": ".x.com", "path": "/",
         "httpOnly": True, "secure": True, "sameSite": "None"},
        {"name": "ct0", "value": CT0, "domain": ".x.com", "path": "/",
         "secure": True, "sameSite": "Lax"},
    ])
    page = ctx.new_page()

    def on_request(request):
        url = request.url
        if "/graphql/" not in url:
            return
        op = urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1]
        if op in WANTED and op not in captured:
            captured[op] = {"url": url, "headers": dict(request.headers)}
            print(f"captured {op} ({len(url)} chars)")

    page.on("request", on_request)
    page.goto(f"https://x.com/{USER}", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(9000)
    page.mouse.wheel(0, 5000)
    page.wait_for_timeout(5000)
    browser.close()

out = pathlib.Path("snapshots/_graphql_probe.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nwrote {out}")

for op, payload in captured.items():
    parsed = urllib.parse.urlparse(payload["url"])
    qs = urllib.parse.parse_qs(parsed.query)
    print(f"\n=== {op} ===")
    print("  path      :", parsed.path)
    print("  variables :", (qs.get("variables") or [""])[0][:200])
    print("  features  :", len((qs.get("features") or [""])[0]), "chars")
    print("  fieldToggl:", (qs.get("fieldToggles") or [""])[0][:120])
    keep = ("authorization", "x-csrf-token", "x-twitter-auth-type",
            "x-twitter-active-user", "x-twitter-client-language",
            "x-twitter-withsuperfollows", "content-type")
    for key in keep:
        value = payload["headers"].get(key)
        if value is None:
            for hk, hv in payload["headers"].items():
                if hk.lower() == key:
                    value = hv
                    break
        if value:
            shown = value[:24] + "..." if key == "authorization" else value[:40]
            print(f"  hdr {key:<28} {shown}")
