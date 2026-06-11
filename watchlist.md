# DealHOT X/Twitter 每日 track 名单

每日扫这些账号，用 WebSearch + `from:<handle>` 拉最近约 3 天、含数字/deal 的推文，转成 ITEMS 喂对应板块。手法与坑（时间窗不可靠、from: 只对固有名 handle 有效）见 `SKILL.md` 第 1.6 节。`✓` = 已实测能拉到内容。

## A. 一级市场 deal 速报（最高优先 → minority / ma / ipo）

这类账号的推文本身就是 deal，DealHOT 核心来源。

| handle | 名称 | 喂板块 | 备注 |
|---|---|---|---|
| `danprimack` ✓ | Dan Primack · Axios Pro Rata | ma / minority / ipo | deal/并购/PE 速报决定版，几乎每条带金额 |
| `newcomer` | Eric Newcomer | minority / ai | VC 与 AI 大额融资 scoop |
| `crunchbase` | Crunchbase | minority | 融资轮次数据，金额齐 |
| `HarryStebbings` ✓ | Harry Stebbings · 20VC | minority / ipo | 大型 round、IPO 速评 |

## B. VC / 投资人（thesis + deal → minority / ai）

| handle | 名称 | 喂板块 | 备注 |
|---|---|---|---|
| `a16z` | Andreessen Horowitz（机构） | ai / minority | 官方 thesis、market map |
| `ycombinator` | Y Combinator（机构） | ai / minority | batch、创业趋势 |
| `benchmark` | Benchmark（机构） | minority | 投资动态 |
| `garrytan` | Garry Tan · YC CEO | ai / minority | YC 动态、创业观点 |
| `bgurley` ✓ | Bill Gurley · Benchmark | ipo / minority | 估值/IPO 定价批评，含数字 |
| `eladgil` | Elad Gil | ai / minority | AI scaling、天使投资 |
| `venturetwins` ✓ | Justine Moore · a16z consumer | ai | 消费 AI、market map |
| `dan_rasmussen` | Daniel Rasmussen · Verdad | minority / ipo | PE/量化、逆向估值观点 |
| `OrlandoBravo` | Orlando Bravo · Thoma Bravo | ma | 软件 PE 并购（发推低频，备用） |

## C. AI / 科技分析（→ ai / bigtech）

| handle | 名称 | 喂板块 | 备注 |
|---|---|---|---|
| `SemiAnalysis_` ✓ | SemiAnalysis | ai / bigtech | 半导体/HBM/CAPEX 深度，数字密 |
| `rohanpaul_ai` ✓ | Rohan Paul | ai / minority | AI 新闻+融资聚合，高频 |
| `benthompson` | Ben Thompson · Stratechery | bigtech | 大厂战略分析 |
| `karpathy` | Andrej Karpathy | ai | AI 技术/模型观点 |
| `lexfridman` | Lex Fridman | ai | AI 访谈（信号稀，备用） |
| `nathanbenaich` | Nathan Benaich · Air Street | ai / minority | AI 投资数据、State of AI 年报作者 |
| `martin_casado` | Martin Casado · a16z | ai / bigtech | AI 基础设施/算力投资视角 |

## D. 机构 / 媒体（→ 各板块 scoop 源）

| handle | 名称 | 喂板块 | 备注 |
|---|---|---|---|
| `TheInformation` | The Information | 各板块 | 一级市场/AI 独家 scoop 源头，常被二手转引 |
| `pitchbook` | PitchBook（机构） | minority / ma | PE/VC 交易数据库官方 |
| `saranormous` | Sarah Guo · Conviction | minority / ai | AI 原生基金，早期 AI deal 嗅觉 |
| `ttunguz` | Tomasz Tunguz · Theory | minority | SaaS/VC 数据与图表，量化感强 |
| `packyM` | Packy McCormick · Not Boring | ai / minority | 公司/赛道 deep dive |

> 2026-06-10：上述 7 个候选已全部并入正式 track 名单（用户确认）。

## E. 前沿科技（→ frontier）—— 用 `--group frontier --loose`

具身智能/机器人、太空、核聚变、量子、国防。多为公司/创始人账号，不一定带金额，用 `--loose`。

| handle | 名称 | 喂板块 | 备注 |
|---|---|---|---|
| `adcock_brett` ✓ | Brett Adcock · Figure CEO | frontier | 人形机器人量产进度、BotQ 工厂数据 |
| `DrJimFan` | Jim Fan · NVIDIA | frontier / ai | 具身智能/机器人研究风向 |
| `anduriltech` ✓ | Anduril（机构） | frontier | 国防科技、自主系统 |
| `CFS_energy` ✓ | Commonwealth Fusion | frontier | 核聚变 ARC 电厂进展 |
| `QuantinuumQC` ✓ | Quantinuum（机构） | frontier | 离子阱量子，已上市 QNT |

## F. 大厂官方（→ ai / bigtech）—— 用 `--group official --loose`

产品/模型发布第一手。多为公告体，用 `--loose`。

| handle | 名称 | 喂板块 | 备注 |
|---|---|---|---|
| `OpenAI` ✓ | OpenAI（机构） | ai / bigtech | 产品/模型发布 |
| `OpenAIDevs` ✓ | OpenAI Developers | ai | API/开发者动态 |
| `AnthropicAI` ✓ | Anthropic（机构） | ai | 模型/研究/政策 |
| `GoogleDeepMind` ✓ | Google DeepMind | ai | 模型/研究 |

## G. 中文 AI（→ ai / minority）—— 用 `--group cn --loose`

| handle | 名称 | 喂板块 | 备注 |
|---|---|---|---|
| `AYi_AInotes` ✓ | AI 笔记 | ai | 中文 AI 快讯 |
| `berryxia` ✓ | Berry Xia | ai | 中文 AI 产品/行情解读 |
| `shao__meng` ✓ | 邵猛 | ai | 中文 AI 工具/模型动态 |

## H. 中东 / 主权基金（→ ma / minority）—— 用 `--group mena`

| handle | 名称 | 喂板块 | 备注 |
|---|---|---|---|
| `WestAsiaWatch` ✓ | West Asia Watch | ma / minority | 中东 deal/主权基金（粉丝少但对口） |

## 其他已并入 deals/vc/ai 组的补充账号

`axios`、`jason`(Jason Calacanis)、`TrungTPhan`（deals 组）；`saranormous`、`dan_rasmussen`、`GavinSBaker`（vc 组）；`kimmonismus`、`swyx`、`alexandr_wang`、`_jasonwei`、`emollick`、`demishassabis`、`Kanjun`（ai 组）。

---

> **认证方式（2026-06-11 更新）**：scan.py 已改为 **cookie 直连 X GraphQL**，不再用 twikit 密码登录（twikit 已与 X 新版不兼容）。cookie 从 Chrome Profile 2 导出，详见 SKILL.md 1.6。
> **2026-06-11**：用户要求每日刷新固定覆盖 前沿科技 + AI动态 + X观点(cmt)；新增 E/F/G/H 四组共约 17 个账号（已实测 resolve）。
