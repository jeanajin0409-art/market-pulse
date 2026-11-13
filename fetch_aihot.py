#!/usr/bin/env python3
"""AIHOT 抓取（2026-10-31 起旧 /api/public/items 已停用）

迁移要点（per https://aihot.news/agent?tab=api#legacy-api-migration）:
  - 旧 https://aihot.virxact.com/api/public/items → 新 https://aihot.news/api/v1/items
  - take → limit
  - since → window（仅 '24h' / '7d'）+ 客户端按 publishedAt 收窄
  - 去掉 fields；旧 cursor 不可复用
  - item.url → item.links.original
  - item.source（字符串）→ item.source.name
"""
import json
import subprocess
import sys
import datetime as dt

API = "https://aihot.news/api/v1/items"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 aihot-skill/0.2.0")


def fetch(hours: int = 48, limit: int = 60, mode: str = "selected") -> list[dict]:
    url = f"{API}?mode={mode}&limit={limit}&window=7d"
    proc = subprocess.run(["curl", "-sL", "-H", f"User-Agent: {UA}", url],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"curl failed rc={proc.returncode}")
    payload = json.loads(proc.stdout)
    if payload.get("status") == 404:
        raise RuntimeError(f"AIHOT API 404: {payload.get('detail')}")
    items = payload.get("items", [])
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    out = []
    for it in items:
        pub = it.get("publishedAt")
        if not pub:
            continue
        try:
            pub_dt = dt.datetime.fromisoformat(pub.replace("Z", "+00:00"))
        except ValueError:
            continue
        if pub_dt < cutoff:
            continue
        src = it.get("source")
        src_name = src.get("name") if isinstance(src, dict) else (src or "?")
        links = it.get("links") or {}
        out.append({
            "id": it.get("id"),
            "title": it.get("title"),
            "summary": it.get("summary"),
            "source": src_name,
            "url": links.get("original") or links.get("aihot"),
            "publishedAt": pub,
            "category": it.get("category"),
            "score": it.get("score", 0),
        })
    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    hours = int(args[0]) if args and not args[0].startswith('-') else 48
    if '--json' in args:
        # 输出纯 JSON 数组，供 pipeline.py import-aihot 直接入库为 raw_candidates
        print(json.dumps(fetch(hours=hours), ensure_ascii=False, indent=2))
        sys.exit(0)
    items = fetch(hours=hours)
    print(f"items within {hours}h: {len(items)}")
    for i, it in enumerate(items, 1):
        print(f"{i:2d}. {it['score']:>3} | {it['source'][:24]:24} | {it['title'][:70]}")
        print(f"     {it['url']}")
