# Changelog

## 3.3.0 (2026-11-07)

### 新增：xauth 后端（登录态 GraphQL，完整分页时间线）

- `src/xtf/backends/xauth.py`：复现网页版的 GraphQL 调用
  （`UserByScreenName` → `UserOriginalsTimeline`），纯 HTTP，无需浏览器
  - 一页 20 条，cursor 可一直往前翻；实测 `--limit 30` → 30 条，严格时间倒序
  - 凭据来自环境变量：`XTF_AUTH_TOKEN`（auth_token）、`XTF_CT0`（ct0，作为 x-csrf-token）
  - **登录态请求不受数据中心 IP 限制** —— 这是 GitHub Actions 能跑通的关键
- `src/xtf/parsers/x_graphql.py`：响应遍历、推文归一化、cursor 处理
  （含长文 `note_tweet`、转推展开、引用、媒体最优码率）
- `src/xtf/data/x_graphql.json`：抓取下来的 queryId + features（可刷新）
- `scripts/refresh_graphql_template.py`：X 轮换 queryId 后重新抓取模板

### 新增：xcom 后端（免登录 x.com 首屏，真实但少）

- `src/xtf/backends/xcom.py` + `parsers/xcom_ssr.py`：解析未登录 profile 页内嵌的
  `__isTweetResult` 对象，按作者过滤，`\n`/HTML 实体还原，时间倒序
- 实测 `aleabitoreddit`：首屏 3 条，最新一条是几分钟前的**真实新推**
  （同一账号 syndication 给的是 **2 条 2025 年的旧推文**）
- `/with_replies`、`/media` 变体均返回 0；payload 无 cursor，无法翻页

### 路由

- auto 链：`xauth`（配置了 cookie 时）→ `xcom` → `syndication` → `nitter` → `browser`
- `xcom` 成功后**不回退** syndication，避免把旧推文混进"最新 N 条"
- 新增开关 `XTF_XAUTH` / `XTF_XCOM`；`--backend` 增加 `xauth` / `xcom`

### 实测记录（2026-11-07）

- **GitHub runner**：匿名入口全部 429（syndication `x-rate-limit-limit: 30, remaining: 0`；
  x.com profile 页 429 且**无 reset 提示**）；`fxtwitter` 正常；公开 Nitter（28 个）、
  公共 CORS 代理（allorigins/codetabs/r.jina.ai）、RSS 桥、twiiit 聚合全部不可用
- **xauth 在 runner 上验证通过**：`tweets=30 backend=xauth`，分 2 页抓取并提交快照
- `auth_token` cookie 实测有效期到 2027-10（数月级），不是"很快就过期"

### 修复

- `.gitignore` 的 `data/` 规则把 `src/xtf/data/x_graphql.json` 一并忽略，
  导致 runner 上 editable 安装缺模板；已加 `!src/xtf/data/` 例外

### 测试

- 新增 `tests/test_x_graphql.py`（14 例）与 `tests/test_xcom_ssr.py`（13 例）
- `test_router_mode_chains` 覆盖五后端链路与各开关
- 全量：152 passed

---

## 3.2.0 (2026-11-07)

### 新增：syndication 后端（免登录用户时间线，零依赖）

- `src/xtf/backends/syndication.py`：读取 X 官方公开嵌入入口
  `syndication.twitter.com/srv/timeline-profile`，一次请求返回该用户最近约 100 条推文
  - 无需登录、无需 API Key、**无需 Nitter 实例**、无需浏览器
  - 单进程内 90 秒 TTL 缓存（`XTF_SYNDICATION_TTL`），429 时自动回退 Nitter / browser
  - `XTF_SYNDICATION=0` 可从 auto 链中摘除；`XTF_SYNDICATION_URL` 支持自建镜像
- `src/xtf/parsers/syndication_json.py`：纯函数解析 `__NEXT_DATA__`
  - 归一化转推（展开原推 + `retweeted_by`）、引用推文、媒体（图片/最高码率视频）、外链
  - 绝对时间戳 -> `time_ago`（`created_at` 原文保留）
- `Tweet` 模型新增可选字段：`created_at` / `lang` / `urls` / `in_reply_to_status_id` /
  `retweeted_status_id` / `quoted_status_id`（仅 syndication 填充；其余后端输出不变，
  v1 JSON 兼容）
- 路由优先级：timeline `syndication → nitter → browser`；user-info `fxtwitter → nitter → syndication`
- CLI：`--backend` 新增 `syndication`，帮助文案与 `--user-info` 说明同步更新

### 新增：GitHub Actions 定时抓取

- `.github/workflows/fetch-tweets.yml`：cron（每 6 小时）+ 手动触发
  - 抓取 `--user <name> --limit <n>` 写入 `snapshots/<name>.json` 与 `snapshots/latest.json`
  - 由 `github-actions[bot]` 提交回仓库；失败时只告警不提交空快照

### 新增：配额等待（`--wait-rate-limit`）

- `RateLimited` 现在携带上游的配额提示：`reset_at`（`x-rate-limit-reset`）与
  `retry_after`（`Retry-After`），并提供 `wait_seconds()`
- `http.get_text` 解析上述响应头并注入异常
- syndication 后端在 429 且 reset 在预算内时**睡眠等待配额刷新后重试**，
  不再直接失败；`--wait-rate-limit SECONDS` / `XTF_WAIT_RATE_LIMIT` 控制预算
  （默认 0 = 快速失败，workflow 设为 1500 秒）

### 实测记录（2026-11-07）

- GitHub runner 上 `api.fxtwitter.com` 可直连（HTTP 200），syndication 因**共享数据中心
  IP 配额耗尽**返回 429（`x-rate-limit-limit: 30`、`remaining: 0`、reset 约 14 分钟后）
- 开启等待预算后，workflow 实测成功：`fetching @nasa (limit 50)` →
  `tweets=19 backend=syndication` → 由 bot 提交 `snapshots/nasa.json`（严格时间倒序）
- 公开 Nitter 实例（28 个）与公共 CORS 代理（allorigins / codetabs / r.jina.ai）全部不可用
- Playwright：headless Chromium 访问 x.com 返回 403；headed 可加载但 CI 无显示器且不稳定

### 测试

- `tests/test_syndication.py`：24 个用例覆盖页面解析、转推/引用/媒体/时间归一化、
  时间倒序排序、模型字段输出、配额等待与预算边界
- `tests/test_core.py::test_router_mode_chains`：更新为四后端链路，并覆盖 `XTF_SYNDICATION=0`
- 全量：125 passed

### 文档

- README / SKILL.md：四后端表格、syndication 限流与等待机制、页大小差异、GitHub Actions 章节

---

## 3.1.0 (2026-08-09)

### 新增：推文库 (Ledger)

- `src/xtf/ledger.py`：SQLite 推文归档核心（schema 与 tweet-ledger 兼容）
  - `archive_tweets`：按 `tweet_id` 去重归档（`INSERT OR IGNORE`），写导入回执
  - `query_ledger`：关键词查询（`full_text` 子串匹配，支持 limit/offset）
  - `ledger_stats`：总量/回复/引用/媒体/链接/语言分布/时间范围统计
- CLI 新参数：
  - `--ledger <db>`：抓取模式（`--user`/`--search`/`--list`/`--replies`/`--url`）下自动归档
  - `--ledger <db> --query <term>`：查询归档库
  - `--ledger <db> --stats`：统计归档库
- 兼容性：
  - 无 `--ledger` 时行为与 3.0.0 完全一致
  - 归档失败不阻断抓取（结果带 `ledger_error`）
  - 单推（fxtwitter）dict 缺少 `tweet_id` 时由 CLI 从 URL 注入
- 测试：89+ 用例通过（归档/去重/查询/统计/CLI 集成/回归）

### 修复

- `ledger_stats`：缺库/空表/无 tweets 表时不再返回 None 或抛错，恒返回完整键集

### 文档

- README 新增「推文库 (Ledger)」章节；`docs/e2e-integration.md` 端到端验证记录

---

## v3.0.0 (2026-07-06)

**Full restructure — same CLI, new internals. See MIGRATION.md.**

- Repo now focuses purely on tweet fetching; analytics / China-platform / Obsidian scripts moved out (`v1-legacy` tag)
- Installable package: `pip install x-tweet-fetcher` → `xtf` command + `from xtf import Router`
- Unified backend protocol (fxtwitter / nitter / browser) with auto-fallback router
- Unified `Tweet`/`Reply`/`Profile`/`Article` data models across all backends
  - **Breaking (`--search`)**: search results now use the normalized `Tweet` schema (`author`/`author_name`/`time_ago`) instead of v1's raw Nitter fields (`username`/`display_name`/`time`); the v1-only `url`/`has_media`/`media_urls` fields are dropped. `--user` timeline already used this schema in v1. See MIGRATION.md.
  - List-producing modes gain a top-level `backend` field; `--replies` adds `reply_count`; empty `media` is omitted rather than emitted as `[]`
- Unreachable-backend errors surface as top-level `error_code: "all_backends_failed"` with per-backend reasons (e.g. `backend_unavailable`) under `error_causes` — a clear error with exit 1, never silent empty results
- Multi-instance Nitter with health-check and failover (`XTF_NITTER=url1,url2`)
- Typed errors with machine-readable `error_code` + per-backend `error_causes`
- Retry with exponential backoff for transient upstream failures
- Test suite: parsers locked by fixture snapshot tests; CI via GitHub Actions
- Removed hardcoded public-Nitter default that contradicted README guidance
- `scripts/fetch_tweet.py` kept as a fully compatible shim
- Removed committed `__pycache__`; fixed `.gitignore`

所有重要更新记录在此。

---

## [1.6.1] - 2026-03-04

### 修复
- **转推/引用推文分离**：新增 `retweeted_by` 和 `quoted_tweet` 字段，正确识别 RT 标记和嵌套引用
- **Stats 提取修复**：icon 字符正则支持逗号分隔数字（如 2,434）；新增 stats-only 行模式
- **内容识别修复**：`_is_content_anchor` 不再将 avatar/profile link 误判为 TOC link
- **正则容错**：stats 末尾允许非数字字符（如 `..."`）

---

## [1.6.0] - 2026-03-04

### 新增
- **X Lists 抓取**：`--list <id_or_url>` 抓取 X Lists 推文
  - 支持纯数字 ID 和完整 URL（x.com/i/lists/xxx 或 twitter.com/i/lists/xxx）
  - 通过 Camofox + Nitter，零 API Key
  - 支持翻页（MAX_PAGES=10）和去重
  - 支持 `--text-only` 纯文本和 JSON 输出

---

## [1.5.0] - 2026-02-25

### 新增
- **Nitter Mentions 监控**：基于 Nitter 的实时 X 提及监控
- **version_check.py**：自动版本检查工具

---

## [1.4.0] - 2026-02-24

### 新增
- **小红书支持**：`fetch_china.py` 新增小红书平台，支持 `--proxy` 和 `--cookies`
- **搜狗微信搜索**：`sogou_wechat.py` 新增 `--resolve`（Google/DDG 解析真实 URL）和 `--via-ssh`（SSH 代理）
- **路由器代理**：`--via-router` 24/7 家庭 IP 代理搜索

---

## [1.3.0] - 2026-02-23

### 新增
- **Mentions 监控**：`--monitor @username` 实时监控谁提到了你
  - 基于 Google 搜索（通过 Camofox），零 API key
  - 增量检测 — 首次建基线，后续只报新内容
  - 支持 cron 集成（退出码 0=无新 / 1=有新）
  - 本地缓存去重（~/.x-tweet-fetcher/）

---

## [1.2.1] - 2026-02-21

### 修复
- **时间线排序（Issue #25）**：时间线模式下按 Snowflake ID 降序排列（最新在前），`tweet_id` 字段从 Nitter `/status/{id}` 链接提取，Pinned tweet 标记 `is_pinned: true`

---

## [1.2.0] - 2026-02-20

### 新增
- **国内平台支持**：新增 `fetch_china.py`，支持 4 个中国平台
  - 🔥 **微博** — 帖子、评论、互动数据
  - 🎬 **B站** — 视频信息、UP主、播放量、点赞、弹幕
  - 💻 **CSDN** — 技术文章、代码块、阅读量
  - 📖 **微信公众号** — 全文+图片，纯 HTTP 无需 Camofox
- **共享模块**：提取 `camofox_client.py`，fetch_tweet.py 和 fetch_china.py 共用
- **多输出格式**：JSON / Markdown（带 YAML frontmatter）/ 纯文本
- **自动平台识别**：给 URL 自动判断是微博/B站/CSDN/微信
- **双语 README**：中文默认 + 英文切换

### 架构
- Strategy Pattern：每个平台独立 Parser，社区可轻松扩展

---

## [1.1.0] - 2026-02-20

### 修复
- **评论区链接提取**：修复 Nitter 返回 `- link "https://..."` 格式时链接丢失的问题
- **嵌套评论**：新增 `thread_replies` 字段支持嵌套回复提取

---

## [1.0.0] - 2026-02-14

### 初始发布
- **单条推文**：通过 FxTwitter API 抓取，零依赖零 API Key
- **评论区**：通过 Camofox + Nitter 抓取回复
- **用户时间线**：支持翻页，最多 200 条
- **X Articles**：长文完整提取
- **引用推文**：自动包含
- **双语支持**：中文/英文消息
