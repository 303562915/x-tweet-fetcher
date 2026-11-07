"""Replay the captured UserOriginalsTimeline request over plain HTTP.

Reads credentials from $X_AUTH_TOKEN / $X_CT0 and the request template from
snapshots/_graphql_probe.json. Prints the response shape and follows the
cursor once, to prove pagination works without a browser.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
AUTH = os.environ.get("X_AUTH_TOKEN", "")
CT0 = os.environ.get("X_CT0", "")
if not AUTH or not CT0:
    raise SystemExit("X_AUTH_TOKEN / X_CT0 missing")

template = json.loads(pathlib.Path("snapshots/_graphql_probe.json").read_text(encoding="utf-8"))
entry = template["UserOriginalsTimeline"]
parsed = urllib.parse.urlparse(entry["url"])
base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
query = urllib.parse.parse_qs(parsed.query)

HEADERS = {
    "User-Agent": UA,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Authorization": entry["headers"].get("authorization", ""),
    "x-csrf-token": CT0,
    "x-twitter-auth-type": "OAuth2Session",
    "x-twitter-active-user": "yes",
    "x-twitter-client-language": "en",
    "Content-Type": "application/json",
    "Cookie": f"auth_token={AUTH}; ct0={CT0}",
}


def call(variables: dict, features: str, field_toggles: str) -> dict:
    params = {
        "variables": json.dumps(variables, separators=(",", ":")),
        "features": features,
        "fieldToggles": field_toggles,
    }
    url = base + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def walk_tweets(node, out, depth=0):
    """Collect tweet results and cursors from the timeline instructions."""
    if depth > 12 or not isinstance(node, (dict, list)):
        return
    if isinstance(node, list):
        for item in node:
            walk_tweets(item, out, depth + 1)
        return
    if "cursorType" in node and "value" in node:
        out["cursors"].append((node.get("cursorType"), node.get("value")))
    if node.get("__typename") == "Tweet" or "legacy" in node and "full_text" in (node.get("legacy") or {}):
        out["tweets"].append(node)
    for value in node.values():
        walk_tweets(value, out, depth + 1)


variables = json.loads(query["variables"][0])
features = query["features"][0]
field_toggles = (query.get("fieldToggles") or [""])[0]
print("userId:", variables.get("userId"), "count:", variables.get("count"))

data = call(variables, features, field_toggles)
print("top keys:", list(data.keys()))
if "errors" in data:
    print("ERRORS:", json.dumps(data["errors"], ensure_ascii=False)[:400])

out: dict = {"tweets": [], "cursors": []}
walk_tweets(data.get("data", {}), out)
print(f"\npage 1: tweets={len(out['tweets'])} cursors={[c[0] for c in out['cursors']]}")

for tw in out["tweets"][:5]:
    legacy = tw.get("legacy") or {}
    core = ((tw.get("core") or {}).get("user_results") or {}).get("result") or {}
    screen = (core.get("core") or {}).get("screen_name")
    views = tw.get("views") or {}
    print(f"  {tw.get('rest_id')} @{screen} fav={legacy.get('favorite_count')} "
          f"views={views.get('count')} {str(legacy.get('full_text'))[:50]!r}")

bottom = next((v for t, v in out["cursors"] if t == "Bottom"), None)
if bottom:
    variables2 = dict(variables, cursor=bottom)
    data2 = call(variables2, features, field_toggles)
    out2: dict = {"tweets": [], "cursors": []}
    walk_tweets(data2.get("data", {}), out2)
    print(f"\npage 2: tweets={len(out2['tweets'])}")
    for tw in out2["tweets"][:3]:
        legacy = tw.get("legacy") or {}
        print(f"  {tw.get('rest_id')} {str(legacy.get('full_text'))[:50]!r}")
    print(f"\nTOTAL across two pages: {len(out['tweets']) + len(out2['tweets'])}")
else:
    print("\nno Bottom cursor found — pagination unavailable")
