# x_scan — DealHOT 的 X(Twitter) 抓取

用 [twikit](https://github.com/d60/twikit) 登录一个 **小号**，拉 watchlist 账号最近推文，按日期+deal信号筛选，输出候选给维护者人工 review 后加入页面。解决 WebSearch 时间窗不准（混入旧推）的问题——登录态能拿到真实 `created_at`，精确筛"近3天"。

## 环境（一次性）

```bash
source /opt/anaconda3/etc/profile.d/conda.sh
conda activate dealhot-x        # Python 3.12 + twikit 2.3.3，已建好
```

## 凭证（一次性，需用户提供小号）

在 `../.secrets/x_account.env` 写入（该目录已 .gitignore，绝不进公开仓库）：

```
XUSER=小号用户名(不带@)
XEMAIL=小号注册邮箱
XPASS=小号密码
```

首次运行会登录并把会话存到 `../.secrets/x_cookies.json`，之后复用、不再反复登录（反复登录易触发风控）。

## 跑

```bash
python scan.py --group deals --days 3      # 一级市场 deal 记者(danprimack等)
python scan.py --group ai --days 2         # AI 分析
python scan.py --group all --days 2        # 全部(慢，账号多)
```

输出：终端可读列表 + `out/x_scan_<group>_<date>.json` 审计文件。维护者挑命中条目，按 `../SKILL.md` 数据结构改写成 ITEMS 入页面。

## ⚠️ 风险（务必知道）

- **违反 X ToS**，小号可能被限流/封禁——**只用小号，永不用主号**。
- twikit 依赖 X 内部 API，X 改版会失效；twikit 维护活跃，失效时 `pip install -U twikit`。
- 礼貌使用：脚本每账号间隔 2s、分组扫而非一次全扫，降低风控概率。
- `--group all` 一次 23 个账号，建议分批（deals/ai 各一次）而不是天天全量。

## 与 watchlist 同步

`scan.py` 里的 `HANDLES` 三组需与 `../watchlist.md` 手动保持一致。加号删号时两处都改。
