"""Open the dedicated browser profile so you can sign in to X once.

Everything later (grab, schedule) reuses this same profile directory, so the
browser keeps — and silently renews — the session itself. Nobody has to copy,
store or periodically refresh an auth_token by hand.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

PROFILE = r"G:\x-profile"
PROXY = "http://127.0.0.1:3067"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

#: Elements that only exist once X considers the visitor signed in.
SIGNED_IN_MARKERS = (
    '[data-testid="SideNav_AccountSwitcher_Button"]',
    '[data-testid="SideNav_NewTweet_Button"]',
    '[data-testid="AppTabBar_Profile_Link"]',
    '[data-testid="AppTabBar_Home_Link"]',
)
COOKIE_DB = Path(PROFILE) / "Default" / "Network" / "Cookies"


def _auth_token_present() -> bool:
    """True once the profile actually holds X's auth_token cookie."""
    if not COOKIE_DB.exists():
        return False
    tmp = Path(tempfile.gettempdir()) / "xtf_login_probe.db"
    try:
        shutil.copy2(COOKIE_DB, tmp)
    except OSError:
        return False  # browser holds it; the DOM markers will catch the login
    try:
        conn = sqlite3.connect(tmp)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%x.com%'"
                " AND name='auth_token'"
            ).fetchone()
        finally:
            conn.close()
        return bool(row and row[0])
    except sqlite3.Error:
        return False

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
    page.goto("https://x.com/login", wait_until="domcontentloaded", timeout=90000)
    print("browser open — sign in to X; this window will close itself once done",
          file=sys.stderr)
    print("WAITING_FOR_LOGIN", flush=True)

    signed_in = False
    for _ in range(150):  # ~12.5 minutes
        try:
            page.wait_for_timeout(5000)
        except Exception:  # noqa: BLE001 — browser closed by the user
            break
        if not ctx.pages:
            break
        for selector in SIGNED_IN_MARKERS:
            try:
                if page.locator(selector).count() > 0:
                    signed_in = True
                    break
            except Exception:  # noqa: BLE001
                pass
        if signed_in:
            break

    if signed_in:
        page.wait_for_timeout(3000)
        print("LOGIN_OK", flush=True)
        print(f"profile saved at {PROFILE}", file=sys.stderr)
    else:
        print("LOGIN_NOT_DETECTED", flush=True)
    ctx.close()
