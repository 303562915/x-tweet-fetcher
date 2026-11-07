"""Does an authenticated request return a fuller timeline than the anonymous one?

Reads the cookie header from $X_COOKIE so no credential ever lands in a file.
"""
from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, "src")

from xtf.parsers.xcom_ssr import extract_tweet_objects, parse_profile_timeline  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
USER = sys.argv[1] if len(sys.argv) > 1 else "aleabitoreddit"
COOKIE = os.environ.get("X_COOKIE", "")

headers = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}
if COOKIE:
    headers["Cookie"] = COOKIE

req = urllib.request.Request(f"https://x.com/{USER}", headers=headers)
print(f"authenticated: {bool(COOKIE)}")
try:
    with urllib.request.urlopen(req, timeout=40) as r:
        html = r.read().decode("utf-8", "replace")
        print(f"HTTP {r.status}  html={len(html)}")
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code} {e.reason}")
    raise SystemExit(1)

objs = extract_tweet_objects(html)
mine = parse_profile_timeline(html, author=USER)
print(f"tweet objects in payload: {len(objs)}")
print(f"own tweets (author={USER}): {len(mine)}")
for entry in mine[:12]:
    print(f"  {entry['created_at']:<32} {entry['tweet_id']:<20} {entry['text'][:52]!r}")

# Does the signed-in page embed a cursor / next-page token?
flat = html.replace('\\"', '"')
for token in ('"cursorType"', '"cursor"', '"has_next_page"', '"entryType"',
              '"timeline"', 'SideNav', '"TweetDetail"'):
    print(f"  {token:<20} {flat.count(token)}")
