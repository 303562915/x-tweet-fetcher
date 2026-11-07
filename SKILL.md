---
name: x-tweet-fetcher
description: >
  Fetch tweets, replies, timelines, search results, X Lists, and X Articles
  from X/Twitter. Single tweets: zero dependencies (FxTwitter). Full paginated
  timelines: X cookies (auth_token/ct0) via the signed-in GraphQL API.
  Login-free timelines: the logged-out profile page (short but current) or the
  syndication widget (longer but may be stale). Search/replies/mentions: a
  Nitter instance (XTF_NITTER). Lists/Articles: a browser driver (Camofox or
  Playwright). Unified JSON schema across all backends; machine-readable
  error_code for agent branching.
  Field reports and agent-use questions: Agent Waystation #22 Teahouse:
  https://github.com/ythx-101/openclaw-qa/discussions/22
---

# X Tweet Fetcher

Fetch tweets from X/Twitter without authentication. For agent-use stories, failures, and field reports, start from **[#22 Teahouse / 茶座](https://github.com/ythx-101/openclaw-qa/discussions/22)**.

## Feature Overview

| Feature | Command | Dependencies |
|---------|---------|-------------|
| Single tweet | `xtf --url <tweet_url>` | None (zero deps) |
| Reply threads | `xtf --url <tweet_url> --replies` | Nitter or browser |
| User timeline (complete) | `xtf --user <name> --limit 30` | X cookies (xauth) |
| User timeline (login-free) | `xtf --user <name> --limit 30` | None (xcom / syndication) |
| Search | `xtf --search "<query>"` | Nitter |
| User profile | `xtf --user-info <username>` | None (zero deps) |
| X List | `xtf --list <list_url_or_id>` | Browser (Camofox/Playwright) |
| X Article | `xtf --article <url_or_id>` | Browser (Camofox/Playwright) |
| Mentions monitor | `xtf --monitor @<username>` | Nitter or browser |

`python3 scripts/fetch_tweet.py` accepts the same flags (v1-compatible entry point).

## Basic Usage (Zero Dependencies)

```bash
# JSON output (default)
xtf --url https://x.com/user/status/1234567890

# Human-readable
xtf --url https://x.com/user/status/1234567890 --text-only

# Output covers: text, author, stats (likes/retweets/views), media URLs,
# quoted tweets, and full article text for tweet-embedded articles.
```

## Timeline (zero dependencies)

```bash
xtf --user aleabitoreddit --limit 30    # auto: picks the best available backend
```

Two login-free sources, measured minutes apart on the same account:

- **xcom** — the logged-out `x.com/<user>` page embeds the first screen (~3-5 tweets), but
  they are genuinely current (`aleabitoreddit`: newest was minutes old).
- **syndication** — `syndication.twitter.com/srv/timeline-profile` can return far more
  (`nasa`: 19 strictly newest-first, `elonmusk`: 100) but for some accounts it serves a
  stale "featured" set (`aleabitoreddit`: **2 tweets from 2025**). `auto` tries `xcom`
  first and does not fall through after a success, so stale data never replaces current.

Both share a ~30 requests/window per-IP cap on syndication (wait it out with
`--wait-rate-limit`), and neither works from datacenter IPs (GitHub runners get 429).

## Full timeline (X cookies)

```bash
export XTF_AUTH_TOKEN=<auth_token cookie>
export XTF_CT0=<ct0 cookie>
xtf --user aleabitoreddit --limit 30 --backend xauth
```

Reproduces the signed-in web app's GraphQL calls (`UserByScreenName` →
`UserOriginalsTimeline`) over plain HTTP — no browser, no Playwright. One page is 20 tweets
and the cursor paginates as deep as you like; measured `--limit 30` → 30 tweets, strictly
newest-first, in seconds. Being a *signed-in* call, it works from GitHub runners where the
anonymous endpoints are blocked.

Query ids/feature flags rotate; refresh with `python scripts/refresh_graphql_template.py`
(needs the `[playwright]` extra) when calls start failing.

## Search / Replies / Mentions (Nitter)

```bash
export XTF_NITTER=http://127.0.0.1:8788   # your instance; comma-separate for failover
xtf --search "openclaw" --limit 10
xtf --url https://x.com/user/status/123 --replies
```

Backend selection: `--backend auto` (default, xauth → xcom → syndication → nitter →
browser), or pin one with `--backend xauth|xcom|syndication|nitter|browser`.

## Lists & Articles (Browser)

```bash
# Camofox running on localhost:9377 (default), or:
export XTF_BROWSER=playwright

xtf --list https://x.com/i/lists/1455045069516357634 --limit 30
xtf --article https://x.com/i/article/2011779830157557760
```

Note: full X Article text requires X login; without it you get title + public preview (`is_partial: true`).

## Mentions Monitor (cron-friendly)

```bash
xtf --monitor @yourhandle
# exit 0 = no new mentions, 1 = new mentions found, 2 = setup error
# First run builds a baseline silently; later runs report only new URLs.
```

## Error Handling for Agents

Every error result includes `error` (message) and `error_code`:
`invalid_input` · `not_found` · `rate_limited` · `upstream_down` · `backend_unavailable` · `all_backends_failed` (with per-backend `error_causes`).

## Python API

```python
from xtf import Router
router = Router()
tweets = router.fetch_timeline("elonmusk", limit=20)  # list[Tweet]
print(tweets[0].to_dict())
```

## Directory Structure

```
src/xtf/
├── backends/   # fxtwitter.py, xauth.py, xcom.py, syndication.py, nitter.py, browser.py
├── parsers/    # pure parsing functions, fixture-tested
├── data/       # captured GraphQL query ids + feature flags
├── router.py   # auto-fallback
├── monitor.py  # mentions monitor
└── cli.py      # the `xtf` command
scripts/fetch_tweet.py               # v1-compatible shim
scripts/refresh_graphql_template.py  # recapture X's query ids
.github/workflows/fetch-tweets.yml   # scheduled fetch -> committed snapshots
```

Looking for Chinese-platform fetching (Weibo/Bilibili/WeChat) or tweet growth tracking? Those moved out of this repo in v2 — see MIGRATION.md.
