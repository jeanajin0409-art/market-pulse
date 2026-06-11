#!/usr/bin/env python3
"""
DealHOT X 抓取脚本 — 直接调用 X GraphQL API（cookie 认证），抓 watchlist 账号最近推文。
不依赖 twikit，operation IDs 自动从 X 当前 JS bundle 刷新。

用法（先 conda activate dealhot-x）：
    python scan.py --group deals --days 3
    python scan.py --group all --days 2 --per 15
group: deals | vc | ai | all

凭证：从 ../.secrets/x_cookies.json 读（由 export_cookies.py 生成）。
文件在 .gitignore 内，绝不进公开仓库。

⚠️ 使用 X 内部 API，违反 X ToS。只用小号，别用主号。
"""
import argparse, asyncio, httpx, json, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SECRETS = HERE.parent / ".secrets"
COOKIES = SECRETS / "x_cookies.json"
OUT = HERE / "out"

HANDLES = {
    # —— 一级市场 deal 速报 → minority / ma / ipo ——
    "deals": ["danprimack", "newcomer", "crunchbase", "HarryStebbings",
              "TheInformation", "pitchbook", "axios", "jason", "TrungTPhan"],
    # —— VC / 投资人 thesis + deal → minority / ai ——
    "vc":    ["a16z", "ycombinator", "benchmark", "garrytan", "bgurley", "eladgil",
              "venturetwins", "ttunguz", "packyM", "saranormous", "dan_rasmussen",
              "GavinSBaker"],
    # —— AI 分析 / 快讯 / 大佬观点（cmt 主力）→ ai / bigtech ——
    "ai":    ["SemiAnalysis_", "rohanpaul_ai", "benthompson", "karpathy",
              "nathanbenaich", "martin_casado", "sama", "ylecun", "kimmonismus",
              "swyx", "alexandr_wang", "_jasonwei", "emollick", "demishassabis", "Kanjun"],
    # —— AI 大厂官方（产品/模型发布）→ ai / bigtech ——
    "official": ["OpenAI", "OpenAIDevs", "AnthropicAI", "GoogleDeepMind"],
    # —— 前沿科技：机器人/具身/太空/核聚变/量子/国防 → frontier ——
    "frontier": ["DrJimFan", "adcock_brett", "anduriltech", "CFS_energy", "QuantinuumQC"],
    # —— 中文 AI / 一级市场 → ai / minority ——
    "cn":    ["AYi_AInotes", "berryxia", "shao__meng"],
    # —— 中东 / 主权基金 deal → ma / minority ——
    "mena":  ["WestAsiaWatch"],
}

# 含数字/deal 信号才保留
KW = re.compile(
    r"(raise|raised|raising|funding|round|valuation|stake|acquir|merger|"
    r"IPO|invest|backed|led by|seed|series\s+[a-f]|\$[\d.]|\bbn\b|\bbillion\b|"
    r"\bmillion\b|融资|领投|收购|并购|估值|轮)", re.I)
NUM = re.compile(r"\d")

# Bearer token（X 公开的 web app token，无需申请 dev 账号）
BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

# 已知的 GraphQL operation IDs（定期会变，脚本启动时自动刷新）
_OP_IDS = {
    "UserByScreenName": "681MIj51w00Aj6dY0GXnHw",
    "UserTweets":       "fVhuOkcsO6w1T0nmCAo_sw",
}


def _build_headers(ct0: str) -> dict:
    return {
        "authorization": f"Bearer {BEARER}",
        "x-csrf-token": ct0,
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "x-twitter-active-user": "yes",
        "x-twitter-auth-type": "OAuth2Session",
        "referer": "https://x.com",
    }


async def refresh_op_ids() -> None:
    """从 X 当前 main.js 刷新 GraphQL operation IDs，若失败则用硬编码默认值。"""
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            resp = await c.get("https://x.com", headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
            js_urls = re.findall(r'src="(https://abs\.twimg\.com/[^"]+main\.[^"]+\.js)"', resp.text)
            if not js_urls:
                return
            js = await c.get(js_urls[0], headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            for op_name in _OP_IDS:
                m = re.search(r'queryId:"([A-Za-z0-9_-]{20,})",operationName:"' + op_name + '"', js.text)
                if m:
                    _OP_IDS[op_name] = m.group(1)
        print(f"  GraphQL IDs: {_OP_IDS}", file=sys.stderr)
    except Exception as e:
        print(f"  ⚠ 刷新 op IDs 失败，用默认值: {e}", file=sys.stderr)


def load_cookies() -> dict:
    if not COOKIES.exists():
        sys.exit(f"缺少 {COOKIES}，请先运行：python .secrets/export_cookies.py chrome")
    data = json.loads(COOKIES.read_text())
    if not data.get("auth_token"):
        sys.exit(f"{COOKIES} 里没有 auth_token，请重新运行 export_cookies.py")
    return data


async def get_user_id(client: httpx.AsyncClient, screen_name: str, headers: dict) -> str | None:
    features = {
        "responsive_web_graphql_exclude_directive_enabled": True,
        "verified_phone_label_enabled": False,
        "responsive_web_graphql_timeline_navigation_enabled": True,
    }
    resp = await client.get(
        f"https://x.com/i/api/graphql/{_OP_IDS['UserByScreenName']}/UserByScreenName",
        params={"variables": json.dumps({"screen_name": screen_name, "withSafetyModeUserFields": True}),
                "features": json.dumps(features)},
        headers=headers,
    )
    result = resp.json().get("data", {}).get("user", {}).get("result", {})
    if result.get("__typename") == "UserUnavailable":
        return None
    return result.get("rest_id")


async def get_tweets(client: httpx.AsyncClient, user_id: str, headers: dict, count: int = 15) -> list[dict]:
    variables = {
        "userId": user_id,
        "count": count,
        "includePromotedContent": False,
        "withQuickPromoteEligibilityTweetFields": True,
        "withVoice": True,
        "withV2Timeline": True,
    }
    features = {
        "responsive_web_graphql_exclude_directive_enabled": True,
        "verified_phone_label_enabled": False,
        "responsive_web_graphql_timeline_navigation_enabled": True,
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "longform_notetweets_consumption_enabled": True,
        "tweet_awards_web_tipping_enabled": False,
    }
    resp = await client.get(
        f"https://x.com/i/api/graphql/{_OP_IDS['UserTweets']}/UserTweets",
        params={"variables": json.dumps(variables), "features": json.dumps(features)},
        headers=headers,
    )
    user_result = resp.json().get("data", {}).get("user", {}).get("result", {})
    # 兼容 timeline 和 timeline_v2 两种路径
    tl = user_result.get("timeline_v2") or user_result.get("timeline") or {}
    instructions = tl.get("timeline", {}).get("instructions", [])
    tweets = []
    for inst in instructions:
        for entry in inst.get("entries", []):
            item = entry.get("content", {}).get("itemContent", {})
            tweet_result = item.get("tweet_results", {}).get("result", {})
            legacy = tweet_result.get("legacy", {})
            text = legacy.get("full_text", "")
            created_at = legacy.get("created_at", "")
            tweet_id = legacy.get("id_str", "")
            likes = legacy.get("favorite_count", 0)
            if text and created_at and tweet_id:
                tweets.append({
                    "text": text,
                    "created_at": created_at,
                    "id": tweet_id,
                    "likes": likes,
                })
    return tweets


def parse_tweet_dt(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
    except Exception:
        return None


async def scan(group: str, days: int, per: int, loose: bool = False) -> None:
    handles = sorted(set(sum(HANDLES.values(), []))) if group == "all" else HANDLES.get(group)
    if not handles:
        sys.exit(f"未知 group '{group}'，可选：{' | '.join(HANDLES)} | all")

    await refresh_op_ids()

    cookies = load_cookies()
    headers = _build_headers(cookies["ct0"])
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    hits, queried, skipped = [], [], {}

    async with httpx.AsyncClient(cookies=cookies, timeout=15) as client:
        for h in handles:
            try:
                user_id = await get_user_id(client, h, headers)
                if not user_id:
                    skipped[h] = "UserUnavailable"
                    print(f"  ✗ {h}: 账号不可访问", file=sys.stderr)
                    continue

                tweets_raw = await get_tweets(client, user_id, headers, count=per)
                queried.append(h)
            except Exception as e:
                skipped[h] = str(e)[:80]
                print(f"  ✗ {h}: {str(e)[:80]}", file=sys.stderr)
                await asyncio.sleep(3)
                continue

            kept = 0
            for tw in tweets_raw:
                if tw["text"].startswith("RT @"):
                    continue
                dt = parse_tweet_dt(tw["created_at"])
                if dt and dt < cutoff:
                    continue
                text = tw["text"].replace("\n", " ")
                # 默认：需含数字+deal 信号；--loose：放宽给 cmt/观点 harvest（只要不是纯短转推）
                if loose:
                    if len(text) < 40:
                        continue
                elif not (NUM.search(text) and KW.search(text)):
                    continue
                tz8 = timezone(timedelta(hours=8))
                date_str = dt.astimezone(tz8).strftime("%Y-%m-%d %H:%M") if dt else "?"
                hits.append({
                    "handle": h,
                    "date": date_str,
                    "text": text[:400],
                    "url": f"https://x.com/{h}/status/{tw['id']}",
                    "likes": tw["likes"],
                })
                kept += 1

            print(f"  ✓ {h}: 命中 {kept}", file=sys.stderr)
            await asyncio.sleep(2)

    hits.sort(key=lambda x: x["date"], reverse=True)
    OUT.mkdir(exist_ok=True)
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    out_file = OUT / f"x_scan_{group}_{stamp}.json"
    out_file.write_text(json.dumps({
        "_meta": {"group": group, "days": days, "generated_at": datetime.now().isoformat(),
                  "queried": queried, "skipped": skipped, "hits": len(hits)},
        "tweets": hits,
    }, ensure_ascii=False, indent=2))

    print(f"\n=== X 扫描结果 group={group} 近{days}天 命中{len(hits)}条 ===")
    for t in hits:
        print(f"\n[{t['date']}] @{t['handle']}  ❤{t['likes']}")
        print(f"  {t['text']}")
        print(f"  {t['url']}")
    print(f"\naudit → {out_file}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="deals",
                    help="deals | vc | ai | official | frontier | cn | mena | all")
    ap.add_argument("--days", type=int, default=3, help="只保留最近 N 天（默认3）")
    ap.add_argument("--per", type=int, default=15, help="每个账号取最近多少条（默认15）")
    ap.add_argument("--loose", action="store_true", help="放宽筛选：不要求含数字/deal词，用于 harvest 观点(cmt)")
    a = ap.parse_args()
    asyncio.run(scan(a.group, a.days, a.per, a.loose))
