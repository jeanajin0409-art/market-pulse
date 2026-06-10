#!/usr/bin/env python3
"""
DealHOT X (Twitter) 抓取脚本 — 用 twikit 登录一个小号，拉 watchlist 账号最近的推文，
按日期(默认近3天)+ 含数字/deal关键词筛选，输出候选给维护者人工 review 后入页面。

用法（先 conda activate dealhot-x）：
    python scan.py --group deals --days 3
    python scan.py --group all --days 2 --per 15
group: deals | vc | ai | all

凭证：从 ../.secrets/x_account.env 读 XUSER/XEMAIL/XPASS（首次登录），
会话存到 ../.secrets/x_cookies.json（之后复用，避免反复登录触发风控）。
两者都在 .gitignore 内，绝不进公开仓库。

⚠️ 这是登录 X 内部 API 的抓取，违反 X ToS、账号可能被限流/封禁：只用小号，别用主号。
"""
import argparse, asyncio, json, os, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from twikit import Client

HERE = Path(__file__).resolve().parent
SECRETS = HERE.parent / ".secrets"
ENV = SECRETS / "x_account.env"
COOKIES = SECRETS / "x_cookies.json"
OUT = HERE / "out"

# 与 ../watchlist.md 保持同步（手动）。分组决定一次扫哪批，避免一次全扫触发风控。
HANDLES = {
    "deals": ["danprimack", "newcomer", "crunchbase", "HarryStebbings", "TheInformation", "pitchbook"],
    "vc":    ["a16z", "ycombinator", "benchmark", "garrytan", "bgurley", "eladgil",
              "venturetwins", "dan_rasmussen", "saranormous", "ttunguz", "packyM"],
    "ai":    ["SemiAnalysis_", "rohanpaul_ai", "benthompson", "karpathy",
              "nathanbenaich", "martin_casado"],
}

# 含数字 / deal 信号才留（推文太多，只要有信息量的）
KW = re.compile(r"(raise|raised|raising|funding|round|valuation|stake|acquir|merger|"
                r"IPO|invest|backed|led by|seed|series\s+[a-f]|\$[\d.]|\bbn\b|\bbillion\b|"
                r"\bmillion\b|融资|领投|收购|并购|估值|轮)", re.I)
NUM = re.compile(r"\d")


def load_creds():
    if not ENV.exists():
        sys.exit(f"缺少凭证文件 {ENV}\n  请创建并填入小号：\n"
                 f"  XUSER=小号用户名(不带@)\n  XEMAIL=小号邮箱\n  XPASS=小号密码\n")
    d = {}
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    return d


async def get_client():
    client = Client("en-US")
    if COOKIES.exists():
        client.load_cookies(str(COOKIES))
        return client
    c = load_creds()
    await client.login(auth_info_1=c["XUSER"], auth_info_2=c.get("XEMAIL", ""),
                       password=c["XPASS"])
    client.save_cookies(str(COOKIES))
    print(f"  已登录并保存会话 → {COOKIES.name}", file=sys.stderr)
    return client


def tweet_dt(tw):
    for attr in ("created_at_datetime",):
        v = getattr(tw, attr, None)
        if isinstance(v, datetime):
            return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    raw = getattr(tw, "created_at", None)  # e.g. "Wed Jun 10 01:46:00 +0000 2026"
    if raw:
        try:
            return datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
        except Exception:
            pass
    return None


async def scan(group, days, per):
    handles = sorted(set(sum(HANDLES.values(), []))) if group == "all" else HANDLES.get(group)
    if not handles:
        sys.exit(f"未知 group '{group}'，可选：deals | vc | ai | all")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    client = await get_client()
    hits, queried, skipped = [], [], {}
    for h in handles:
        try:
            user = await client.get_user_by_screen_name(h)
            tweets = await client.get_user_tweets(user.id, "Tweets", count=per)
            queried.append(h)
        except Exception as e:
            skipped[h] = str(e)[:80]
            print(f"  ✗ {h}: {str(e)[:80]}", file=sys.stderr)
            await asyncio.sleep(3)
            continue
        kept = 0
        for tw in tweets:
            dt = tweet_dt(tw)
            text = (getattr(tw, "text", "") or "").replace("\n", " ")
            if dt and dt < cutoff:
                continue                       # 太旧，丢（解决 WebSearch 的时间窗问题）
            if not (NUM.search(text) and KW.search(text)):
                continue                       # 没数字/没deal信号，丢
            hits.append({
                "handle": h,
                "date": dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M") if dt else "?",
                "text": text[:400],
                "url": f"https://x.com/{h}/status/{tw.id}",
                "likes": getattr(tw, "favorite_count", None),
            })
            kept += 1
        print(f"  ✓ {h}: 命中 {kept}", file=sys.stderr)
        await asyncio.sleep(2)                  # 礼貌间隔，降风控

    hits.sort(key=lambda x: x["date"], reverse=True)
    OUT.mkdir(exist_ok=True)
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    out_file = OUT / f"x_scan_{group}_{stamp}.json"
    out_file.write_text(json.dumps({
        "_meta": {"group": group, "days": days, "generated_at": datetime.now().isoformat(),
                  "queried": queried, "skipped": skipped, "hits": len(hits)},
        "tweets": hits,
    }, ensure_ascii=False, indent=2))

    # 给维护者看的人话输出（stdout）
    print(f"\n=== X 扫描结果 group={group} 近{days}天 命中{len(hits)}条 ===")
    for t in hits:
        print(f"\n[{t['date']}] @{t['handle']}  ❤{t['likes']}")
        print(f"  {t['text']}")
        print(f"  {t['url']}")
    print(f"\naudit → {out_file}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="deals", help="deals | vc | ai | all")
    ap.add_argument("--days", type=int, default=3, help="只保留最近 N 天（默认3）")
    ap.add_argument("--per", type=int, default=15, help="每个账号取最近多少条（默认15）")
    a = ap.parse_args()
    asyncio.run(scan(a.group, a.days, a.per))
