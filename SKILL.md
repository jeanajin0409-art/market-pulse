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

### 1.5 IT桔子 OpenAPI（少数股权deal + 重要新闻，接入中）

数据源 `openapi.itjuzi.com`，用于拉取中国一级市场投融资事件（少数股权）与重要新闻。
凭证在仓库所有者本机 `.secrets/itjuzi.env`（含 ITJUZI_APPID / ITJUZI_APPKEY），**该目录已被 .gitignore 排除——绝不能把 appid/appkey 提交进这个公开仓库，也不要写进任何会推送的文件**。
具体端点与鉴权方式待官方接口文档确认后补充到本节；文档到位前此数据源不可用，少数股权板块继续用 web search。

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
