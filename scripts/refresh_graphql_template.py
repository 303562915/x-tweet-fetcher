"""Recapture X's web GraphQL query ids and feature flags.

X rotates these periodically; when it does, every xauth call starts failing
with an upstream error and this script rebuilds ``src/xtf/data/x_graphql.json``
from a real signed-in browser session.

Usage (needs the [playwright] extra and X cookies):

    $env:X_AUTH_TOKEN = "..."; $env:X_CT0 = "..."
    python scripts/refresh_graphql_template.py [username]
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
PROXY = os.environ.get("XTF_PROXY", "http://127.0.0.1:3067")
USER = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("X_USER", "aleabitoreddit")
DEST = pathlib.Path(__file__).resolve().parent.parent / "src" / "xtf" / "data" / "x_graphql.json"

WANTED = ("UserByScreenName", "UserOriginalsTimeline")
AUTH = os.environ.get("XTF_AUTH_TOKEN") or os.environ.get("X_AUTH_TOKEN", "")
CT0 = os.environ.get("XTF_CT0") or os.environ.get("X_CT0", "")
if not AUTH or not CT0:
    raise SystemExit("set XTF_AUTH_TOKEN and XTF_CT0 first")

captured: dict[str, dict] = {}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=os.environ.get("X_HEADLESS", "1") != "0",
                                proxy={"server": PROXY},
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
            captured[op] = {"url": url}
            print(f"captured {op}")

    page.on("request", on_request)
    page.goto(f"https://x.com/{USER}", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(9000)
    page.mouse.wheel(0, 5000)
    page.wait_for_timeout(5000)
    browser.close()

missing = [op for op in WANTED if op not in captured]
if missing:
    raise SystemExit(f"could not capture: {missing}")

out: dict = {
    "_comment": ("Captured X web GraphQL query ids/features. "
                 "Refresh with scripts/refresh_graphql_template.py when X rotates them."),
    "captured_at": datetime.date.today().isoformat(),
}
for op, payload in captured.items():
    parsed = urllib.parse.urlparse(payload["url"])
    qs = urllib.parse.parse_qs(parsed.query)
    out[op] = {
        "queryId": parsed.path.split("/graphql/")[-1].split("/")[0],
        "operationName": op,
        "variables": json.loads((qs.get("variables") or ["{}"])[0]),
        "features": (qs.get("features") or [""])[0],
        "fieldToggles": (qs.get("fieldToggles") or [""])[0],
    }

DEST.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"wrote {DEST}")
for op in WANTED:
    print(f"  {op}: queryId={out[op]['queryId']} features={len(out[op]['features'])} chars")
