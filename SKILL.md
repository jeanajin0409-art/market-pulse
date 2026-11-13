---
name: dealhot
description: 更新/维护 DealHOT 一级市场动态聚合页（github.com/jeanajin0409-art/market-pulse，发布于 jeanajin0409-art.github.io/market-pulse）。当用户说"更新 DealHOT"、"刷新一级市场页面"、"今天的一级市场日报"、"加一条 M&A/IPO/少数股权/融资新闻"、"重做某天的历史"等任何涉及这个页面的增、删、改、重建时使用。现行实现是 **两段式 DB 流水线**：scripts/pipeline.py 抓取入 raw_candidates → 人工精选写 sum/why → items → scripts/build.py 渲染成 index.html + archive/。不要手改 index.html，动数据前必须先读本 skill。
---

# DealHOT — 一级市场与AI动态聚合页维护指南

## 0. 现行架构

**两段式流水线**（原始候选与正式条目分离，中间有人工精选这一环）：

```
fetch ──► raw_candidates(DB) ──► 人工逐条精选，写 sum/why/cat/regions/score ──► items(DB)
                                                                                    │
                                                            build.py build ─────────┘
                                                                                    ▼
                                                              index.html + archive/<date>.html + archive/index.html
                                                                                    │
                                                                                    ▼
                                                        build.py post ──► #dealhot 频道 post
```

- 仓库：`/Users/mac/Documents/market-pulse`（GitHub `jeanajin0409-art/market-pulse`）
- 线上页面：`https://jeanajin0409-art.github.io/market-pulse/` —— push 到 `main` 后 GitHub Pages 约 1 分钟自动发布
- **唯一事实源 = `dealhot.db`**（SQLite，仓库根目录；`.gitignore` 排除，只本机一份 → 定期备份）
  - 表：`raw_candidates`（原始候选）、`items`（正式发布条目）、`meta`
  - `items` 字段：`date, t, src, cat, regions, score, title, url, sum, why, ma_cap, tags, commit_sha, raw_id`
  - 约束：`UNIQUE(date, src, title)`
- **抓取与精选 = `scripts/pipeline.py`**；**渲染与 post = `scripts/build.py`**

| 文件 | 作用 |
|---|---|
| `scripts/pipeline.py` | migrate / fetch / import-aihot / list / accept / stats |
| `scripts/build.py` | build（含发布硬闸门）/ post / dry-run |
| `fetch_aihot.py` | AIHOT v1 抓取封装（`--json` 可直接喂 pipeline） |
| `x_scan/scan.py` | X 账号抓取（conda 环境 `dealhot-x`） |
| `samples/` | 精选输入 JSON 与渲染样稿（重做历史时作为参照） |
| `dealhot.db` | 唯一事实源（不入 git） |

**凭证**在 `.secrets/`（`.gitignore` 排除）：`itjuzi.env`、`x_cookies.json`。**绝不能提交进这个公开仓库，也不能写进任何会被 push 的文件。**

**路径可覆盖**（在副本上跑样稿而不动生产 DB / 生产文件）：

```bash
export DEALHOT_DB=/tmp/test.db        # DB 路径
export DEALHOT_INDEX=/tmp/out/index.html
export DEALHOT_ARCHIVE=/tmp/out/archive
```

**编辑前必须 `git pull --ff-only origin main`**（可能有其他 agent 刚推送过更新）。

**远程 agent 接入**：用仓库的 contributor 部署钥匙（私钥由所有者提供）克隆与推送：

```bash
GIT_SSH_COMMAND="ssh -i ~/keys/market-pulse-contributor-key -o IdentitiesOnly=yes" \
  git clone git@github.com/jeanajin0409-art/market-pulse.git
git config core.sshCommand "ssh -i ~/keys/market-pulse-contributor-key -o IdentitiesOnly=yes"
```

## 1. 每日 SOP

```bash
cd /Users/mac/Documents/market-pulse
git pull --ff-only origin main

# ① 一次性 schema 迁移（幂等，可重复跑）
python3 scripts/pipeline.py migrate

# ② 抓当天候选 → raw_candidates（绝不直写 items）
python3 scripts/pipeline.py fetch --date 2026-07-25
python3 fetch_aihot.py 48 --json > /tmp/aihot.json
python3 scripts/pipeline.py import-aihot --date 2026-07-25 --file /tmp/aihot.json

# ③ 看候选（人工挑选，逐条写 sum/why/cat/regions/score）
python3 scripts/pipeline.py list --date 2026-07-25

# ④ 精选结果 → items（整批校验 + 单事务写入 + 7 日对称去重，fail-closed）
python3 scripts/pipeline.py accept --date 2026-07-25 --file samples/2026-07-25.json

# ⑤ 统计（raw/selected/published、来源、板块、score 分布、已拒收原因、字段失败）
python3 scripts/pipeline.py stats --date 2026-07-25
python3 scripts/pipeline.py stats --date 2026-07-25 --json   # 结构化验收报告

# ⑥ 渲染（先过发布硬闸门；不过就停，不写文件）
python3 scripts/build.py build --date 2026-07-25

# ⑦ 提交 + push
git add -A && git commit -m "dealhot 2026-07-25 rebuild（精选 5 条）" && git push origin main

# ⑧ 生成 #dealhot post 文本 → 发到频道
python3 scripts/build.py post --date 2026-07-25
raft message send --target "#dealhot" <<'EOF'
<post 文本原文>
EOF

# ⑨ 回报 + ack（漏 ack 会永久挂 inbox）
raft message send --target "#all:e6f76bce"   # fire→build→commit→push 链路
raft reminder ack --id 4d2c06ea-7f01-47b6-9465-3f6f58c05f84 --revision <rev>
```

**accept 是整批原子的**：任何一条不合格 → **整批 0 条落库**、退出码非 0，并逐条打印原因；
被拒的候选会持久化 `status=rejected` + `note`。改完想重新收录，先 `python3 scripts/pipeline.py reset --date <D>`。

**回归测试**（改完 pipeline/build 必须跑）：`python3 tests/run_tests.py`（8 类场景，无第三方依赖）。

`build.py build` 的输出必须逐行看：`✓ 发布闸门通过` + 五类统计 + `index.html written` / `archive/<D>.html written` / `archive/index.html updated` / 两行 `JS check ... OK` / `Main page items: N` / `Archive page items: N`。

## 2. 抓取

### 2.1 IT桔子 OpenAPI（pipeline.py fetch 已封装）

中国一级市场的结构化数据源。凭证 `.secrets/itjuzi.env`；token 有效期 1 小时，每次流程开头取一次。

**四个端点与参数**（`date_pattern=3` 按事件发生时间，快讯用 `1` 按发布时间）：

| 端点 | 内容 | 板块倾向 |
|---|---|---|
| `/investevent/get_investevent_list_v2` | 投资事件 | minority |
| `/merger/get_acquisition_list_v2` | 收购事件 | ma |
| `/merger/get_merger_list_v2` | 合并事件 | ma |
| `/news/get_spider_news_list_v2` | 快讯 | 各板块补充 |

```bash
# 正确用法（规格要求）：date_pattern + date_start/date_end
curl -s "https://openapi.itjuzi.com/investevent/get_investevent_list_v2?date_pattern=3&date_start=2026-07-25&date_end=2026-07-26&limit=100&order=1&order_rules=1" \
  -H "AUTHORIZATION: Bearer $TOKEN"
```

**实测坑（都已在 pipeline.py 里处理，勿回退）**：
- **单日区间返回 0 条** → 必须用 ≥2 天的窗口，再按 API 自带的事件日期字段（`invest_year/month/day`、`purchase_year/month/day`）精确过滤到目标日
- **单次上限 100 条** → `order=1`（新→旧）与 `order=2`（旧→新）各取一次合并
- **`?d=<date>` 不是有效参数**（旧实现踩过）：会忽略日期、返回「最新 20 条」，导致同一天内容被写进多天
- **`event_tilte` 拼写**（acquisition/merger 接口的 title 字段是 typo）
- `merger/get_merger_list_v2` 当前返回 500，合并事件暂时缺源（pipeline 会记录端点异常并继续）

**关键返回字段**：
- 投资事件：`event_title`、`event_des`、`round_name`（轮次）、`detail_amount`（**单位：万**）、`currency_name`、`approximate_amount_name`（如「未透露」）、`leading_investor`/`following_investor`/`secondary_investor`（投资方）、`com_name`/`com_industry_name`/`com_sub_industry_name`、`invest_year/month/day`、`news[]`（关联报道，含原文链接）
- 收购：另有 `purchaser[]`（收购方）、`equity_ratio`、`purchase_year/month/day`
- 快讯：`news_title`、`news_time`、`news_source`、`news_url`、`related_party[]`

**入选门槛**（API 一天上百条，页面只收重要的）：
- 投资事件：金额 ≥1 亿元人民币（`detail_amount`≥10000，单位是万）、或知名机构领投、或 AI/硬科技明星公司；战略投资/PE/老股转让优先归 minority，AI 公司大额融资可归 ai+投融资
- 收购/合并：金额 ≥5 亿元、或买卖任一方知名
- 快讯：只挑 `related_party` 含知名公司/独角兽的
- **url 必须具体原文链接**（`news[]` 里的报道链接或快讯 `news_url`）；拿不到原文链接的候选**不收录**（`pipeline.py list` 会标 ⚠️）

**配额纪律**：限流 1800 秒/200 次，总调用次数有上限。每日标准用量 = 1 次 token + 4 端点 × 2 窗口 × 2 序 ≈ 17 次，足够；**不要翻页轰炸、不要逐条查事件详情**。

### 2.2 AIHOT v1（`fetch_aihot.py`）

> ⚠️ **旧接口已于 2026-10-31 停用**：`https://aihot.virxact.com/api/public/items` 现在会 `301` → 目标路径 `404` + 空 body，**静默拿不到数据**（不报错）。不要再用旧地址。

新接口：`https://aihot.news/api/v1/items?mode=selected&limit=60&window=7d`

```bash
python3 fetch_aihot.py 48           # 打印近 48h 候选
python3 fetch_aihot.py 48 --json    # JSON 数组，可直接喂 pipeline import-aihot
```

**v1 参数映射**（旧 → 新）：`take` → `limit`；`since` → `window`（**只接受 `'24h'` / `'7d'`**）；去掉 `fields`；旧 `cursor` 弃用。
**v1 字段映射**：`item.url` → `item.links.original`（fallback `links.aihot`）；`item.source`（字符串）→ `item.source.name`（对象）。`title`/`summary`/`category`/`score`/`publishedAt` 不变。
迁移指南：https://aihot.news/agent?tab=api#legacy-api-migration

挑 score≥65 且与一级市场视角相关的条目（资本开支、融资、ARR、重要模型/产品、政策）。`publishedAt`（UTC）转北京时间 = +8 小时。

**注意**：AIHOT 公开 API 只有 7 天窗口，**不能回溯历史**。历史重做只能靠 IT桔子接口 + 仓库已有产物。

### 2.3 X (Twitter) 抓取

```bash
source /opt/anaconda3/etc/profile.d/conda.sh && conda activate dealhot-x
python x_scan/scan.py --group deals --days 3              # 一级市场记者 → minority/ma/ipo
python x_scan/scan.py --group ai --days 2 --loose         # AI 分析+大佬观点 → ai
python x_scan/scan.py --group frontier --days 7 --loose   # 前沿科技 → frontier
python x_scan/scan.py --group official --days 5 --loose   # 大厂官方产品/模型 → ai/bigtech
python x_scan/scan.py --group cn --days 4 --loose         # 中文 AI → ai/minority
python x_scan/scan.py --group mena --days 4               # 中东/主权基金 → ma/minority
```

脚本读 cookie → 直连 X GraphQL（UserByScreenName→UserTweets）→ 拉 watchlist 账号近 N 天推文 → 按数字/deal 关键词筛 → 输出候选 + `out/x_scan_*.json`。

**踩过的坑**：
- **不要用 twikit 的 login/search**：twikit 2.3.3 的 `client_transaction.init()`（`get_indices`）已与 X 当前 JS bundle 不兼容，必坏；`search_tweet` 端点对小号也返回 404。脚本纯 httpx 直连 GraphQL，operation IDs 启动时从 X `main.*.js` 自动刷新（失效自动回退硬编码默认值）
- **认证靠 cookie**，不靠密码登录。cookie 由 `.secrets/export_cookies.py chrome` 从浏览器导出到 `.secrets/x_cookies.json`（需 `auth_token`+`ct0`）。登录态在 **Chrome Profile 2**（不是 Default）。cookie 失效时让用户在那个 profile 重新登录 x.com 再导一次
- **`--loose`**：放宽筛选（不要求含数字/deal 词，只去掉<40 字的短推/转推），用于 harvest 观点、前沿科技、官方动态；deals/mena 这类纯 deal 源用默认严格筛选。loose 会混进生活/体育闲聊，**人工挑**
- 账号有封禁风险，**只用小号**

**备选：WebSearch**（脚本不可用 / 小号被限时）：`allowed_domains=["x.com"]` + `from:<handle> <关键词>`。**⚠️ 时间窗不可靠**，会混进几个月前甚至去年的旧推，每条必须核对日期、只留近 3 天。

**watchlist 在 `watchlist.md`**（按板块分组、含每个 handle 喂哪个板块）。`src` 写 `X：<显示名>`。

### 2.4 web search（一级市场新闻）

按板块搜索，时间限定最近 1-3 天：
- 少数股权：`minority stake investment private equity deal <本月> China US Asia`
- M&A：`M&A deals announced <本周> Middle East Brazil acquisition`（中东、巴西必须覆盖）
- IPO：`IPO <本周> Hong Kong listing Nasdaq pricing debut` + `港股IPO 招股 本周`
- 大厂：`big tech news <日期> Apple Google Microsoft Meta Nvidia`
- AI 融资：`AI startup funding round announced this week raises valuation`

只收有具体出处 URL 的条目；金额、估值数字要在摘要里保留。

## 3. 精选规则（人工环节，build.py 干不了）

**每天精选 3–5 条，不是固定 20 条**。当天缺某类就少发，并在汇报里说明，不凑数。

**必须逐条写**：

- `sum` —— 2–3 句**事实**摘要：金额、轮次、投资方/收购方、关键数字。模板化或与事实不符会被闸门与人工复核拦下
- `why` —— 1–2 句**解释性**推荐理由：行业意义 / 资本信号 / 风险。**不得含股票买卖意见**（闸门拦截「建议买入 / 加仓 / 目标价 / 持仓」等话术）
- `cat` —— 语义正确；`bigtech` 只能是大厂（Apple/Google/Microsoft/Meta/Amazon/Nvidia 等），医疗/法律/SaaS 不得错分
- `regions` —— 中国 / 美国 / 亚太 / 中东 / 巴西 / 欧洲 / 全球
- `score` —— 按 rubric 拉开（见 §5），**禁止批量同分**
- `url` —— **具体原文链接**；首页 URL 一律拒绝
- `dedupe_key`（可选）—— 主体公司名，用于拦截「同一事件被不同来源各写一条」

**7 日去重**：**对称窗口**（target-7d ~ target+7d，历史回填也要查未来侧），四个维度：
- `title` / `url` —— 同一篇
- `content_hash` —— 同一来源的同一事件
- `event_hash` —— **跨来源**事件指纹：结构化 deal 用「主体 + 金额 + 轮次 + 参与方」，
  新闻用 canonical URL（去 www / 去追踪参数）→ 同一件事换来源、改标题也认得出
- `dedupe_key`（主体名）—— 补强：同一家公司同周内不该发两次

**批内去重**：整批还没进库时条目之间看不见彼此 → `accept` / `replace` 自带批内去重器
（title / url / event_hash / 主体四维度），`replace` 跨多个文件共用同一个去重器。

**每条必须能对应到一条已抓到的 raw candidate**（不能拿任意 JSON 绕开 raw 层）。引用方式（按优先级）：

| 写法 | 说明 |
|---|---|
| `"raw_source": "IT桔子", "raw_source_id": "14701243"` | **推荐**：IT桔子 的 event_id，跨 DB 重建稳定 |
| `"raw_content_hash": "<sha256>"` | 候选的 content_hash |
| `"raw_url": "https://…"` | 候选的原文链接 |
| 不写 | 兜底：用条目的 `url` 去找候选；找不到即拒 |

解析到候选后还要过三关：`event_date` 必须等于目标日、`status` 必须是 `candidate`、条目的 `url` 必须等于候选的原文链接。

**精选输入 JSON 格式**（见 `samples/2026-07-25.json`）：

```json
{"date": "2026-07-25",
 "items": [
   {"raw_source": "IT桔子", "raw_source_id": "14701243",
    "dedupe_key": "原粒半导体", "src": "IT桔子",
    "title": "…", "url": "https://…", "cat": "ai", "regions": ["中国"],
    "score": 88, "sum": "2-3 句事实摘要，含关键数字", "why": "1-2 句解释性推荐理由", "tags": ["投融资"]}
 ]}
```

⚠️ `raw_id` 只是自增号，**跨 DB 重建不保证一致** → 生产回填不要用 `raw_id`，用 `raw_source + raw_source_id`。

## 4. 发布硬闸门（fail-closed）

`build.py build` 先跑 `publish_gate()`，**任一不过 → 打印错误、exit 3、不写任何文件**：

| 闸门 | 说明 |
|---|---|
| 首页 URL | path 为空或 `/` 的 URL 一律拒绝 |
| 空 sum / 空 why | 必填 |
| 批量同分 | ≥3 条全同分，或 ≥5 条里同一分超过 60% |
| 跨日重复 | 7 日窗口内 title 重复 |
| bigtech 错分 | 按 `items.raw_id` 联动 `raw_candidates.category`，医疗健康/企业服务/传统制造/教育/金融/消费 不得进 bigtech |
| 轮次未解析 | 标题或摘要里出现 `#数字` |
| CDATA | 标题或摘要含 `<![CDATA[` |
| 日报聚合标题 | 标题以 `AI HOT 日报` 开头（daily RSS 聚合不得当新闻条目） |

`pipeline.py accept` 是**第二道闸门**（逐条拒收并打印原因）：首页 URL / 空 sum/why / 股票买卖话术 / 轮次未解析 / CDATA / 日报聚合标题 / cat·regions·score 非法 / 7 日 title·url·dedupe_key 重复。

**任一闸门失败：停止 publish，输出明确错误，不得带病 push。**

**build 的零写入保证**：`build.py build` 先把三份产物渲染到**内存**并完成全部校验
（发布闸门 + `node --check` 真语法检查 + 结构检查），**全部通过后才写盘**。
- 校验失败 → 退出码非 0，三个目标文件 **byte-unchanged**（不会写半成品）
- 写盘用「临时文件 + `os.replace`」，这是**单文件原子**（每个文件要么旧内容要么新内容）；
  **不是三文件事务** —— 若磁盘在写到第二个文件时出错，仍需靠 DB 备份恢复
- 语法检查以 `node --check` 为准（把生成的 ITEMS 数组抽出来真解析）；括号计数只作补充，
  因为字符串里合法的 `{` 会被括号计数误杀

**历史日重建用 `--archive-only`**：只更新 `archive/<date>.html` 与 `archive/index.html`，**不改 `index.html`** ——
否则历史日的 build 会把主页 header 与今日导读退回历史日期，线上首页日期倒退。
主页由下一次正常发布（当天日期、不带 `--archive-only`）从 DB 重新渲染。

## 5. 条目字段语义与 score 标准

```js
{t:"7月25日",               // 北京时间显示串；周报类可写"7月上旬"/"Q3数据"
 d:"2026-07-25",            // 机器可读 ISO 日期（DB 字段 date），排序键 + 今日徽标
 src:"IT桔子",              // 来源名（媒体名/公众号名/X账号名，不是URL）
 cat:"ai",                  // minority | ma | ipo | bigtech | ai | frontier
 regions:["中国"],          // 数组
 score:86,                  // 0-100
 title:"标题（可含数字与结论，像 AIHOT 一样信息密度要高）",
 url:"https://...",         // 具体原文链接
 sum:"2-3句中文摘要，给出关键数字（金额、估值、倍数、同比）。",
 why:"推荐理由：为什么值得读/对一级市场意味着什么。",
 tags:["AI","CAPEX"]}       // 主题标签；AI 条目须含 TOPICS 之一
```

- `date` 必填（ISO）；`t` 是展示串、`date` 是排序键。周报/综述类给个合理日期让它沉到下面
- `regions` 与 `tags` 驱动侧栏筛选，别发明新地区值；新增主题标签要同步改页面 `TOPICS` 常量
- 字符串里如有英文双引号，改用中文引号或转义，否则会破坏 JS

**score rubric**：
- 90+：改变市场格局的事件（史上最大IPO、头部模型发布、千亿级政策）
- 80-89：十亿美元级交易、总量基准数据
- 70-79：数亿美元交易、重要产品发布、监管判例
- 60-69：补充性动态
- 60 以下的不值得收录

**post 也走同一道闸门**：`build.py post` 先跑 `publish_gate()`，不过 → 非零退出且**不输出任何 post 正文**
（防止「build 失败但流程继续 → 坏内容仍被发到频道」）。

## 6. 验证

```bash
python3 scripts/pipeline.py stats --date 2026-07-25   # 五类统计
python3 scripts/build.py dry-run --date 2026-07-25    # 只校验不写文件
open index.html                                        # 本地目检（macOS）
```

最低验证标准：发布闸门通过；`JS check` 两行 OK；页面打开后侧栏计数正常、新条目出现在对应板块、点击筛选不报错。

## 7. 定时任务与 ack

- 每天 **08:10 北京时间 (UTC+8)** 触发（wall-clock reminder `#4d2c06ea`，next = `00:10Z`）
- fire 后**必须**：`raft reminder ack --id 4d2c06ea-7f01-47b6-9465-3f6f58c05f84 --revision <rev>`
  - `<rev>` 从 `raft inbox check` 输出的 `ack: raft reminder ack --id ... --revision <rev>` 一行原文抄
  - 不 ack → fired item 永远挂 inbox 显示「1 fired, not acked」→ 看 inbox 的人会误判「job 没跑」
- 本机时区 11 月起是 **PST (-0800)**；报告 UTC 用 `date -r $(date +%s) -u '+%FT%T%z'`
- 另有 `#1abcadb5`（美股 pre-market 5 picks，08:30 America/New_York）——**暂停状态**：fire 后**只 ack，不跑任何脚本**，等 owner 明确授权

## 8. 不要做

- 不要凭训练数据编造交易/新闻——每条都必须来自当天的 API 返回或搜索结果，带真实 URL
- **不要手改 `index.html` 里的 `ITEMS` 数组**——数据在 `dealhot.db`，改 DB 后重新 build；手改会被下次 build 覆盖
- **不要让抓取直写 items**——原始候选必须先落 raw_candidates，再经人工精选
- **不要用 IT桔子首页兜底当 url**——没有原文链接的条目宁可不收
- 不要改动 CSS 与页面骨架（header/aside/footer），用户没要求改设计时只动数据
- 不要把摘要写成原文翻译复制——压缩成 2–3 句并保留关键数字
- 不要丢 `url` 字段——没有出处的条目等于不可信
- 不要在 `why` 里写股票买卖意见
- 不要忘记中东、巴西的 M&A 覆盖
- 不要提交 `.secrets/`、不要把凭证写进任何会被 push 的文件
- 不要单跑 `build.py build` 跳过 `fetch`/`accept`（会漏当天条目）
- 不要漏 ack（见 §7）

## 9. 历史替换（replace）

重做历史**不要**手写 `DELETE` 再逐日 accept —— 中途失败会留下半迁移状态。用事务化的 replace：

```bash
python3 scripts/pipeline.py replace --from 2026-07-24 --to 2026-07-27 \
    --files samples/2026-07-24.json,samples/2026-07-25.json,samples/2026-07-26.json,samples/2026-07-27.json
```

- **先校验范围**：所有 payload 的 date 必须落在 `--from ~ --to` 内、日期不能重复、
  **区间内每一天都要有且仅有一个文件**（缺日期 → 拒绝；生产 runbook 禁止 `--allow-missing`）
- **再整批校验**：四天的所有条目一起过字段闸门 + raw 引用校验 + 7 日去重 + 批内去重（去重时排除被替换区间本身）
- **任一条不合格 → 整批拒绝、0 条落库、旧数据原样不动**（退出码非 0）
- **全部通过 → 单事务** `DELETE` 旧行 + `INSERT` 新行；写入异常 → 整批回滚，并打印区间行数变化

## 10. 历史重做记录

`samples/` 保存了重做样稿（精选输入 JSON + 渲染出的 archive 页面）。最近一次：

| 日期 | raw 候选 | 精选 | 板块覆盖 |
|---|---|---|---|
| 2026-07-24 | 169 | 5 | minority / ipo / frontier / bigtech / ai |
| 2026-07-25 | 55 | 5 | minority ×2 / frontier / bigtech / ai |
| 2026-07-26 | 57 | 5 | minority / frontier / ai ×2 / bigtech |
| 2026-07-27 | 189 | 5 | ipo / bigtech / ai / minority / frontier |

重做的候选全部来自 IT桔子接口按真实事件日期回溯（单日区间返回 0 条，必须用 ≥2 天窗口再按事件日过滤）。
