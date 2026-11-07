# X Timeline Snapshot API

一个**只读、无需认证**的数据接口：GitHub Actions 每小时把某个 X 账号的最近 N 条原创推文
抓成一个 JSON 文件提交回仓库，任何程序/Agent 直接读 raw URL 即可消费。

- 仓库：`https://github.com/303562915/x-tweet-fetcher`
- 抓取实现：本仓库的 `xauth` 后端（登录态 GraphQL，见 [README](../README.md)）
- 当前账号：**@aleabitoreddit**

---

## 1. 数据地址

| 用途 | URL |
|------|-----|
| **最近一次抓取（推荐）** | `https://raw.githubusercontent.com/303562915/x-tweet-fetcher/main/snapshots/latest.json` |
| 指定账号 | `https://raw.githubusercontent.com/303562915/x-tweet-fetcher/main/snapshots/<username>.json` |
| 网页查看 | `https://github.com/303562915/x-tweet-fetcher/blob/main/snapshots/latest.json` |

`latest.json` 与 `<username>.json` 内容相同，前者始终指向最近一次抓取的账号。
用响应里的 `username` 字段判断当前是谁，不要写死。

> 如果本仓库被 fork 到别处，把 URL 里的 `303562915/x-tweet-fetcher` 换成对应路径即可。

## 2. 怎么取

```bash
# curl
curl -sL https://raw.githubusercontent.com/303562915/x-tweet-fetcher/main/snapshots/latest.json

# 只看最新一条的正文
curl -sL .../latest.json | jq -r '.tweets[0] | "\(.created_at)  \(.text)"'

# 条数
curl -sL .../latest.json | jq '.count'
```

```python
import urllib.request, json

URL = "https://raw.githubusercontent.com/303562915/x-tweet-fetcher/main/snapshots/latest.json"
with urllib.request.urlopen(URL, timeout=30) as r:
    data = json.load(r)

for tw in data["tweets"]:
    print(tw["created_at"], tw["tweet_id"], tw["likes"], tw["text"][:80])
```

```javascript
const data = await (await fetch(URL)).json();
console.log(data.count, data.tweets[0].text);
```

```bash
# 用 gh（私有/需要认证的场景）
gh api repos/303562915/x-tweet-fetcher/contents/snapshots/latest.json \
  --jq '.content' | base64 -d | jq '.count'
```

## 3. 数据结构

```json
{
  "username": "aleabitoreddit",
  "count": 30,
  "backend": "xauth",
  "updated_at": "2026-11-07T12:30:11Z",
  "tweets": [ /* 最新在前 */ ]
}
```

| 顶层字段 | 类型 | 说明 |
|----------|------|------|
| `username` | string | 抓取的账号（不含 `@`） |
| `count` | int | `tweets` 实际条数，**≤ limit** |
| `backend` | string | 产出这份数据的后端，正常为 `"xauth"` |
| `updated_at` | string | 本次抓取时间，ISO-8601 UTC |
| `tweets` | array | 推文数组，**严格按时间倒序** |

### 推文对象

| 字段 | 类型 | 出现情况 | 说明 |
|------|------|:--------:|------|
| `tweet_id` | string | ✅ 总是 | 推文 ID，全局唯一，可作去重主键 |
| `author` | string | ✅ 总是 | 作者 handle，形如 `"@aleabitoreddit"` |
| `author_name` | string | ✅ 总是 | 作者显示名 |
| `text` | string | ✅ 总是 | 正文；长文（note tweet）已展开为完整文本 |
| `created_at` | string | ✅ 总是 | 发帖时间，Twitter 格式 UTC：`"Sat Nov 07 12:05:47 +0000 2026"` |
| `time_ago` | string | ✅ 总是 | 抓取时刻的相对时间（`"3m"` / `"2h"` / `"1d"`），仅供展示，**不要用它排序** |
| `likes` | int | ✅ 总是 | 点赞数 |
| `retweets` | int | ✅ 总是 | 转推数 |
| `replies` | int | ✅ 总是 | 回复数 |
| `views` | int | ✅ 总是 | 浏览数（0 表示上游未提供） |
| `lang` | string | ⚠️ 常见 | 语言代码；上游缺失时该键不出现 |
| `media` | string[] | ❌ 可选 | 图片/视频直链；视频取最高码率 mp4 |
| `urls` | string[] | ❌ 可选 | 正文中的展开链接；没有外链时该键不出现 |
| `quoted_tweet` | object | ❌ 可选 | 被引用的推文，同结构子集 |
| `quoted_status_id` | string | ❌ 可选 | 被引用推文 ID |
| `retweeted_by` | string | ❌ 可选 | 转推者 handle（本快照只含原创推文，通常不出现） |
| `in_reply_to_status_id` | string | ❌ 可选 | 若为回复，父推文 ID |

> **统一建议**：除 `tweet_id` / `text` / `created_at` 外，其余字段一律用 `.get(key, default)`
> 读取，不要假设键一定存在。

**实测字段覆盖率**（`2026-11-07T12:30:11Z` 快照，30 条）：

| 字段 | 命中 | 字段 | 命中 |
|------|-----:|------|-----:|
| `tweet_id` `author` `author_name` `text` `created_at` `time_ago` `likes` `retweets` `replies` `views` `lang` | 30/30 | `media` | 19/30 |
| | | `quoted_tweet` / `quoted_status_id` | 16/30 |
| | | `urls` / `retweeted_by` | 0/30 |

**真实样例行**（节选自 `2026-11-07T12:30:11Z` 快照）：

```json
{
  "author": "@aleabitoreddit",
  "author_name": "Serenity",
  "text": "今年收益率仍有 +594.4% YTD，不过巴菲特大概睡得比我安稳。\n\n我那套面向2027–2028年的光电子和存储投资逻辑……",
  "time_ago": "24m",
  "likes": 252,
  "retweets": 12,
  "replies": 31,
  "views": 32926,
  "tweet_id": "2119038604819468364",
  "media": ["https://pbs.twimg.com/media/HWhULggacAANEX7.png"],
  "created_at": "Sat Nov 07 12:05:47 +0000 2026",
  "lang": "zh"
}
```

## 4. 时效性与保证

- **更新频率**：每小时一次（GitHub Actions cron `0 * * * *`，UTC 整点）。
- **窗口大小**：滚动保留最新 N 条（默认 `limit=30`）。
- **顺序**：始终严格按 `created_at` 倒序，`tweets[0]` 是最新一条。
- **去重**：按 `tweet_id` 合并，重复抓取不会产生重复条目。
- **无变化不提交**：没有新推文时不会产生新 commit（`updated_at` 会变，但内容相同的话
  Actions 会跳过提交）。

## 5. 边界情况（调用方必须处理）

| 情况 | 表现 | 建议 |
|------|------|------|
| 首次运行前 | URL 返回 `404` | 视为"暂无数据"，不要当错误重试 |
| 账号发帖少 | `count < limit` | 按实际条数处理，别假设一定有 30 |
| 可选字段缺失 | `media`/`urls`/`lang`/`quoted_tweet` 键**不存在** | 用 `.get("media", [])`，别直接索引 |
| 数据陈旧 | `updated_at` 超过 2 小时 | 可能 Actions 失败或 cookie 过期，检查 [Actions 页](https://github.com/303562915/x-tweet-fetcher/actions/workflows/fetch-tweets.yml) |
| 抓取失败 | Actions 变红，**不写入坏数据** | 旧快照仍然可读且完整 |
| 超过 30 条 | 拿不到 | 本接口只提供滚动窗口，历史需自行累积 |

## 6. 换账号 / 改条数 / 立即刷新

```bash
# 抓另一个账号（会写入 snapshots/<name>.json 并更新 latest.json）
gh workflow run fetch-tweets.yml -R 303562915/x-tweet-fetcher \
   -f username=elonmusk -f limit=30

# 立即刷新当前账号
gh workflow run fetch-tweets.yml -R 303562915/x-tweet-fetcher \
   -f username=aleabitoreddit -f limit=30
```

也可以在网页 Actions → **Fetch X timeline** → **Run workflow** 手动触发，参数同上。

## 7. 数据来源与已知限制

- 由本仓库的 **`xauth` 后端**产出：复现网页版登录态的 `UserByScreenName` →
  `UserOriginalsTimeline` GraphQL 调用，纯 HTTP，无需浏览器。
- 只返回**原创推文**，不含纯转推/回复（`UserOriginalsTimeline` 语义）。
- 凭据（`auth_token` / `ct0`）存在仓库 Secrets，**不在代码里**；cookie 失效时 job 会变红，
  重新 `gh secret set` 即可恢复。
- 数据是"抓取时刻的快照"：`likes`/`views` 等是当时的值，不会回填更新。
- X 可能轮换 GraphQL queryId；届时抓取失败，需跑
  `scripts/refresh_graphql_template.py` 重新抓取模板。

## 8. 仓库内相关文件

| 路径 | 说明 |
|------|------|
| `snapshots/latest.json` | 最近一次抓取结果（本接口主入口） |
| `snapshots/<username>.json` | 各账号的滚动快照 |
| `.github/workflows/fetch-tweets.yml` | 每小时的抓取工作流 |
| `src/xtf/backends/xauth.py` | 抓取实现 |
| `scripts/refresh_graphql_template.py` | X 轮换 queryId 后刷新模板 |
