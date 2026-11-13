#!/usr/bin/env python3
"""pipeline.py · dealhot 原始候选层 + 精选入库 + 统计

目标架构（task #1）：
    fetch → raw_candidates(DB) → 硬筛/去重 → 逐条精选写 sum/why → items(DB)
          → build → index.html + archive/ + channel post

与旧 build.py fetch 的区别（旧实现的三处硬错误）：
  1. 旧实现用 `?d=<date>&d=<date>&limit=20` —— `d` 不是该接口的参数，返回的是「最新 20 条」，
     不是当天事件；同一天被写进多天时，四天内容完全一样。
  2. 旧实现抓完直接 INSERT items —— 原始候选与正式条目没有分离，也没有去重闸门。
  3. 旧实现 url 兜底成 `https://www.itjuzi.com/`（首页），违反「正式条目保留具体原文 URL」。

命令：
  migrate                     建 raw_candidates 表 + items.raw_id 列（幂等，可重复跑）
  fetch --date D              抓 IT桔子 4 端点当天事件 → raw_candidates（绝不直写 items）
  import-aihot --date D       把 AIHOT 候选（fetch_aihot.py 的 JSON）写进 raw_candidates
  list --date D               列出当天候选，供人工挑选
  accept --date D --file F    精选结果 → items（字段闸门 + 7 日 title/URL/content_hash 去重，fail-closed）
  stats --date D              raw/selected/published、来源、板块、score 分布、去重拦截、字段失败

环境变量：
  DEALHOT_DB        覆盖 DB 路径（默认 <repo>/dealhot.db）
  DEALHOT_ITJUZI_ENV  覆盖凭证 env 路径（默认 <repo>/.secrets/itjuzi.env）
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEALHOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get('DEALHOT_DB', DEALHOT_DIR / 'dealhot.db'))
ITJUZI_ENV = Path(os.environ.get('DEALHOT_ITJUZI_ENV', DEALHOT_DIR / '.secrets' / 'itjuzi.env'))

API_BASE = 'https://openapi.itjuzi.com'
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 aihot-skill/0.2.0')

ALLOWED_CATS = {'minority', 'ma', 'ipo', 'bigtech', 'ai', 'frontier'}
ALLOWED_REGIONS = {'中国', '美国', '亚太', '中东', '巴西', '欧洲', '全球'}

# 首页 URL 判定：path 为空或 '/' 即为首页（正式条目必须有具体原文链接）
HOMEPAGE_HOSTS = {'www.itjuzi.com', 'itjuzi.com', 'aihot.virxact.com', 'aihot.news'}

# why / sum 里不允许出现的股票买卖话术（task #1：不要股票买卖意见）
ADVICE_PATTERNS = [
    '建议买入', '建议卖出', '建议增持', '建议减持', '加仓', '减仓', '建仓', '满仓', '空仓',
    '目标价', '持仓', '重仓', '抄底', '追高', '配置比例',
]

# 轮次未解析（原始 round_id）——规格禁止
ROUND_UNRESOLVED = re.compile(r'#\d+')
CDATA = re.compile(r'<!\[CDATA\[', re.I)


# ---------------------------------------------------------------- helpers
def bj_now():
    return datetime.now(timezone(timedelta(hours=8)))


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def get_conn():
    return sqlite3.connect(DB_PATH)


def content_hash(source, title, amount, round_name, parties):
    raw = '|'.join([
        (source or '').strip(),
        (title or '').strip(),
        (amount or '').strip(),
        (round_name or '').strip(),
        '、'.join(sorted(p for p in (parties or []) if p)),
    ])
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def load_itjuzi_env():
    if ITJUZI_ENV.exists():
        for line in ITJUZI_ENV.read_text().strip().split('\n'):
            if '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())


def itjuzi_token():
    load_itjuzi_env()
    appid = os.environ.get('ITJUZI_APPID')
    appkey = os.environ.get('ITJUZI_APPKEY')
    if not appid or not appkey:
        raise RuntimeError('缺少 ITJUZI_APPID / ITJUZI_APPKEY（%s）' % ITJUZI_ENV)
    data = urllib.parse.urlencode({
        'appid': appid, 'appkey': appkey, 'granttype': 'client_credentials'
    }).encode()
    req = urllib.request.Request(f'{API_BASE}/oauth2.0/get_access_token', data=data)
    payload = json.loads(urllib.request.urlopen(req, timeout=20).read())
    return payload['data']['access_token']


def itjuzi_get(token, path, params):
    url = f'{API_BASE}/{path}?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'AUTHORIZATION': f'Bearer {token}'})
    try:
        raw = urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:                                    # 端点不可用不算致命
        return None, f'{path}: {e}'
    if not raw.strip():
        return None, f'{path}: 空响应'
    try:
        payload = json.loads(raw)
    except Exception as e:
        return None, f'{path}: JSON 解析失败 {e}'
    return payload.get('data') or [], None


def names_of(value):
    """投资方/收购方字段形态不一：list[dict{name}] / list[str] / str。"""
    out = []
    if isinstance(value, list):
        for v in value:
            if isinstance(v, dict):
                n = v.get('name') or v.get('com_legal_name') or v.get('com_name')
                if n:
                    out.append(str(n))
            elif isinstance(v, str) and v.strip():
                out.append(v.strip())
    elif isinstance(value, str) and value.strip():
        out.append(value.strip())
    return out


def first_news_url(row):
    """从 news[] / news_url 里取第一条具体原文链接（绝不用首页兜底）。"""
    news = row.get('news')
    if isinstance(news, list):
        for n in news:
            if isinstance(n, dict):
                for k in ('url', 'news_url', 'link'):
                    u = (n.get(k) or '').strip()
                    if u.startswith('http'):
                        return u
            elif isinstance(n, str) and n.startswith('http'):
                return n
    for k in ('news_url', 'url', 'source_url', 'link'):
        u = (row.get(k) or '').strip()
        if isinstance(u, str) and u.startswith('http'):
            return u
    return None


def event_date_of(row, yk, mk, dk):
    y, m, d = row.get(yk), row.get(mk), row.get(dk)
    try:
        return f'{int(y):04d}-{int(m):02d}-{int(d):02d}'
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- migrate
def cmd_migrate(args):
    conn = get_conn()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS raw_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_id TEXT,
            url TEXT,
            event_date TEXT NOT NULL,
            published_at TEXT,
            fetched_at TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT,
            category TEXT,
            score REAL,
            round_name TEXT,
            amount TEXT,
            parties TEXT,
            content_hash TEXT NOT NULL,
            raw_json TEXT,
            status TEXT NOT NULL DEFAULT 'candidate',
            selected_item_id INTEGER,
            note TEXT,
            UNIQUE(source, content_hash)
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_raw_date ON raw_candidates(event_date)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_raw_status ON raw_candidates(status)')
    cols = {r[1] for r in c.execute('PRAGMA table_info(items)')}
    if 'raw_id' not in cols:
        c.execute('ALTER TABLE items ADD COLUMN raw_id INTEGER')
        print('items: 新增列 raw_id')
    conn.commit()
    print(f'✓ schema 就绪（DB: {DB_PATH}）')
    print('  raw_candidates 表:', '存在' if 'raw_candidates' in {
        r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")} else '缺失')
    conn.close()
    return 0


# ---------------------------------------------------------------- fetch
def fetch_window(token, path, date_pattern, d_from, d_to):
    """取一个日期窗口。API 单日区间返回 0 条，所以窗口至少 2 天；再按事件日过滤。"""
    rows, err = itjuzi_get(token, path, {
        'date_pattern': date_pattern, 'date_start': d_from, 'date_end': d_to,
        'limit': 100, 'order': 1, 'order_rules': 1,
    })
    if err:
        return [], err
    out = list(rows)
    rows2, err2 = itjuzi_get(token, path, {
        'date_pattern': date_pattern, 'date_start': d_from, 'date_end': d_to,
        'limit': 100, 'order': 2, 'order_rules': 1,
    })
    if not err2 and rows2:
        out.extend(rows2)
    return out, None


def normalize_itjuzi(kind, row, target_date):
    """把一个 IT桔子事件规整成候选行。返回 None 表示日期不匹配目标日。"""
    if kind == 'investevent':
        title = (row.get('event_title') or '').strip()
        event_date = event_date_of(row, 'invest_year', 'invest_month', 'invest_day')
        parties = names_of(row.get('leading_investor')) + names_of(row.get('following_investor'))
        amount = (row.get('approximate_amount_name') or '').strip()
        if not amount or amount == '未透露':
            detail = (row.get('detail_amount') or '').strip()
            currency = (row.get('currency_name') or '').strip()
            amount = f'{detail}{currency}' if detail else ''
        category = row.get('com_industry_name') or row.get('com_sub_industry_name') or ''
        summary = (row.get('event_des') or '').strip()
        round_name = (row.get('round_name') or '').strip()
        source_id = str(row.get('event_id') or '')
        published = row.get('create_time')
    elif kind in ('acquisition', 'merger'):
        title = (row.get('event_tilte') or row.get('event_title') or '').strip()  # API 字段拼写有 typo
        event_date = event_date_of(row, 'purchase_year', 'purchase_month', 'purchase_day')
        parties = names_of(row.get('purchaser'))
        amount = (row.get('approximate_amount_name') or '').strip()
        if not amount or amount == '未透露':
            detail = (row.get('detail_amount') or '').strip()
            currency = (row.get('currency_name') or '').strip()
            amount = f'{detail}{currency}' if detail else ''
        category = row.get('com_industry_name') or row.get('com_sub_industry_name') or ''
        summary = (row.get('event_des') or '').strip()
        round_name = ''
        source_id = str(row.get('event_id') or '')
        published = row.get('create_time')
    elif kind == 'news':
        title = (row.get('news_title') or '').strip()
        event_date = (row.get('news_time') or '')[:10] or None
        parties = names_of(row.get('related_party'))
        amount = ''
        category = (row.get('news_source') or '').strip()
        summary = ''
        round_name = ''
        source_id = str(row.get('news_id') or row.get('id') or '')
        published = row.get('news_time')
    else:
        return None

    if not title or not event_date or event_date != target_date:
        return None

    url = first_news_url(row)
    return {
        'source': 'IT桔子',
        'source_id': source_id,
        'url': url,
        'event_date': event_date,
        'published_at': published,
        'title': title,
        'summary': summary,
        'category': category,
        'score': None,
        'round_name': round_name,
        'amount': amount,
        'parties': '、'.join(parties),
        'content_hash': content_hash('IT桔子', title, amount, round_name, parties),
        'raw_json': json.dumps(row, ensure_ascii=False),
    }


def insert_candidates(rows):
    """写入 raw_candidates（UNIQUE(source,content_hash) 去重）。返回 (新增, 已存在)。"""
    conn = get_conn()
    c = conn.cursor()
    added = existed = 0
    fetched = now_iso()
    for r in rows:
        try:
            c.execute('''
                INSERT INTO raw_candidates
                    (source, source_id, url, event_date, published_at, fetched_at, title, summary,
                     category, score, round_name, amount, parties, content_hash, raw_json, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'candidate')
            ''', (r['source'], r['source_id'], r['url'], r['event_date'], r['published_at'], fetched,
                  r['title'], r['summary'], r['category'], r['score'], r['round_name'], r['amount'],
                  r['parties'], r['content_hash'], r['raw_json']))
            added += 1
        except sqlite3.IntegrityError:
            existed += 1
    conn.commit()
    conn.close()
    return added, existed


def cmd_fetch(args):
    token = itjuzi_token()
    target = args.date
    d0 = datetime.strptime(target, '%Y-%m-%d')
    # 单日区间 API 返回 0 条 → 用 ±1 天的窗口覆盖，再按事件日精确过滤
    windows = [
        ((d0 - timedelta(days=1)).strftime('%Y-%m-%d'), d0.strftime('%Y-%m-%d')),
        (d0.strftime('%Y-%m-%d'), (d0 + timedelta(days=1)).strftime('%Y-%m-%d')),
    ]
    endpoints = [
        ('investevent', 'investevent/get_investevent_list_v2', 3),
        ('acquisition', 'merger/get_acquisition_list_v2', 3),
        ('merger', 'merger/get_merger_list_v2', 3),
        ('news', 'news/get_spider_news_list_v2', 1),
    ]
    all_rows, errors = [], []
    for kind, path, pattern in endpoints:
        got = []
        for w_from, w_to in windows:
            rows, err = fetch_window(token, path, pattern, w_from, w_to)
            if err:
                errors.append(err)
                break
            got.extend(rows)
        seen = set()
        n_raw = len(got)
        for row in got:
            norm = normalize_itjuzi(kind, row, target)
            if not norm:
                continue
            key = norm['content_hash']
            if key in seen:
                continue
            seen.add(key)
            all_rows.append(norm)
        print(f'  {kind:11s} 原始 {n_raw:4d} 条 → 当日去重后 {len(seen):3d} 条')
    added, existed = insert_candidates(all_rows)
    print(f'\n✓ raw_candidates 新增 {added}，已存在 {existed}（DB: {DB_PATH}）')
    if errors:
        print('⚠️ 端点异常：')
        for e in sorted(set(errors)):
            print('   ', e)
    return 0


def cmd_import_aihot(args):
    """把 AIHOT 候选（fetch_aihot.py 输出的 JSON 数组）写进 raw_candidates。"""
    data = json.loads(Path(args.file).read_text())
    if isinstance(data, dict):
        data = data.get('items') or []
    rows = []
    for it in data:
        title = (it.get('title') or '').strip()
        if not title:
            continue
        pub = it.get('publishedAt') or ''
        event_date = (pub[:10] or args.date)
        url = it.get('url') or ''
        rows.append({
            'source': 'AIHOT',
            'source_id': str(it.get('id') or ''),
            'url': url if url.startswith('http') else None,
            'event_date': event_date,
            'published_at': pub,
            'title': title,
            'summary': (it.get('summary') or '').strip(),
            'category': it.get('category') or '',
            'score': it.get('score'),
            'round_name': '',
            'amount': '',
            'parties': '',
            'content_hash': content_hash('AIHOT', title, '', '', []),
            'raw_json': json.dumps(it, ensure_ascii=False),
        })
    added, existed = insert_candidates(rows)
    print(f'✓ AIHOT 候选新增 {added}，已存在 {existed}')
    return 0


# ---------------------------------------------------------------- list
def cmd_list(args):
    conn = get_conn()
    c = conn.cursor()
    rows = c.execute('''
        SELECT id, source, category, round_name, amount, url, title, status
        FROM raw_candidates WHERE event_date = ? ORDER BY source, id
    ''', (args.date,)).fetchall()
    conn.close()
    if not rows:
        print(f'(no raw candidates for {args.date} — 先跑 fetch)')
        return 0
    print(f'raw_candidates for {args.date}: {len(rows)}')
    for (rid, source, category, round_name, amount, url, title, status) in rows:
        flag = '' if url else '  ⚠️ 无原文 URL（不能收录）'
        print(f'\n  #{rid} [{source}] {category} {round_name} {amount}{flag}')
        print(f'      {title[:90]}')
        if url:
            print(f'      {url[:110]}')
        if status != 'candidate':
            print(f'      status={status}')
    return 0


# ---------------------------------------------------------------- accept
def validate_item(it, target_date, conn):
    """返回 (ok, reason, cleaned)。任何一条不满足即拒（fail-closed）。"""
    title = (it.get('title') or '').strip()
    url = (it.get('url') or '').strip()
    summary = (it.get('sum') or '').strip()
    why = (it.get('why') or '').strip()
    cat = (it.get('cat') or '').strip()
    regions = it.get('regions') or []
    score = it.get('score')
    tags = it.get('tags') or []

    if not title:
        return False, 'title 为空', None
    if CDATA.search(title) or CDATA.search(summary):
        return False, '含 CDATA', None
    if ROUND_UNRESOLVED.search(title) or ROUND_UNRESOLVED.search(summary):
        return False, '轮次未解析（#数字）', None
    if title.startswith('AI HOT 日报'):
        return False, 'daily RSS 聚合标题（不得当新闻条目）', None
    if not url:
        return False, '缺 url（没有出处的条目不可信）', None
    if not url.startswith('http'):
        return False, 'url 非 http(s)', None
    parsed = urllib.parse.urlparse(url)
    if parsed.path in ('', '/'):
        return False, '首页 URL（必须具体原文链接）', None
    if parsed.netloc in HOMEPAGE_HOSTS and parsed.path in ('', '/'):
        return False, '首页 URL（必须具体原文链接）', None
    if len(summary) < 15:
        return False, 'sum 过短（需 2-3 句事实摘要）', None
    if len(why) < 10:
        return False, 'why 为空或过短', None
    for pat in ADVICE_PATTERNS:
        if pat in why or pat in summary:
            return False, f'含股票买卖话术「{pat}」', None
    if cat not in ALLOWED_CATS:
        return False, f'cat 非法（{cat}）', None
    if not isinstance(regions, list) or not regions:
        return False, 'regions 为空', None
    bad = [r for r in regions if r not in ALLOWED_REGIONS]
    if bad:
        return False, f'regions 非法值 {bad}', None
    if not isinstance(score, int) or not (1 <= score <= 100):
        return False, f'score 非法（{score}）', None
    if not isinstance(tags, list):
        return False, 'tags 必须是数组', None

    # 7 日跨日去重：同标题或同 URL 在前后 7 天内已发布 → 拦截
    since = (datetime.strptime(target_date, '%Y-%m-%d') - timedelta(days=7)).strftime('%Y-%m-%d')
    dup = conn.execute(
        'SELECT date, title FROM items WHERE date >= ? AND date <= ? AND (title = ? OR url = ?)',
        (since, target_date, title, url)).fetchall()
    if dup:
        return False, f'跨日重复（{dup[0][0]} 已发过）', None

    # 同主体重复：同一事件被不同来源各写一条（如「眸深智能 Pre-A」两条报道）→ 拦截
    dedupe_key = (it.get('dedupe_key') or '').strip()
    if dedupe_key:
        same = conn.execute(
            "SELECT date, title FROM items WHERE date >= ? AND date <= ? AND title LIKE ?",
            (since, target_date, f'%{dedupe_key}%')).fetchall()
        if same:
            return False, f'同主体重复（{same[0][0]} 已发过：{same[0][1][:24]}）', None

    cleaned = {
        'title': title, 'url': url, 'sum': summary, 'why': why, 'cat': cat,
        'regions': regions, 'score': score, 'tags': tags,
        'ma_cap': (it.get('ma_cap') or '').strip(),
        'src': (it.get('src') or '').strip(),
        'raw_id': it.get('raw_id'),
        't': it.get('t'),
    }
    return True, '', cleaned


def cmd_accept(args):
    payload = json.loads(Path(args.file).read_text())
    items = payload.get('items') if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        print('✗ 输入需要是 {"items": [...]} 或数组')
        return 2
    conn = get_conn()
    c = conn.cursor()
    accepted, failures, dup_blocked = 0, [], 0
    commit_sha = f"accept-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    for it in items:
        ok, reason, cleaned = validate_item(it, args.date, conn)
        if not ok:
            if '跨日重复' in reason:
                dup_blocked += 1
            failures.append((it.get('title', '')[:40], reason))
            continue
        t = cleaned['t'] or f'{int(args.date[5:7])}月{int(args.date[8:10])}日'
        src = cleaned['src'] or 'IT桔子'
        try:
            cur = c.execute('''
                INSERT INTO items (date, t, src, cat, regions, score, title, url, sum, why, ma_cap, tags, commit_sha, raw_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ''', (args.date, t, src, cleaned['cat'], json.dumps(cleaned['regions'], ensure_ascii=False),
                  cleaned['score'], cleaned['title'], cleaned['url'], cleaned['sum'], cleaned['why'],
                  cleaned['ma_cap'], json.dumps(cleaned['tags'], ensure_ascii=False), commit_sha,
                  cleaned['raw_id']))
            accepted += 1
            if cleaned['raw_id']:
                c.execute('UPDATE raw_candidates SET status=?, selected_item_id=? WHERE id=?',
                          ('selected', cur.lastrowid, cleaned['raw_id']))
        except sqlite3.IntegrityError as e:
            failures.append((cleaned['title'][:40], f'DB 约束冲突 {e}'))
    conn.commit()
    conn.close()
    print(f'✓ 收录 {accepted} 条；去重拦截 {dup_blocked} 条；字段/闸门失败 {len(failures) - dup_blocked} 条')
    for title, reason in failures:
        print(f'   ✗ [{reason}] {title}')
    return 0


# ---------------------------------------------------------------- stats
def cmd_stats(args):
    conn = get_conn()
    c = conn.cursor()
    d = args.date
    raw = c.execute("SELECT COUNT(*) FROM raw_candidates WHERE event_date=?", (d,)).fetchone()[0]
    raw_by_src = c.execute(
        "SELECT source, COUNT(*) FROM raw_candidates WHERE event_date=? GROUP BY source", (d,)).fetchall()
    selected = c.execute("SELECT COUNT(*) FROM raw_candidates WHERE event_date=? AND status='selected'",
                         (d,)).fetchone()[0]
    pub = c.execute("SELECT COUNT(*) FROM items WHERE date=?", (d,)).fetchone()[0]
    srcs = c.execute("SELECT src, COUNT(*) FROM items WHERE date=? GROUP BY src ORDER BY 2 DESC", (d,)).fetchall()
    cats = c.execute("SELECT cat, COUNT(*) FROM items WHERE date=? GROUP BY cat ORDER BY 2 DESC", (d,)).fetchall()
    scores = c.execute("SELECT score, COUNT(*) FROM items WHERE date=? GROUP BY score ORDER BY score DESC",
                       (d,)).fetchall()
    no_url = c.execute(
        "SELECT COUNT(*) FROM raw_candidates WHERE event_date=? AND (url IS NULL OR url='')",
        (d,)).fetchone()[0]
    dup_rows = c.execute('''
        SELECT title, COUNT(DISTINCT date) n FROM items
        WHERE date >= date(?, '-7 day') AND date <= ? GROUP BY title HAVING n > 1
    ''', (d, d)).fetchall()
    conn.close()

    print(f'=== stats {d} ===')
    print(f'raw candidates      : {raw}' + ('  (' + ' / '.join(f'{s} {n}' for s, n in raw_by_src) + ')' if raw_by_src else ''))
    print(f'selected (候选已选)  : {selected}')
    print(f'published (items)   : {pub}')
    print(f'来源分布            : ' + (' / '.join(f'{s} {n}' for s, n in srcs) or '-'))
    print(f'板块分布            : ' + (' / '.join(f'{c} {n}' for c, n in cats) or '-'))
    print(f'score 分布          : ' + (' '.join(f'{s}({n})' for s, n in scores) or '-'))
    print(f'候选无原文 URL      : {no_url}（这些候选不能收录）')
    print(f'7 日内跨日重复标题  : {len(dup_rows)}')
    for t, n in dup_rows[:5]:
        print(f'    ×{n} {t[:50]}')
    return 0


# ---------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser(description='dealhot 原始候选层 / 精选入库 / 统计')
    sub = p.add_subparsers(dest='cmd', required=True)

    m = sub.add_parser('migrate', help='建 raw_candidates 表 + items.raw_id 列')
    m.set_defaults(func=cmd_migrate)

    f = sub.add_parser('fetch', help='抓 IT桔子 4 端点当天事件 → raw_candidates')
    f.add_argument('--date', required=True)
    f.set_defaults(func=cmd_fetch)

    ia = sub.add_parser('import-aihot', help='AIHOT 候选 → raw_candidates')
    ia.add_argument('--date', required=True)
    ia.add_argument('--file', required=True)
    ia.set_defaults(func=cmd_import_aihot)

    li = sub.add_parser('list', help='列出当天候选')
    li.add_argument('--date', required=True)
    li.set_defaults(func=cmd_list)

    ac = sub.add_parser('accept', help='精选结果 → items（字段闸门 + 7 日去重）')
    ac.add_argument('--date', required=True)
    ac.add_argument('--file', required=True)
    ac.set_defaults(func=cmd_accept)

    st = sub.add_parser('stats', help='统计')
    st.add_argument('--date', required=True)
    st.set_defaults(func=cmd_stats)

    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == '__main__':
    main()
