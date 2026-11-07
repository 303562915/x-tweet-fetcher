<div align="center">

# x-tweet-fetcher

**Fetch X/Twitter tweets, replies, timelines, lists, and articles — no login, no API keys.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-green.svg)](https://www.python.org)
[![GitHub stars](https://img.shields.io/github/stars/ythx-101/x-tweet-fetcher?style=social)](https://github.com/ythx-101/x-tweet-fetcher)

*Five backends · Auto fallback · Unified JSON schema · Built for AI agents*

[Quick Start](#-quick-start) · [Backends](#-five-backends) · [Capabilities](#-capabilities) · [Python API](#-python-api) · [Self-hosted Nitter](#-self-hosted-nitter) · [GitHub Actions](#-github-actions) · [Migrating from v1](#-migrating-from-v1)

</div>

---

## 😤 Problem

```
You: fetch that tweet / list / article for me
AI:  I can't access X/Twitter. Please copy-paste the content manually.

You: ...seriously?
```

X has no free API. Scraping gets you blocked. Browser automation is fragile in headless environments.

**x-tweet-fetcher** solves this with **smart backend routing**: FxTwitter for single tweets (zero deps), an authenticated GraphQL backend for complete timelines, X's public syndication endpoint for login-free timelines, Nitter for search and replies (direct HTTP), a browser driver for everything else — with automatic fallback between them.

## 🚀 Quick Start

```bash
git clone https://github.com/ythx-101/x-tweet-fetcher
cd x-tweet-fetcher && pip install .

# Single tweet — works instantly, zero configuration
xtf --url https://x.com/user/status/1234567890

# User timeline — works instantly too (login-free syndication endpoint)
xtf --user elonmusk --limit 50

# Full timeline (30+ posts, paginated) — sign in once
export XTF_AUTH_TOKEN=... XTF_CT0=...   # your X cookies
xtf --user elonmusk --limit 30 --backend xauth

# Search (needs a Nitter instance, see below)
export XTF_NITTER=http://127.0.0.1:8788
xtf --search "openclaw" --limit 10

# Human-readable output instead of JSON
xtf --user elonmusk --text-only
```

Prefer not to install? `python3 scripts/fetch_tweet.py --url ...` works straight from the clone (same flags).

## 🔀 Five Backends

| Backend | Deps | Speed | Covers |
|---------|------|-------|--------|
| **fxtwitter** | None (stdlib) | ⚡⚡ | Single tweets, user profiles |
| **xauth** | X cookies | ⚡⚡ | **Full timeline, paginated (30-100+)** |
| **xcom** | None (stdlib) | ⚡ | Timeline first screen (~3-5, but current) |
| **syndication** | None (stdlib) | ⚡⚡ | Widget timeline (varies per account) |
| **nitter** | A Nitter instance | ⚡ | Timeline, search, replies, mentions |
| **browser** | Camofox *or* Playwright | 🐢 | Everything above + **Lists** + **X Articles** |
| **auto** (default) | Best available | ⚡→🐢 | xauth → xcom → syndication → nitter → browser |

```bash
xtf --user elonmusk                      # auto (default)
xtf --user elonmusk --limit 30 --backend xauth   # full timeline (needs cookies)
xtf --user elonmusk --backend xcom       # login-free, current but short
xtf --user elonmusk --backend syndication # login-free, longer but may be stale
xtf --list 1455045069516357634           # lists always use the browser
```

**Picking a login-free backend.** Measured on the same account, minutes apart:

| Account | `xcom` (logged-out page) | `syndication` (widget) |
|---------|--------------------------|------------------------|
| `aleabitoreddit` | 3 tweets, newest was minutes old | **2 tweets from 2025** — stale |
| `nasa` | — | 19 tweets, strictly newest-first |
| `elonmusk` | — | 100 tweets, but a "top tweets" mix from 2022-2025 |

So: `xcom` is short but trustworthy; `syndication` can be long but hands some accounts
year-old "featured" tweets. `auto` tries `xcom` first and does not fall through to
`syndication` on success, so a current timeline is never silently replaced by stale posts.
For a complete timeline, use `xauth`.

**The `xauth` backend.** It reproduces the signed-in web app's GraphQL calls
(`UserByScreenName` → `UserOriginalsTimeline`) over plain HTTP: no browser, no Playwright.
One page carries 20 tweets and the cursor paginates as far back as you ask — measured
`--limit 30` → 30 tweets across 2 pages, strictly newest-first, in a couple of seconds.

```bash
export XTF_AUTH_TOKEN=<auth_token cookie>
export XTF_CT0=<ct0 cookie>            # sent as x-csrf-token
xtf --user someone --limit 50 --backend xauth
```

- Cookies come from a signed-in browser (DevTools → Application → Cookies → `x.com`).
  `auth_token` typically stays valid for **months**, not hours.
- Query ids and feature flags rotate on X's schedule and live in
  `src/xtf/data/x_graphql.json`; refresh them with
  `python scripts/refresh_graphql_template.py` (needs the `[playwright]` extra) when calls start failing.
- Because these are *signed-in* calls, they are not subject to the 429s that the
  anonymous endpoints return for datacenter IPs — which is what makes the
  [GitHub Actions](#-github-actions) workflow viable.

**A note on the syndication backend.** It reads `syndication.twitter.com/srv/timeline-profile`,
the X-owned endpoint that renders embedded profile timelines. It needs no login and no
Nitter instance, and one request returns up to ~100 tweets with absolute timestamps
(`created_at`), language, media, quotes, and retweets. Entries are always emitted
newest-first.

Two things are worth knowing before you rely on it:

- **Page size and ordering vary by account.** Most profiles return a reverse-chronological
  page (measured: `nasa` → 19 tweets, strictly newest-first). A handful of
  very-high-engagement accounts (`elonmusk`) are instead served a **"top tweets" mix** of
  old viral posts, so `--limit 50` can reach back years. The backend sorts by `created_at`
  so the output is time-ordered either way, but for a complete timeline of such an account
  you need `xauth` or a self-hosted Nitter instance.
- **Rate limit.** X allows roughly **30 requests per window per IP** (the endpoint
  answers `429` with `x-rate-limit-limit: 30`). The backend therefore caches each page
  in-process for 90 seconds (`XTF_SYNDICATION_TTL`), and a `429` falls through to
  Nitter / the browser instead of failing the fetch.

Set `XTF_SYNDICATION=0` (or `XTF_XCOM=0`) to take either out of the auto chain.

**Browser driver** defaults to Camofox (`localhost:9377`). Playwright users:

```bash
pip install ".[playwright]"            # from the clone
export XTF_BROWSER=playwright          # or: --browser-driver playwright
```

## 📊 Capabilities

| Feature | Flag | Backend |
|---------|------|---------|
| Single tweet (text, stats, media, quotes) | `--url` | fxtwitter |
| Reply comments (threaded) | `--url --replies` | nitter / browser |
| User timeline (paginated, complete) | `--user` | **xauth** |
| User timeline (login-free) | `--user` | xcom → syndication → nitter → browser |
| Search | `--search` | nitter |
| User profile | `--user-info` | fxtwitter → nitter → syndication |
| X List tweets | `--list` | browser |
| X Article full text | `--article` | browser |
| Mentions monitor (incremental, cron-friendly) | `--monitor` | nitter / browser |
| Archive fetch results (dedupe, SQLite) | `--ledger <db>` | any |
| Search / stats the archive (offline) | `--ledger <db> --query/--stats` | offline |

**Exit codes** (cron-friendly): `0` success / no new mentions · `1` error / new mentions found · `2` monitor setup error.

**Errors are machine-readable.** Every failure carries `error` (human message) plus `error_code` — one of `invalid_input`, `not_found`, `rate_limited`, `upstream_down`, `backend_unavailable`, `all_backends_failed` — so agents can branch on it. `all_backends_failed` additionally includes per-backend `error_causes`.

## 📚 推文库 (Ledger)

`--ledger <db>` turns xtf into a **fetch + archive + query** local tweet library: every timeline / search / list / replies / single-tweet fetch is archived into a SQLite DB, deduped by `tweet_id` (`INSERT OR IGNORE`, idempotent). Schema is compatible with the tweet-ledger (OpenClaw) `tweets` table, so the same DB can be read by both tools.

```bash
# Fetch + archive a timeline
xtf --user YuLin807 --limit 20 --ledger ~/tweets.db

# Search the archive (offline)
xtf --ledger ~/tweets.db --query "sop"

# Stats: totals, languages, media/urls, time ranges
xtf --ledger ~/tweets.db --stats
```

Behavior without `--ledger` is unchanged (3.0.0-compatible). Archiving never breaks a successful fetch — on failure the JSON envelope carries `ledger_error` instead. Single-tweet (fxtwitter) dicts lack `tweet_id`, so the CLI injects it from the URL; `--replies` results are archived with `is_reply=1` and `in_reply_to_status_id` pointing at the parent tweet.

`tweets` table: `tweet_id` (PK) · `created_at` · `full_text` · `lang` · `source_file` · `is_reply` · `in_reply_to_status_id` · `retweeted_status_id` · `quoted_status_id` · `urls_json` · `media_json` · `raw_json` · `imported_at`

End-to-end integration test record: [docs/e2e-integration.md](docs/e2e-integration.md).

## 🐍 Python API

```python
from xtf import Router, NotFound, RateLimited

router = Router()                                  # backend="auto"
tweet   = router.fetch_tweet("user", "1234567890") # dict, v1-compatible shape
tweets  = router.fetch_timeline("user", limit=20)  # list[Tweet]
replies = router.fetch_replies("user", "1234567890")
results = router.search("openclaw", limit=10)

for tw in tweets:
    print(tw.author, tw.likes, tw.text)
    print(tw.to_dict())                            # JSON-ready
```

All backends normalize into one `Tweet` / `Reply` / `Profile` / `Article` schema — your downstream prompt only ever needs to describe one shape.

## ⚙️ Configuration

Everything is an environment variable (CLI flags override):

| Variable | Default | Meaning |
|----------|---------|---------|
| `XTF_AUTH_TOKEN` | — | X `auth_token` cookie. Enables the `xauth` backend (full timeline) |
| `XTF_CT0` | — | X `ct0` cookie, sent as `x-csrf-token` |
| `XTF_XAUTH` | `1` | Set to `0` to keep `xauth` out of the auto chain |
| `XTF_NITTER` | `http://127.0.0.1:8788` | Comma-separated Nitter instances, tried in order with failover |
| `XTF_XCOM` | `1` | Set to `0` to drop the logged-out x.com profile backend |
| `XTF_SYNDICATION` | `1` | Set to `0` to drop the login-free syndication backend from the auto chain |
| `XTF_SYNDICATION_URL` | `https://syndication.twitter.com` | Comma-separated syndication base URLs (for a mirror/proxy) |
| `XTF_SYNDICATION_TTL` | `90` | Seconds a fetched timeline is reused in-process (`0` disables caching) |
| `XTF_WAIT_RATE_LIMIT` | `0` | Seconds to sleep waiting for a quota refill when a `429` carries a reset hint (`0` = fail fast) |
| `XTF_BROWSER` | `camofox` | Browser driver: `camofox` or `playwright` |
| `XTF_BROWSER_PORT` | `9377` | Camofox HTTP port |
| `XTF_LANG` | `zh` | Message language: `zh` or `en` |
| `XTF_CACHE_DIR` | `~/.x-tweet-fetcher` | Mentions-monitor cache |

## 📡 Consuming the snapshots

The scheduled job publishes a read-only JSON feed that any program or agent can
consume without authentication — see **[docs/snapshot-api.md](docs/snapshot-api.md)**
for the full contract (URLs, schema, field coverage, edge cases).

```bash
curl -sL https://raw.githubusercontent.com/303562915/x-tweet-fetcher/main/snapshots/latest.json | jq '.count'
```

`NITTER_URL` (the v1 name) is still honored as a fallback for `XTF_NITTER`.

## 🏗 Self-hosted Nitter

Public Nitter instances are unreliable and frequently dead — as of 2026 most are gone. **Timelines no longer need Nitter at all** (they use the syndication backend), so a Nitter instance is now only required for **search**, **replies**, and **mentions monitoring**. For those, self-hosting is strongly recommended:

```bash
# See https://github.com/zedeus/nitter for full setup
docker run -d -p 8788:8080 --name nitter zedeus/nitter:latest
export XTF_NITTER=http://127.0.0.1:8788
```

Multiple instances failover automatically:

```bash
export XTF_NITTER=http://127.0.0.1:8788,https://your-backup-instance.example
```

If no instance is reachable, you get a clear error (`error_code: "all_backends_failed"`, with each backend's reason — e.g. `backend_unavailable` — under `error_causes`) telling you exactly what to set. Never a silent empty result.

## 🤖 GitHub Actions

`.github/workflows/fetch-tweets.yml` runs the whole pipeline on a schedule — no server, no Nitter instance, no browser:

```
GitHub Actions (cron) -> xtf --user <name> --limit 30 --backend xauth
                      -> snapshots/<name>.json + latest.json -> committed back to the repo
```

**Setup (once):**

```bash
gh secret set XTF_AUTH_TOKEN -R <you>/<repo> -b '<auth_token cookie>'
gh secret set XTF_CT0        -R <you>/<repo> -b '<ct0 cookie>'
```

- Fires **hourly** (`0 * * * *`) and on manual **Run workflow** with `username` / `limit` / `backend` inputs.
- Merges each fetch into `snapshots/<name>.json` — deduped by `tweet_id`, sorted by absolute time, truncated to `limit` — so the file is a rolling window of the newest N posts.
- Also writes `snapshots/latest.json` and commits both as `github-actions[bot]`.
- A failed fetch goes red with the stderr attached; it never commits a fake or stale snapshot.

**Why `xauth` and not the free endpoints.** Measured from a GitHub runner, the anonymous
endpoints are unusable: `syndication` answered `429` with `x-rate-limit-limit: 30,
remaining: 0` (the whole shared runner IP pool competes for it) and the logged-out
`x.com` profile page answered `429` with **no reset hint at all**, so waiting does not
help. The signed-in GraphQL call is not affected — a verified run fetched
`tweets=30 backend=xauth` across 2 pages and committed the snapshot.

Set `XTF_WAIT_RATE_LIMIT` (the workflow ships `1500`) to let the free backends sleep out a
quota reset when they *do* get a reset hint. Cookies expire on X's schedule — `auth_token`
typically lasts months; when the job starts failing, re-run the two `gh secret set`
commands. Nothing is ever written to the repository, and rotating the cookie invalidates
the old value.

Consume the snapshot over raw HTTP, e.g.:

```bash
curl -s https://raw.githubusercontent.com/<you>/<repo>/main/snapshots/latest.json | jq '.count'
```

## 📁 Project Structure

```
src/xtf/
├── models.py        # Tweet / Reply / Profile / Article dataclasses
├── backends/
│   ├── fxtwitter.py # single tweets + profiles
│   ├── xauth.py     # signed-in GraphQL: full paginated timeline
│   ├── xcom.py      # logged-out x.com profile page (current but short)
│   ├── syndication.py # login-free widget timelines
│   ├── nitter.py    # direct HTTP, multi-instance failover
│   └── browser.py   # Camofox / Playwright snapshot fetching
├── parsers/         # pure functions, locked by fixture tests
│   ├── syndication_json.py # __NEXT_DATA__ -> Tweet entries
│   ├── xcom_ssr.py         # logged-out profile payload
│   └── x_graphql.py        # GraphQL timeline responses
├── data/x_graphql.json  # captured query ids + feature flags (refreshable)
├── router.py        # auto-fallback chain
├── monitor.py       # incremental mentions monitor
└── cli.py           # the `xtf` command
scripts/fetch_tweet.py              # v1-compatible entry point (thin shim)
scripts/refresh_graphql_template.py # recapture X's query ids when they rotate
tests/fixtures/                     # captured page structures — regression protection
.github/workflows/fetch-tweets.yml  # scheduled fetch -> snapshots/*.json
```

## 🔄 Migrating from v1

`python3 scripts/fetch_tweet.py` still works with all v1 flags and exit codes, and JSON fields are unchanged for every mode **except `--search`**, whose per-tweet schema is now unified with `--user` (fields renamed, `url`/`has_media`/`media_urls` dropped). See [MIGRATION.md](MIGRATION.md) for the full list, including where the analytics/China/Obsidian scripts went (spoiler: their own repos — this project is now purely about fetching tweets; the old world lives at the `v1-legacy` tag).

## 🧪 Development

```bash
pip install -e ".[dev]"
pytest          # all parsers locked by fixture tests
ruff check src tests
```

When Nitter or X change their page structure, capture a fresh snapshot into `tests/fixtures/` — the failing test will show exactly which parser and field broke.

## 🙏 Acknowledgments

- **[Nitter](https://github.com/zedeus/nitter)** by [zedeus](https://github.com/zedeus) — self-hosted Twitter frontend
- **[FxTwitter](https://github.com/FxEmbed/FxEmbed)** — public API for single tweet data
- **[Camofox](https://github.com/openclaw/camofox)** — anti-fingerprint browser, default browser driver
- **[Playwright](https://github.com/microsoft/playwright)** — alternative browser automation driver
- **[OpenClaw](https://github.com/openclaw/openclaw)** — AI agent framework this tool grew up in

## 📜 License

[MIT](LICENSE)

---

<div align="center">

*Three backends. Auto fallback. Built for AI agents.*

**[GitHub](https://github.com/ythx-101/x-tweet-fetcher)** · **[Issues](https://github.com/ythx-101/x-tweet-fetcher/issues)** · **[#22 Teahouse](https://github.com/ythx-101/openclaw-qa/discussions/22)** · **[Agent Waystation](https://github.com/ythx-101/openclaw-qa)**

</div>
