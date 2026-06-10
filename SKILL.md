---
name: dealhot
description: 更新/维护 DealHOT 一级市场动态聚合页（github.com/jeanajin0409-art/market-pulse 仓库的 index.html，发布于 jeanajin0409-art.github.io/market-pulse）。当用户说"更新 DealHOT"、"刷新一级市场页面"、"更新市场动态网页"、"把今天的交易新闻加到页面"、"加一条 M&A/IPO/少数股权/融资新闻"、"重新生成 market-pulse"、"今天的一级市场日报"等任何涉及这个页面的增、删、改、重建时使用。即使用户只说"更新那个网页"且上下文涉及一级市场/IPO/并购/AI投融资，也应触发本 skill。页面数据内嵌在 HTML 里，不懂结构直接改容易把页面改坏——动这个文件前必须先读本 skill。
---

# DealHOT — 一级市场与AI动态聚合页维护指南

目标文件：本仓库根目录的 `index.html`（仓库：`github.com/jeanajin0409-art/market-pulse`）
单文件静态页面，无构建步骤、无依赖。所有数据内嵌在 `<script>` 里的 `const ITEMS = [...]` 数组中，前端 JS 负责筛选与渲染。

**发布机制**：push 到 `main` 分支后，GitHub Pages 约 1 分钟内自动发布到
`https://jeanajin0409-art.github.io/market-pulse/` ——这是对外分享的正式地址。

**编辑前必须 `git pull`**（可能有其他 agent 刚推送过更新）；改完按下文验证，然后 commit + push。commit message 用一行中文概括（如"新增 6/11 三条M&A + 更新导读"）。

**远程 agent 接入**：不在仓库所有者电脑上的 agent，用仓库的 contributor 部署钥匙（私钥文件由所有者提供）克隆与推送：

```bash
# 私钥文件放到本机后（例如 ~/keys/market-pulse-contributor-key，权限 600）：
GIT_SSH_COMMAND="ssh -i ~/keys/market-pulse-contributor-key -o IdentitiesOnly=yes" \
  git clone git@github.com:jeanajin0409-art/market-pulse.git
# 之后的 pull/push 都带同样的 GIT_SSH_COMMAND，或写进仓库配置：
git config core.sshCommand "ssh -i ~/keys/market-pulse-contributor-key -o IdentitiesOnly=yes"
```

## 页面是什么

仿 aihot.virxact.com 风格的资讯卡片流，覆盖五个板块：

| cat 值 | 板块 | 内容范围 |
|---|---|---|
| `minority` | 少数股权 | 中国、美国、亚太的少数股权/PE成长投资、产业资本合资 |
| `ma` | M&A | 中国、美国、亚太，**并特别关注中东、巴西** |
| `ipo` | IPO | 全球 IPO 定价/上市/递表/募资数据，重点港股+美股 |
| `bigtech` | 大厂动态 | Apple/Google/Microsoft/Meta/Amazon/Nvidia 等重要新闻、监管 |
| `ai` | AI 动态 | 产品、模型/技术、CAPEX、ARR/商业化、投融资、政策 |

## 数据结构（ITEMS 数组元素）

```js
{t:"6月10日 09:46",          // 北京时间显示串；周报类条目可写"6月上旬"/"Q1数据"
 src:"Bloomberg",            // 来源名（人话：媒体名/公众号名/X账号名，不是URL）
 cat:"ai",                   // minority | ma | ipo | bigtech | ai（五选一）
 regions:["美国"],           // 数组，取值：中国 美国 亚太 中东 巴西 欧洲 全球（可多个）
 score:86,                   // 0-100 重要度，标准见下文
 title:"标题（可含数字与结论，像 AIHOT 一样信息密度要高）",
 url:"https://...",          // 必填，原文链接，没有可靠链接的条目不要加
 sum:"2-3句中文摘要，给出关键数字（金额、估值、倍数、同比）。",
 why:"推荐理由（可选）：为什么值得读/对一级市场意味着什么。",
 tags:["AI","CAPEX"]}        // 主题标签；AI 条目须含 TOPICS 之一：产品、模型/技术、CAPEX、ARR/商业化、投融资、政策
```

字段注意：
- 字符串里如有英文双引号，改用中文引号或转义，否则会破坏 JS。
- `regions` 与 `tags` 驱动侧栏筛选，别发明新地区值；新主题标签可以加，但 AI 主题筛选 chips 取自 `TOPICS` 常量，新增主题需同步改 `TOPICS`。
- `publishedAt` 类的 ISO 时间不要出现，`t` 直接写人话北京时间。

## score 打分标准

- 90+：改变市场格局的事件（史上最大IPO、头部模型发布、千亿级政策）
- 80-89：十亿美元级交易、总量基准数据（季度M&A统计、CAPEX指引）
- 70-79：数亿美元交易、重要产品发布、监管判例
- 60-69：补充性动态
- 60 以下的不值得收录

## 更新工作流

### 1. 拉 AI 动态（AIHOT 公开API）

必须带浏览器 UA，否则 403：

```bash
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 aihot-skill/0.2.0"
since=$(date -u -v-24H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ)
curl -sH "User-Agent: $UA" "https://aihot.virxact.com/api/public/items?mode=selected&since=$since&take=60"
```

返回字段 `title/source/publishedAt/summary/category/score/url`。挑 score≥65 且与一级市场视角相关的条目（资本开支、融资、ARR、重要模型/产品、政策），改写为上面的 ITEMS 格式。`publishedAt`（UTC）转北京时间 = +8 小时。若用户装有 aihot skill，其文档有完整 API 说明。

### 1.5 IT桔子 OpenAPI（中国少数股权deal + 并购收购 + 重要新闻）

中国一级市场的结构化数据源，已实测可用。完整文档：`itjuzi.com/shujuapi/V2BaseComponents/explain/explain.html`

**凭证**：在仓库所有者本机 `.secrets/itjuzi.env`（含 ITJUZI_APPID / ITJUZI_APPKEY）。该目录被 .gitignore 排除——**绝不能把凭证提交进这个公开仓库或写进任何会推送的文件**。远程 agent 需要所有者另行提供一份 env 文件，同样放本机 git 之外。

**鉴权**（token 有效期 1 小时，每次更新流程开头取一次即可）：

```bash
source .secrets/itjuzi.env
TOKEN=$(curl -s -X POST "https://openapi.itjuzi.com/oauth2.0/get_access_token" \
  -d "appid=$ITJUZI_APPID&appkey=$ITJUZI_APPKEY&granttype=client_credentials" | jq -r '.data.access_token')
# 之后所有请求带头（注意 AUTHORIZATION 全大写、Bearer 后有空格）：
# -H "AUTHORIZATION: Bearer $TOKEN"
```

**接口与板块对应**（GET/POST 均可；`date_pattern=3` 按事件发生时间、快讯用 `1` 按发布时间；日期格式 `2026-06-10`；返回统一为 `{code:1000,info:"Success",data:[...]}`，中文是 \u 转义，用 jq 取字段自动解码）：

| 端点 | 内容 | 喂哪个板块 |
|---|---|---|
| `/investevent/get_investevent_list_v2` | 投资事件 | minority（中国少数股权） |
| `/merger/get_acquisition_list_v2` | 收购事件 | ma（中国） |
| `/merger/get_merger_list_v2` | 合并事件 | ma（中国） |
| `/news/get_spider_news_list_v2` | 快讯 | 各板块补充、大厂动态 |

```bash
Y=$(date -v-1d +%Y-%m-%d 2>/dev/null || date -d yesterday +%Y-%m-%d); T=$(date +%Y-%m-%d)
# 最近24小时投资事件（按融资时间倒序）
curl -s "https://openapi.itjuzi.com/investevent/get_investevent_list_v2?date_pattern=3&date_start=$Y&date_end=$T&limit=50&order=1&order_rules=1" -H "AUTHORIZATION: Bearer $TOKEN"
# 最近24小时收购事件
curl -s "https://openapi.itjuzi.com/merger/get_acquisition_list_v2?date_pattern=3&date_start=$Y&date_end=$T&limit=50" -H "AUTHORIZATION: Bearer $TOKEN"
# 最近24小时快讯（可加 keywords=xxx 按标题搜）
curl -s "https://openapi.itjuzi.com/news/get_spider_news_list_v2?date_pattern=1&date_start=$Y&date_end=$T&limit=100" -H "AUTHORIZATION: Bearer $TOKEN"
```

**关键返回字段**：
- 投资事件：`event_title`、`event_des`、`round_name`（轮次）、`detail_amount`（**单位：万**）、`currency_name`、`leading_investor`/`following_investor`（投资方）、`com_name`/`com_industry_name`、`invest_year/month/day`、`news[]`（关联报道，含可用的原文链接）
- 收购：另有 `purchaser[]`（收购方）、`equity_ratio`（股比）、`purchase_year/month/day`
- 快讯：`news_title`、`news_time`、`news_source`、`news_url`、`related_party[]`（关联主体）

**入选门槛**（API 一天返回上百条，页面只收重要的）：
- 投资事件：金额 ≥1 亿元人民币（`detail_amount`≥10000，单位是万）、或知名机构领投、或 AI/硬科技明星公司；轮次为战略投资/PE/老股转让的优先归 minority，AI 公司大额融资可归 ai+投融资
- 收购/合并：金额 ≥5 亿元、或买卖任一方知名
- 快讯：只挑 `related_party` 含知名公司/独角兽的
- 条目 `url` 优先用投资事件 `news[]` 里的报道链接或快讯 `news_url`；实在没有外部链接时用 `https://www.itjuzi.com/investevent` 兜底、`src` 写"IT桔子"

**配额纪律**：限流 1800 秒/200 次，且总调用次数有限额。每日更新的标准用量 = 1 次 token + 3 次业务调用，足够；**不要翻页轰炸、不要逐条查事件详情**。

### 1.6 从 X (Twitter) 扒一级市场 / AI 信号

不用 Twitter API，用 Claude 内置 **WebSearch** 工具，`allowed_domains=["x.com"]` + 查询 `from:<handle> <关键词>` 即可拉到某账号的相关推文（已实测 SemiAnalysis_、rohanpaul_ai 均通）。

**⚠️ 最大坑：WebSearch 的时间窗不可靠**，结果里会混进几个月前的旧推（甚至去年的 status）。所以**每条都必须核对日期**——从推文正文或结果里判断发布时间，只保留最近约 3 天的；拿不准日期的宁可丢。这是用 WebSearch 扒 X 与用正经 API 的本质区别，别跳过。

**watchlist 在单独文件 `watchlist.md`**（按板块分组、含每个 handle 喂哪个板块）。每日更新时读它、按板块挑账号扫，不要一次全扫。该文件维护着核心 track 名单（一级市场 deal 记者、VC、AI 分析）+ 候选补充。要扩名单：先用本节方法实测某 handle 能拉到内容、标 ✓ 再加。

补充几个 watchlist.md 之外也好用的：`kimmonismus`（AI快讯）、`AYi_AInotes` `berryxia` `shao__meng`（中文AI）、大厂官方 `OpenAI` `OpenAIDevs` `AnthropicAI` `GoogleDeepMind`、中东 deal `WestAsiaWatch` ✓。

**两种查询方式**：
- `from:<handle>` 只对**固有性强的 handle 有效**（SemiAnalysis_、rohanpaul_ai 实测准）；像 `from:business`（Bloomberg）这种通用词 handle，`from:` 会失效、返回一堆杂账号——这类不要用 from:。
- **无 from: 的主题搜索**同样能挖 deal：如 `M&A Middle East stake billion acquire`、`私募 战略投资 亿元`，配 `x.com` domain，实测能拉到中东/全球并购推文。一级市场 deal 发掘优先用这种。

**查询示例**：
- `from:rohanpaul_ai funding OR raise OR valuation billion`、`from:SemiAnalysis_ HBM OR capex OR Blackwell`
- 主题式：`AI startup raises billion valuation`、`Middle East sovereign fund acquire stake`
- ✓ 留：含数字（$Xbn、+47%、估值/轮次/市占）、且与一级市场或某板块主题相关
- ✗ 丢：纯观点/meme/转推无原创、日期核对不在最近3天、与所有板块都无关

**转成 ITEMS**：`src` 写 `X：<显示名>`（与现有页面 X 条目一致），`url` 用推文链接，`sum` 提炼含数字的要点，按内容归到对应 `cat`。AIHOT 的 API 已经覆盖很多 X 热门内容（见 1.1），**X 扒取是补充**——优先填 AIHOT/IT桔子没覆盖的一级市场 deal 与大额融资，避免与 AIHOT 条目重复。

### 2. 搜一级市场新闻（web search）

按板块搜索，时间限定到最近 1-3 天，示例查询：
- 少数股权：`minority stake investment private equity deal <本月> China US Asia`
- M&A：`M&A deals announced <本周> Middle East Brazil acquisition`（中东、巴西必须覆盖）
- IPO：`IPO <本周> Hong Kong listing Nasdaq pricing debut` + 中文搜 `港股IPO 招股 本周`
- 大厂：`big tech news <日期> Apple Google Microsoft Meta Nvidia`
- AI融资：`AI startup funding round announced this week raises valuation`（Crunchbase 周榜是好来源）

只收有具体出处 URL 的条目；金额、估值数字要在摘要里保留。

### 3. 编辑文件

只动三处，**不要重写整个文件**（设计样式已定稿，除非用户要求改设计）：
1. `ITEMS` 数组——增删条目。同板块内按重要度**大致**降序、相近主题可聚在一起，不要求严格按 score 排。过时条目（超过约一周且无背景价值）删除，有判例/基准价值的旧条目可保留并在 `t` 标注"（背景）"。
2. `.nav .date` 里的日期串（如 `2026年6月10日 · 星期三`）。
3. `今日导读` 的 `<p>`——用一句话串起当天 4-5 个最大事件（金额+事件名）。

**场景区分**：每日全量更新时三处都要动；用户只让加/删个别条目时，只动 ITEMS 即可——除非新条目够格进当天 top 4-5 事件，否则导读和日期不用碰。

### 4. 验证

改完用浏览器或无头方式确认 JS 没坏（页面空白=ITEMS 数组语法错误）：

```bash
# 快速语法检查：抽出 ITEMS 数组（到 let state 为止，去掉最后一行）让 node 解析
node -e "$(sed -n '/^const ITEMS/,/^let state/p' index.html | sed '\$d'); console.log('ITEMS ok:', ITEMS.length)"
# 然后浏览器目检（macOS 用 open；Linux 环境跳过，靠语法检查 + push 后看线上页面）
open index.html
```

最低验证标准：页面打开后侧栏计数正常、新条目出现在对应板块、点击筛选不报错（开发者工具 Console 无红字）。

## 不要做

- 不要凭训练数据编造交易/新闻——每条都必须来自当天的 API 返回或搜索结果，带真实 URL。
- 不要改动 CSS 与页面骨架（header/aside/footer），用户没要求改设计时只动数据。
- 不要把摘要写成原文翻译复制——压缩成 2-3 句并保留关键数字。
- 不要丢 `url` 字段——没有出处的条目等于不可信，宁可不收。
- 不要忘记中东、巴西的 M&A 覆盖——这是用户明确点名的需求。
