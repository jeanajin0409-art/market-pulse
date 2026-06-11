# x_scan — DealHOT 的 X(Twitter) 抓取

拉 watchlist 账号最近推文，按日期+deal信号筛选，输出候选给维护者人工 review 后加入页面。解决 WebSearch 时间窗不准（混入旧推）的问题——登录态能拿到真实 `created_at`，精确筛"近 N 天"。

> **认证方式（2026-06-11 起）**：改为 **cookie 直连 X GraphQL**，不再用 twikit 密码登录。
> 原因：twikit 2.3.3 的 `client_transaction.init()`（`get_indices`）已与 X 当前 JS bundle 不兼容、必坏；`search_tweet` 端点对小号也返回 404。脚本因此绕开 twikit，纯 httpx 直接调 `UserByScreenName`→`UserTweets`，GraphQL operation IDs 启动时从 X `main.*.js` 自动刷新。

## 环境（一次性）

```bash
source /opt/anaconda3/etc/profile.d/conda.sh
conda activate dealhot-x        # Python 3.12，已建好
```

## 凭证（cookie，需用户在浏览器登录小号）

1. 用户在 **Chrome Profile 2** 登录 `https://x.com`（小号，不是主号）。
2. 导出 cookie 到 `../.secrets/x_cookies.json`（该目录 .gitignore，绝不进公开仓库）：

```bash
python ../.secrets/export_cookies.py chrome
```

脚本会从 Chrome 各 profile 找到含 `auth_token`+`ct0` 的那个并导出。cookie 失效（一般几周）时，让用户在那个 profile 重新登录 x.com 再导一次即可。

## 跑

```bash
python scan.py --group deals --days 3              # 一级市场 deal 记者 → minority/ma/ipo
python scan.py --group ai --days 2 --loose         # AI 分析+大佬观点(cmt) → ai
python scan.py --group frontier --days 7 --loose   # 前沿科技 → frontier
python scan.py --group official --days 5 --loose   # 大厂官方 → ai/bigtech
python scan.py --group cn --days 4 --loose         # 中文 AI → ai/minority
python scan.py --group mena --days 4               # 中东/主权基金 → ma/minority
python scan.py --group all --days 2                # 全部(慢，账号多)
```

`--loose`：放宽筛选（不要求含数字/deal词，只去掉 <40 字的短推/转推），用于 harvest 观点(cmt)、前沿科技、官方动态这类不一定带金额的内容。deals/mena 这类纯 deal 源用默认严格筛即可。loose 会混进生活/体育闲聊，**人工挑**。

输出：终端可读列表 + `out/x_scan_<group>_<date>.json` 审计文件。维护者挑命中条目，按 `../SKILL.md` 数据结构改写成 ITEMS 入页面。

## ⚠️ 风险（务必知道）

- **违反 X ToS**，小号可能被限流/封禁——**只用小号，永不用主号**。
- 纯内部 API，X 改版可能失效；operation IDs 会自动刷新，但若 X 大改 GraphQL schema 仍需手动修 `scan.py`。
- 礼貌使用：脚本每账号间隔 2s、分组扫而非一次全扫，降低风控概率。

## 与 watchlist 同步

`scan.py` 里的 `HANDLES`（deals/vc/ai/official/frontier/cn/mena 七组）需与 `../watchlist.md` 手动保持一致。加号删号时两处都改。
