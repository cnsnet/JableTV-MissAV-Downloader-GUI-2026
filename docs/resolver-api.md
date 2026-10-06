# UAV Resolver API

Resolver 是给 Android 客户端用的 HTTP 服务（[src/uav_downloader/resolver/app.py](../src/uav_downloader/resolver/app.py)），
负责浏览站点、解析播放地址、提交远程下载，以及把视频信息存进 SQLite（[store.py](../src/uav_downloader/resolver/store.py)）。

> 本文档只记录当前有哪些接口；每次增删或修改接口后同步更新接口总数和对应表格。

## 概览

`/api` 下共 **19 个接口**（按 方法 + 路径 计），另有不在 `/api` 下的 `GET /health`。

| # | 方法 | 路径 | 分组 |
|---|---|---|---|
| 1 | POST | `/api/resolve` | 解析 / 下载 |
| 2 | POST | `/api/download` | 解析 / 下载 |
| 3 | GET | `/api/detail` | 解析 / 下载 |
| 4 | GET | `/api/browse/sites` | 浏览 |
| 5 | GET | `/api/browse/{site}/categories` | 浏览 |
| 6 | GET | `/api/browse/{site}/videos` | 浏览 |
| 7 | GET | `/api/browse/{site}/search` | 浏览 |
| 8 | GET | `/api/browse/{site}/related` | 浏览 |
| 9 | GET | `/api/browse/thumb` | 浏览 |
| 10 | POST | `/api/crawl` | 爬取 |
| 11 | GET | `/api/crawl` | 爬取 |
| 12 | POST | `/api/crawl/full` | 爬取 |
| 13 | GET | `/api/crawl/full` | 爬取 |
| 14 | POST | `/api/backfill` | 爬取 |
| 15 | GET | `/api/backfill` | 爬取 |
| 16 | GET | `/api/videos` | 已存视频 |
| 17 | DELETE | `/api/videos` | 已存视频 |
| 18 | POST | `/api/query` | 数据查询 |
| 19 | GET | `/api/query/tables` | 数据查询 |

**认证**：除 `/health` 和 `/api/browse/thumb` 外，所有接口都要带请求头
`X-API-Key: <RESOLVER_API_KEY>`，否则返回 401。`/api/browse/thumb` 还可以用
query 参数 `api_key=` 传 key（图片加载库加不了请求头）。

**站点**：`{site}` 取 `jabletv` 或 `missav`，其他值返回 404。

**常见错误码**：400 参数错误 / 不支持的 URL；404 视频已软删除或不存在；
409 预取功能已关闭（`RESOLVER_PREFETCH=0`）时调用爬取接口；422 页面解析失败；
502 站点或 Recombee 被拦截、网络错误；503 数据库写入失败。

## 解析 / 下载

### POST `/api/resolve`

抓取视频页面、解析出播放地址，并提交到远程下载服务器。

请求体：`{"url": "<页面 URL>", "output_name": "<可选，默认 <id>.mp4>"}`

返回：`{"ok": true, "output_name": "...", "resolved_url": "..."}`

### POST `/api/download`

直接把已经解析好的地址提交下载，不再抓页面（客户端已经调过 `/api/detail` 的情况）。

请求体：`{"resolved_url": "...", "output_name": "<可选，默认 video.mp4>"}`

返回同 `/api/resolve`。

### GET `/api/detail?url=`

解析页面，只用于预览播放，不提交下载。库里的 `resolved_url` 还有效时直接返回，不抓页面。

返回：`ok`、`title`、`id`（页面 slug）、`description`、`thumbnail`、
`resolved_url`、`headers`（播放时 CDN 需要的 Referer / Origin）、
`has_chinese_subtitle`、`is_uncensored_leak`。

## 浏览

列表类接口（`videos` / `search` / `related`）返回 `{"videos": [...], "page": n}`
（`related` 没有 `page`）。每个视频包含：`url`、`title`、`thumbnail`、
`duration`（显示用字符串，如 `2:01:00`）、`id`（slug）、`description`、
`has_chinese_subtitle`、`is_uncensored_leak`。列表里出现的视频会存进库里，
并在后台排队预取详情；已软删除的视频不会出现。

### GET `/api/browse/sites`

返回 `{"sites": [{"key": "jabletv", "name": "JableTV"}, ...]}`。

### GET `/api/browse/{site}/categories?refresh=false`

分类 / 标签列表。按站点在内存里缓存 30 天，`refresh=true` 强制重新抓取。
返回 `{"categories": [{"name", "url", "count", ...}]}`。

### GET `/api/browse/{site}/videos?category_url=&page=1`

某个分类下的视频列表。

### GET `/api/browse/{site}/search?q=&page=1`

搜索。`q` 只是番号时直接从库里返回第 1 页（后台再搜一次站点，补上新的变体页面），之后的页为空。

### GET `/api/browse/{site}/related?url=&count=12`

相关视频：MissAV 来自 Recombee 推荐；Jable 来自视频页里的「猜你喜歡」。
MissAV 的 Recombee 请求失败时返回空列表。

### GET `/api/browse/thumb?url=&api_key=`

代理缩略图，原样返回图片内容。

## 爬取

两个爬取都只支持 MissAV，在预取的后台队列里运行（共享节奏和退避）：
沿 Recombee related 广度优先走，每个视频都存全部 Recombee 属性；还没有
`resolved_url` 的视频会排队抓页面。`limit`（默认 `RESOLVER_CRAWL_LIMIT`=10000）
计的是「第一次拿到 Recombee 属性的视频」数。

队列全部跑完后会自动 **补抓**：把 MissAV 中 `resolved_url` 为空、未删除的行重新排队，
一轮一轮直到每个页面试满 `RESOLVER_BACKFILL_ATTEMPTS`（默认 3）次；返回 4xx 的页面立即放弃。
每次 POST 新的爬取都会让补抓重新开始计数；也可以用 `POST /api/backfill` 不爬取、直接手动补抓。

### POST `/api/crawl`

从页面出发爬取。请求体：`{"url": "<MissAV 页面 URL>", "limit": <可选>}`

返回：`{"ok": true, "seed_queued": bool, ...进度字段}`

### GET `/api/crawl`

`/api/crawl` 这个爬取的进度。

### POST `/api/crawl/full`

从 Recombee 关键字搜索的结果出发爬取（番号、女优、标题关键字……）。

请求体：`{"query": "...", "limit": <可选>, "count": 12}`（`count` 为起始搜索返回条数，1–100）

返回：`{"ok": true, "search_queued": bool, ...进度字段}`

### GET `/api/crawl/full`

`/api/crawl/full` 这个爬取的进度。

**进度字段**（两个爬取共用格式）：

| 字段 | 含义 |
|---|---|
| `found` | 已计入 limit 的视频数 |
| `remaining` | 距 limit 还剩多少 |
| `seen` | 本进程内已走过的页面数 |
| `queued` / `queued_background` | 预取主队列 / 后台队列长度 |
| `backfill_active` | 是否正在补抓 |
| `backfill_sites` | 补抓覆盖的站点 |
| `unresolved` | 这些站点中还没有 `resolved_url`、未删除的行数 |

### POST `/api/backfill`

手动补抓：把 `site` 中 `resolved_url` 为空、未删除的行重新排进后台队列，每个页面重新给
`RESOLVER_BACKFILL_ATTEMPTS` 次机会（规则同上面的自动补抓，共享预取的节奏和退避）。

请求体：`{"site": "missav"}`（默认 `missav`；`jabletv` 只补 JableTV；`null` 两个站点都补）

返回：`{"ok": true, "queued": <本轮排队数>, "backfill_active", "backfill_sites", "unresolved"}`。
`queued` 为 0 表示没有可补的行。

### GET `/api/backfill`

补抓进度：`{"backfill_active", "backfill_sites", "unresolved", "queued", "queued_background"}`。

## 已存视频

### GET `/api/videos?page=1&size=12&site=&deleted=false&search=`

库里存的视频，按入库时间倒序；`deleted=true` 时改为列出已软删除的（按删除时间倒序）。
`size` 为 1–100。`search` 模糊匹配番号（`ipx` → `ipx-789`、`ipx-789-uncensored-leak`…）
或任意标题，完全匹配 / 前缀匹配的排在前面。

返回：`{"videos": [...], "page", "size", "total", "pages"}`，每个视频的字段：

| 字段 | 含义 |
|---|---|
| `url` / `site` | 页面 URL（已规范化）/ 站点 |
| `id` | 页面 slug，如 `ipx-771-uncensored-leak` |
| `row_id` | 表内自增整数 id |
| `code` | 番号，去掉变体后缀，如 `IPX-771` |
| `title` / `description` | 显示用标题（「番号 + 中文」）及去掉番号的部分 |
| `title_ja` / `title_cn` / `title_zh` | Recombee 的原标题 / 简体 / 繁体 |
| `has_chinese_subtitle` / `has_english_subtitle` / `is_uncensored_leak` | 标记，`null` 为未知 |
| `actors` / `actresses` / `genres` | 数组 |
| `duration` / `duration_seconds` | 显示用字符串 / 秒数 |
| `released_at` / `type` | 发行日期 / 类型 |
| `thumbnail` / `preview` | 封面 / 预览视频（可能 404，表示没有预览） |
| `has_detail` | 页面是否抓过 |
| `resolved_url` / `resolved_expires` / `resolved_valid` / `headers` | 上次抓到的播放地址、过期时间、是否仍可用、所需请求头 |
| `details_at` | 上次写入 Recombee 属性的时间，`null` 为从未 |
| `created_at` / `updated_at` / `deleted_at` | 时间戳（秒） |

Recombee 相关字段要等爬取经过该视频后才有值。播放仍应走 `/api/detail`。

### DELETE `/api/videos?url=`

软删除：行保留，但之后除 `/api/videos?deleted=true` 外任何接口都不再返回它。
返回 `{"ok": true}`，不存在或已删除返回 404。

## 数据查询

### POST `/api/query`

只读的临时查询，相当于执行
`SELECT <query> FROM <table> WHERE <filter> ORDER BY <order> LIMIT <limit> OFFSET <offset>`。

请求体：

| 字段 | 默认 | 含义 |
|---|---|---|
| `query` | `*` | 要查的列 / 表达式，如 `id, slug, resolved_url`、`site, count(*) n` |
| `table` | `videos` | 表名，必须是库里已有的表（见 `/api/query/tables`） |
| `filter` | 空 | WHERE 条件，如 `resolved_url IS NULL AND site = 'missav'`；可在末尾接 `GROUP BY` |
| `order` | 空 | ORDER BY，如 `id DESC` |
| `limit` / `offset` | `100` / `0` | 分页，`limit` 为 1–1000 |

`query` / `filter` / `order` 是原样拼进去的 SQL 片段。连接是只读的（`PRAGMA query_only`）
且只能执行一条语句，写操作或 `;` 拼多条语句会报错；单次查询超过 10 秒会被中止。

返回：`{"columns": [...], "rows": [{列: 值}, ...], "total", "limit", "offset"}`，
`total` 是不分页时的总行数（有 `GROUP BY` 时为组数）。表名不存在、SQL 出错返回 400。

### GET `/api/query/tables`

库里有哪些表：`{"tables": ["videos"]}`。

## 环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `RESOLVER_API_KEY` | （必填） | API Key，未设置则拒绝启动 |
| `RESOLVER_DB_PATH` | `/data/resolver.db` | SQLite 路径 |
| `RESOLVER_PREFETCH` | `1` | 设为 `0` 关闭后台预取（爬取接口也随之不可用） |
| `RESOLVER_PREFETCH_DELAY_MIN` / `_MAX` | `3` / `8` | 两次后台抓取之间的随机间隔（秒） |
| `RESOLVER_PREFETCH_QUEUE` | `300` | 主队列上限，超出丢弃最旧的 |
| `RESOLVER_CRAWL_LIMIT` | `10000` | 每次 POST 爬取的默认 limit |
| `RESOLVER_BACKFILL_ATTEMPTS` | `3` | 补抓时每个页面最多尝试次数 |

