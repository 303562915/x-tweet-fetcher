"""Can a real (headed) browser scroll a profile and collect more than the
first screen of tweets? Uses a persistent profile so a login can be kept
across runs without anyone managing cookies by hand.
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, "src")

from playwright.sync_api import sync_playwright  # noqa: E402

from xtf.parsers.xcom_ssr import extract_tweet_objects  # noqa: E402

USER = sys.argv[1] if len(sys.argv) > 1 else "aleabitoreddit"
PROFILE = r"G:\x-profile"
PROXY = "http://127.0.0.1:3067"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE,
        headless=False,
        proxy={"server": PROXY},
        user_agent=UA,
        locale="en-US",
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    print(f"goto https://x.com/{USER}")
    try:
        resp = page.goto(f"https://x.com/{USER}", wait_until="domcontentloaded", timeout=90000)
        print("status:", getattr(resp, "status", None))
    except Exception as e:  # noqa: BLE001
        print("goto FAIL:", type(e).__name__, str(e)[:100])

    page.wait_for_timeout(9000)

    def report(tag: str) -> int:
        html = page.content()
        objs = extract_tweet_objects(html)
        mine = 0
        ids = []
        for o in objs:
            core = ((o.get("core") or {}).get("user_results") or {}).get("result") or {}
            screen = (core.get("core") or {}).get("screen_name", "")
            if screen.lower() == USER.lower():
                mine += 1
                ids.append(str((o.get("edit_control") or {}).get("edit_tweet_ids", [""])[0]))
        body = page.inner_text("body")[:80].replace("\n", " | ")
        print(f"[{tag}] objects={len(objs)} mine={mine} html={len(html)} | {body!r}")
        return mine

    report("initial")
    best = 0
    for i in range(1, 7):
        page.mouse.wheel(0, 5000)
        page.wait_for_timeout(5000)
        n = report(f"scroll{i}")
        if n <= best and i >= 3:
            print("  -> no growth, stopping scroll")
            break
        best = max(best, n)

    print(f"\nmax own tweets observed: {best}")
    page.screenshot(path="snapshots/_scroll_probe.png")
    print("screenshot -> snapshots/_scroll_probe.png")
    ctx.close()
