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
  （旧 build.py 的 fetch 子命令已删除 —— 抓取只有本文件这一条路径。）

命令：
  migrate                     建 raw_candidates 表 + items.raw_id 列（幂等，可重复跑）
  fetch --date D              抓 IT桔子 4 端点当天事件 → raw_candidates（绝不直写 items）
  import-aihot --date D       把 AIHOT 候选（fetch_aihot.py --json）写进 raw_candidates
  list --date D               列出当天候选，供人工挑选
  accept --date D --file F    精选结果 → items（整批校验 + 单事务写入 + 7 日对称去重，fail-closed）
  replace --from D --to D --files F1,F2
                              历史替换：整批校验通过后单事务 DELETE 旧行 + INSERT 新行，失败整批回滚
  stats --date D [--json]     raw/selected/published、来源、板块、score 分布、拒绝原因、字段失败

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
BJ = timezone(timedelta(hours=8))

ALLOWED_CATS = {'minority', 'ma', 'ipo', 'bigtech', 'ai', 'frontier'}
ALLOWED_REGIONS = {'中国', '美国', '亚太', '中东', '巴西', '欧洲', '全球'}

# 首页 URL 判定：path 为空或 '/' 即为首页（正式条目必须有具体原文链接）
HOMEPAGE_HOSTS = {'www.itjuzi.com', 'itjuzi.com', 'aihot.virxact.com', 'aihot.news'}

# why / sum 里不允许出现的股票买卖话术（task #1：不要股票买卖意见）
ADVICE_PATTERNS = [
    '建议买入', '建议卖出', '建议增持', '建议减持', '加仓', '减仓', '建仓', '满仓', '空仓',
    '目标价', '持仓', '重仓', '抄底', '追高', '配置比例',
]

ROUND_UNRESOLVED = re.compile(r'#\d+')
CDATA = re.compile(r'<!\[CDATA\[', re.I)


# ---------------------------------------------------------------- helpers
def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def get_conn():
    return sqlite3.connect(DB_PATH)


def canonical_url(url):
    """规范化 URL：去 www、去追踪参数与 fragment。"""
    if not url:
        return ''
    try:
        pr = urllib.parse.urlparse(url)
    except ValueError:
        return url.strip().lower()
    host = (pr.netloc or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    keep = [(k, v) for k, v in urllib.parse.parse_qsl(pr.query or '')
            if not k.lower().startswith(('utm_', 'spm', 'from', 'scene', 'xtrack', 'chksm',
                                         'poc_token', 'share', '__biz'))]
    keep.sort()
    path = (pr.path or '').rstrip('/')
    return f'{host}{path}?{urllib.parse.urlencode(keep)}'


def event_hash(subject, amount, round_name, parties, url, structured):
    """**跨来源**的事件指纹。

    - 结构化 deal（投资/收购/合并）：主体 + 金额 + 轮次 + 参与方 → 同一事件换来源仍是同一个 hash
    - 新闻：canonical URL（去 www / 去追踪参数）→ 同一链接换标题仍是同一个 hash
    跨来源的同一事件，再由 dedupe_key（主体名）补强。
    """
    if structured:
        raw = '|'.join([
            (subject or '').strip(),
            (amount or '').strip().replace(' ', ''),
            (round_name or '').strip(),
            '、'.join(sorted(p for p in (parties or []) if p)),
        ])
    else:
        raw = canonical_url(url) or (subject or '').strip()
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


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
    except Exception as e:
        return None, f'{path}: {e}'
    if not raw.strip():
        return None, f'{path}: 空响应'
    try:
        payload = json.loads(raw)
    except Exception as e:
        return None, f'{path}: JSON 解析失败 {e}'
    return payload.get('data') or [], None


def names_of(value):
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


def utc_to_bj_date(iso_ts):
    """UTC 时间戳 → 北京时间日期（UTC+8）。00:00–07:59 UTC 的条目属于北京时间次日。"""
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_ts).replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BJ).strftime('%Y-%m-%d')


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
            event_hash TEXT,
            subject TEXT,
            raw_json TEXT,
            status TEXT NOT NULL DEFAULT 'candidate',
            selected_item_id INTEGER,
            note TEXT,
            UNIQUE(source, content_hash)
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_raw_date ON raw_candidates(event_date)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_raw_status ON raw_candidates(status)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_raw_url ON raw_candidates(url)')
    raw_cols = {r[1] for r in c.execute('PRAGMA table_info(raw_candidates)')}
    for col, decl in (('event_hash', 'TEXT'), ('subject', 'TEXT')):
        if col not in raw_cols:
            c.execute(f'ALTER TABLE raw_candidates ADD COLUMN {col} {decl}')
            print(f'raw_candidates: 新增列 {col}')
    # 索引要在列存在之后建
    c.execute('CREATE INDEX IF NOT EXISTS idx_raw_event_hash ON raw_candidates(event_hash)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_raw_subject ON raw_candidates(subject)')
    cols = {r[1] for r in c.execute('PRAGMA table_info(items)')}
    if 'raw_id' not in cols:
        c.execute('ALTER TABLE items ADD COLUMN raw_id INTEGER')
        print('items: 新增列 raw_id')
    conn.commit()
    print(f'✓ schema 就绪（DB: {DB_PATH}）')
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
    structured = kind in ('investevent', 'acquisition', 'merger')
    # 主体 = 公司/机构名。新闻类**不**用媒体名当主体（那是来源，不是主体，会误拦同一家媒体的两条新闻）
    subject = (row.get('com_name') or '').strip() if structured else ''
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
        'event_hash': event_hash(subject, amount, round_name, parties, url, structured),
        'subject': subject,
        'raw_json': json.dumps(row, ensure_ascii=False),
    }


def insert_candidates(rows):
    conn = get_conn()
    c = conn.cursor()
    added = existed = 0
    fetched = now_iso()
    for r in rows:
        try:
            c.execute('''
                INSERT INTO raw_candidates
                    (source, source_id, url, event_date, published_at, fetched_at, title, summary,
                     category, score, round_name, amount, parties, content_hash, event_hash, subject,
                     raw_json, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'candidate')
            ''', (r['source'], r['source_id'], r['url'], r['event_date'], r['published_at'], fetched,
                  r['title'], r['summary'], r['category'], r['score'], r['round_name'], r['amount'],
                  r['parties'], r['content_hash'], r.get('event_hash'), r.get('subject'), r['raw_json']))
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
    """把 AIHOT 候选（fetch_aihot.py --json 的数组）写进 raw_candidates。

    publishedAt 是 UTC → 事件日期按北京时间 (UTC+8) 计算，避免北京时间 00:00–07:59
    的条目被归到前一天。
    """
    data = json.loads(Path(args.file).read_text())
    if isinstance(data, dict):
        data = data.get('items') or []
    rows = []
    for it in data:
        title = (it.get('title') or '').strip()
        if not title:
            continue
        pub = it.get('publishedAt') or ''
        event_date = utc_to_bj_date(pub) or args.date
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
            'event_hash': event_hash('', '', '', [], url, False),
            'subject': (it.get('category') or '').strip(),
            'raw_json': json.dumps(it, ensure_ascii=False),
        })
    added, existed = insert_candidates(rows)
    print(f'✓ AIHOT 候选新增 {added}，已存在 {existed}（事件日期按北京时间 UTC+8 计算）')
    return 0


# ---------------------------------------------------------------- list
def cmd_list(args):
    conn = get_conn()
    c = conn.cursor()
    rows = c.execute('''
        SELECT id, source, source_id, category, round_name, amount, url, title, status, note
        FROM raw_candidates WHERE event_date = ? ORDER BY source, id
    ''', (args.date,)).fetchall()
    conn.close()
    if not rows:
        print(f'(no raw candidates for {args.date} — 先跑 fetch)')
        return 0
    print(f'raw_candidates for {args.date}: {len(rows)}')
    for (rid, source, source_id, category, round_name, amount, url, title, status, note) in rows:
        flag = '' if url else '  ⚠️ 无原文 URL（不能收录）'
        print(f'\n  #{rid} [{source}] {category} {round_name} {amount}{flag}  status={status}')
        print(f'      {title[:90]}')
        if url:
            print(f'      {url[:110]}')
        if note:
            print(f'      note: {note}')
    return 0


# ---------------------------------------------------------------- accept
RAW_COLS = ('id, source, source_id, url, event_date, title, content_hash, event_hash, subject, status')
RAW_KEYS = ['id', 'source', 'source_id', 'url', 'event_date', 'title', 'content_hash',
            'event_hash', 'subject', 'status']


def _raw_select(conn, where, params):
    """读候选行；DB 还没迁移（缺 event_hash/subject 列）时降级查询。"""
    try:
        return conn.execute(f'SELECT {RAW_COLS} FROM raw_candidates WHERE {where}', params).fetchone()
    except sqlite3.OperationalError:
        return conn.execute(
            'SELECT id, source, source_id, url, event_date, title, content_hash, NULL, NULL, status '
            f'FROM raw_candidates WHERE {where}', params).fetchone()


def resolve_raw(conn, it, target_date):
    """按稳定键解析 raw candidate。返回 (row_dict, error_msg)。

    稳定键优先级：raw_content_hash → raw_source+raw_source_id → raw_url → raw_id。
    raw_id 只是自增号，跨 DB 重建不保证一致，因此只作最后手段，且必须逐项校验。
    """
    row = None
    if it.get('raw_content_hash'):
        cur = _raw_select(conn, 'content_hash = ? AND source = ?',
                          (it['raw_content_hash'], it.get('raw_source') or 'IT桔子'))
        if cur is None:
            return None, 'raw candidate 不存在（content_hash 对不上）'
        row = cur
    elif it.get('raw_source') and it.get('raw_source_id'):
        cur = _raw_select(conn, 'source = ? AND source_id = ?',
                          (it['raw_source'], str(it['raw_source_id'])))
        if cur is None:
            return None, 'raw candidate 不存在（source + source_id 对不上）'
        row = cur
    elif it.get('raw_url'):
        cur = _raw_select(conn, 'url = ?', (it['raw_url'],))
        if cur is None:
            return None, 'raw candidate 不存在（url 对不上）'
        row = cur
    elif it.get('raw_id'):
        cur = _raw_select(conn, 'id = ?', (it['raw_id'],))
        if cur is None:
            return None, 'raw candidate 不存在（id 对不上）'
        row = cur
    elif it.get('url'):
        # 兜底：用条目的 url 去找候选 —— 找不到就说明这条不是抓回来的候选
        cur = _raw_select(conn, 'url = ?', (it['url'],))
        if cur is None:
            return None, 'raw candidate 不存在（url 对不上）'
        row = cur
    else:
        return None, '缺少 raw 引用（raw_url / raw_source+raw_source_id / raw_content_hash / raw_id / url）'

    d = dict(zip(RAW_KEYS, row))
    if d['event_date'] != target_date:
        return None, f"raw candidate 日期不符（{d['event_date']} != {target_date}）"
    if d['status'] != 'candidate':
        return None, f"raw candidate 状态不是 candidate（{d['status']}）"
    if it.get('raw_content_hash') and it['raw_content_hash'] != d['content_hash']:
        return None, 'content_hash 与 raw candidate 不一致'
    if it.get('raw_source') and it.get('raw_source_id'):
        if it['raw_source'] != d['source'] or str(it['raw_source_id']) != str(d['source_id']):
            return None, 'source/source_id 与 raw candidate 不一致'
    if it.get('raw_url') and it['raw_url'] != d['url']:
        return None, 'url 与 raw candidate 不一致'
    return d, None


def dedupe_reason(conn, target_date, title, url, raw_hash, exclude_dates=(),
                  event_hash=None, subject=None):
    """7 日**对称**窗口去重：target-7d ~ target+7d，跨 title / url / content_hash / event_hash / 主体。

    - event_hash 是**跨来源**的事件指纹（主体+金额+轮次+参与方，或 canonical URL），
      换来源、换标题也认得出是同一件事
    - subject（主体名）用于补强：同一家公司同周内不该发两次
    历史回填时窗口必须对称 —— 只查过去会漏掉「同一事件在目标日之后又发过」。
    """
    d = datetime.strptime(target_date, '%Y-%m-%d')
    lo = (d - timedelta(days=7)).strftime('%Y-%m-%d')
    hi = (d + timedelta(days=7)).strftime('%Y-%m-%d')
    excl_i = excl_p = ''
    params = []
    if exclude_dates:
        lo_s, hi_s = min(exclude_dates), max(exclude_dates)
        excl_i = ' AND NOT (i.date BETWEEN ? AND ?)'
        excl_p = ' AND NOT (date BETWEEN ? AND ?)'
        params = [lo_s, hi_s]
    rows = conn.execute(
        f'SELECT date, title FROM items WHERE date BETWEEN ? AND ?{excl_p} AND (title = ? OR url = ?)',
        [lo, hi] + params + [title, url]).fetchall()
    if rows:
        return f'跨日重复（{rows[0][0]} 已发过）'
    if raw_hash:
        q = (f'SELECT i.date, i.title FROM items i JOIN raw_candidates r ON r.id = i.raw_id '
             f'WHERE i.date BETWEEN ? AND ?{excl_i} AND r.content_hash = ?')
        rows = conn.execute(q, [lo, hi] + params + [raw_hash]).fetchall()
        if rows:
            return f'内容重复（{rows[0][0]} 已发过同一事件）'
    if event_hash:
        q = (f'SELECT i.date, i.title FROM items i JOIN raw_candidates r ON r.id = i.raw_id '
             f'WHERE i.date BETWEEN ? AND ?{excl_i} AND r.event_hash = ?')
        rows = conn.execute(q, [lo, hi] + params + [event_hash]).fetchall()
        if rows:
            return f'事件重复（{rows[0][0]} 已发过同一事件，跨来源指纹一致）'
    # ⚠️ 这里**不做**「主体名出现在标题里」的跨日去重 —— 会误拦不同事件
    #（例：11/06「国星宇航今起招股」与 11/13「国星宇航今日上市」是同一主体的两件事）。
    # 跨日同一事件由 event_hash（主体+金额+轮次+参与方）识别；批内同主体由 PlanDeduper 拦。
    return None


class PlanDeduper:
    """**批内**去重：整批还没进库时，条目之间看不见彼此 → 必须自己盯住。

    title / url / event_hash / dedupe_key（主体）四个维度。
    """

    def __init__(self):
        self.titles = set()
        self.urls = set()
        self.event_hashes = set()
        self.subjects = set()

    def check(self, title, url, event_hash, subject):
        if title and title in self.titles:
            return f'批内重复标题（{title[:24]}）'
        if url and url in self.urls:
            return f'批内重复 url（{url[:40]}）'
        if event_hash and event_hash in self.event_hashes:
            return '批内重复事件（event_hash 相同）'
        if subject and subject in self.subjects:
            return f'批内重复主体（{subject}）'
        return None

    def add(self, title, url, event_hash, subject):
        if title:
            self.titles.add(title)
        if url:
            self.urls.add(url)
        if event_hash:
            self.event_hashes.add(event_hash)
        if subject:
            self.subjects.add(subject)


def validate_item(it, target_date, conn, exclude_dates=(), deduper=None):
    """返回 (ok, reason, cleaned, raw_dict)。任何一条不满足即拒（fail-closed）。"""
    title = (it.get('title') or '').strip()
    url = (it.get('url') or '').strip()
    summary = (it.get('sum') or '').strip()
    why = (it.get('why') or '').strip()
    cat = (it.get('cat') or '').strip()
    regions = it.get('regions') or []
    score = it.get('score')
    tags = it.get('tags') or []

    raw, raw_err = resolve_raw(conn, it, target_date)
    if raw_err:
        return False, raw_err, None, None

    if not title:
        return False, 'title 为空', None, raw
    if CDATA.search(title) or CDATA.search(summary):
        return False, '含 CDATA', None, raw
    if ROUND_UNRESOLVED.search(title) or ROUND_UNRESOLVED.search(summary):
        return False, '轮次未解析（#数字）', None, raw
    if title.startswith('AI HOT 日报'):
        return False, 'daily RSS 聚合标题（不得当新闻条目）', None, raw
    if not url:
        return False, '缺 url（没有出处的条目不可信）', None, raw
    if not url.startswith('http'):
        return False, 'url 非 http(s)', None, raw
    if urllib.parse.urlparse(url).path in ('', '/'):
        return False, '首页 URL（必须具体原文链接）', None, raw
    if raw.get('url') and url != raw['url']:
        return False, 'url 与 raw candidate 不一致（正式链接必须是候选的原文链接）', None, raw
    if len(summary) < 15:
        return False, 'sum 过短（需 2-3 句事实摘要）', None, raw
    if len(why) < 10:
        return False, 'why 为空或过短', None, raw
    for pat in ADVICE_PATTERNS:
        if pat in why or pat in summary:
            return False, f'含股票买卖话术「{pat}」', None, raw
    if cat not in ALLOWED_CATS:
        return False, f'cat 非法（{cat}）', None, raw
    if not isinstance(regions, list) or not regions:
        return False, 'regions 为空', None, raw
    bad = [r for r in regions if r not in ALLOWED_REGIONS]
    if bad:
        return False, f'regions 非法值 {bad}', None, raw
    if not isinstance(score, int) or not (1 <= score <= 100):
        return False, f'score 非法（{score}）', None, raw
    if not isinstance(tags, list):
        return False, 'tags 必须是数组', None, raw

    # dedupe_key 由策展人显式指定（通常是主体/公司名）；不从候选的 subject 自动继承
    # —— 自动继承会把「同一家媒体的两条不同新闻」或「同一家公司的两笔不同融资」误拦
    subject = (it.get('dedupe_key') or '').strip()
    dup = dedupe_reason(conn, target_date, title, url, raw['content_hash'], exclude_dates,
                         event_hash=raw.get('event_hash'), subject=subject or None)
    if dup:
        return False, dup, None, raw
    if deduper is not None:
        in_plan = deduper.check(title, url, raw.get('event_hash'), subject or None)
        if in_plan:
            return False, in_plan, None, raw

    cleaned = {
        'title': title, 'url': url, 'sum': summary, 'why': why, 'cat': cat,
        'regions': regions, 'score': score, 'tags': tags,
        'ma_cap': (it.get('ma_cap') or '').strip(),
        'src': (it.get('src') or raw['source'] or '').strip(),
        't': it.get('t'),
        'subject': subject,
        'event_hash': raw.get('event_hash'),
    }
    return True, '', cleaned, raw


def _insert_items(conn, target_date, plan, commit_sha):
    c = conn.cursor()
    for cleaned, raw in plan:
        t = cleaned['t'] or f'{int(target_date[5:7])}月{int(target_date[8:10])}日'
        cur = c.execute('''
            INSERT INTO items (date, t, src, cat, regions, score, title, url, sum, why, ma_cap, tags, commit_sha, raw_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (target_date, t, cleaned['src'], cleaned['cat'],
              json.dumps(cleaned['regions'], ensure_ascii=False), cleaned['score'],
              cleaned['title'], cleaned['url'], cleaned['sum'], cleaned['why'], cleaned['ma_cap'],
              json.dumps(cleaned['tags'], ensure_ascii=False), commit_sha, raw['id']))
        c.execute('UPDATE raw_candidates SET status=?, selected_item_id=? WHERE id=?',
                  ('selected', cur.lastrowid, raw['id']))


def _persist_rejections(conn, failures):
    """把拒绝原因持久化到 raw_candidates（status=rejected + note），让 stats 可审计。"""
    c = conn.cursor()
    for title, reason, raw in failures:
        if raw:
            c.execute('UPDATE raw_candidates SET status=?, note=? WHERE id=?',
                      ('rejected', reason[:200], raw['id']))


def cmd_accept(args):
    payload = json.loads(Path(args.file).read_text())
    items = payload.get('items') if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        print('✗ 输入需要是 {"items": [...]} 或数组')
        return 2
    conn = get_conn()
    # ① 先整批校验（不写库）—— 任一条不过，整批不落库；批内重复由 PlanDeduper 盯住
    plan, failures = [], []
    deduper = PlanDeduper()
    for it in items:
        ok, reason, cleaned, raw = validate_item(it, args.date, conn, deduper=deduper)
        if ok:
            deduper.add(cleaned['title'], cleaned['url'], cleaned.get('event_hash'),
                        cleaned.get('subject') or None)
            plan.append((cleaned, raw))
        else:
            failures.append((it.get('title', '')[:40], reason, raw))
    if failures:
        _persist_rejections(conn, failures)
        conn.commit()
        print(f'✗ 整批拒绝（{len(failures)}/{len(items)} 条不合格）：**0 条落库**')
        for title, reason, _ in failures:
            print(f'   ✗ [{reason}] {title}')
        conn.close()
        return 3
    # ② 校验全过 → 单事务写入
    commit_sha = f"accept-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    try:
        with conn:
            _insert_items(conn, args.date, plan, commit_sha)
    except sqlite3.Error as e:
        conn.rollback()
        print(f'✗ 写入失败，整批回滚（0 条落库）：{e}')
        conn.close()
        return 4
    print(f'✓ 收录 {len(plan)} 条（单事务提交，失败整批回滚）')
    conn.close()
    return 0


# ---------------------------------------------------------------- replace
def cmd_replace(args):
    """历史替换：整批校验通过后，单事务 DELETE 旧行 + INSERT 新行。

    失败（任何一条不合格 / 写入异常）→ 整批回滚，**旧数据原样不动**。
    """
    files = [f for f in args.files.split(',') if f.strip()]
    by_date, file_of_date, range_errors = {}, {}, []
    d0 = datetime.strptime(args.from_, '%Y-%m-%d')
    d1 = datetime.strptime(args.to, '%Y-%m-%d')
    span = []
    cur = d0
    while cur <= d1:
        span.append(cur.strftime('%Y-%m-%d'))
        cur += timedelta(days=1)
    for f in files:
        payload = json.loads(Path(f.strip()).read_text())
        items = payload.get('items') if isinstance(payload, dict) else payload
        d = payload.get('date') if isinstance(payload, dict) else None
        d = d or (items[0].get('date') if items else None)
        if not d:
            range_errors.append(f'{f}: payload 缺 date')
            continue
        if d not in span:
            range_errors.append(f'{f}: 日期 {d} 不在区间 {args.from_}~{args.to} 内')
            continue
        if d in file_of_date:
            range_errors.append(f'{f}: 日期 {d} 重复（已有 {file_of_date[d]}）')
            continue
        file_of_date[d] = f
        by_date.setdefault(d, []).extend(items or [])
    missing = [d for d in span if d not in by_date]
    if missing and not args.allow_missing:
        range_errors.append(f'区间缺日期的文件：{", ".join(missing)}'
                            '（要跳过请显式加 --allow-missing，生产 runbook 禁止）')
    if range_errors:
        print('✗ 替换范围校验未通过，**未做任何改动**：')
        for e in range_errors:
            print('   -', e)
        return 3

    conn = get_conn()
    plan_all, failures = {}, []
    deduper = PlanDeduper()          # 跨 4 个文件共用一个去重器
    for d, items in sorted(by_date.items()):
        plan = []
        for it in items:
            ok, reason, cleaned, raw = validate_item(it, d, conn, exclude_dates=(args.from_, args.to),
                                                     deduper=deduper)
            if ok:
                deduper.add(cleaned['title'], cleaned['url'], cleaned.get('event_hash'),
                            cleaned.get('subject') or None)
                plan.append((cleaned, raw))
            else:
                failures.append((d, it.get('title', '')[:40], reason, raw))
        plan_all[d] = plan
    if failures:
        _persist_rejections(conn, [(t, r, raw) for _, t, r, raw in failures])
        conn.commit()
        print(f'✗ 整批拒绝（{len(failures)} 条不合格）：**旧数据保持原样，0 条落库**')
        for d, title, reason, _ in failures:
            print(f'   ✗ [{d}] [{reason}] {title}')
        conn.close()
        return 3

    before = conn.execute(
        'SELECT COUNT(*) FROM items WHERE date BETWEEN ? AND ?', (args.from_, args.to)).fetchone()[0]
    commit_sha = f"replace-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    try:
        with conn:
            conn.execute('DELETE FROM items WHERE date BETWEEN ? AND ?', (args.from_, args.to))
            for d, plan in plan_all.items():
                _insert_items(conn, d, plan, commit_sha)
    except sqlite3.Error as e:
        conn.rollback()
        after = conn.execute('SELECT COUNT(*) FROM items WHERE date BETWEEN ? AND ?',
                             (args.from_, args.to)).fetchone()[0]
        print(f'✗ 写入失败，整批回滚：{e}（区间行数 {before} → {after}，旧数据未动）')
        conn.close()
        return 4
    after = conn.execute('SELECT COUNT(*) FROM items WHERE date BETWEEN ? AND ?',
                         (args.from_, args.to)).fetchone()[0]
    print(f'✓ 区间 {args.from_} ~ {args.to} 已替换：{before} → {after} 行（单事务提交）')
    for d, plan in sorted(plan_all.items()):
        print(f'   {d}: {len(plan)} 条')
    conn.close()
    return 0


# ---------------------------------------------------------------- reset
def cmd_reset(args):
    """把候选状态复位（默认回到 candidate）。

    被拒的候选会持久化 status=rejected + note；改完 sum/why 后想重新收录，
    先 reset 再 accept（否则会因「状态不是 candidate」被拒）。
    """
    conn = get_conn()
    c = conn.cursor()
    sql = ("UPDATE raw_candidates SET status='candidate', note=NULL, selected_item_id=NULL "
           "WHERE {where}")
    if args.status == 'all':
        where = "status != 'candidate'" + (" AND event_date = ?" if args.date else "")
        params = (args.date,) if args.date else ()
    else:
        where = "status = ?" + (" AND event_date = ?" if args.date else "")
        params = (args.status, args.date) if args.date else (args.status,)
    c.execute(sql.format(where=where), params)
    n = c.rowcount
    conn.commit()
    conn.close()
    print(f'✓ 已把 {n} 条候选复位为 candidate'
          + (f'（限定 {args.date}）' if args.date else ''))
    return 0


# ---------------------------------------------------------------- stats
def cmd_stats(args):
    conn = get_conn()
    c = conn.cursor()
    d = args.date
    raw = c.execute('SELECT COUNT(*) FROM raw_candidates WHERE event_date = ?', (d,)).fetchone()[0]
    raw_by_src = c.execute(
        'SELECT source, COUNT(*) FROM raw_candidates WHERE event_date = ? GROUP BY source', (d,)).fetchall()
    selected = c.execute("SELECT COUNT(*) FROM raw_candidates WHERE event_date = ? AND status = 'selected'",
                         (d,)).fetchone()[0]
    rejected = c.execute("SELECT COUNT(*) FROM raw_candidates WHERE event_date = ? AND status = 'rejected'",
                         (d,)).fetchone()[0]
    reject_reasons = c.execute(
        "SELECT note, COUNT(*) FROM raw_candidates WHERE event_date = ? AND status = 'rejected' "
        "GROUP BY note ORDER BY 2 DESC", (d,)).fetchall()
    pub = c.execute('SELECT COUNT(*) FROM items WHERE date = ?', (d,)).fetchone()[0]
    srcs = c.execute('SELECT src, COUNT(*) FROM items WHERE date = ? GROUP BY src ORDER BY 2 DESC', (d,)).fetchall()
    cats = c.execute('SELECT cat, COUNT(*) FROM items WHERE date = ? GROUP BY cat ORDER BY 2 DESC', (d,)).fetchall()
    scores = c.execute('SELECT score, COUNT(*) FROM items WHERE date = ? GROUP BY score ORDER BY score DESC',
                       (d,)).fetchall()
    no_url = c.execute(
        'SELECT COUNT(*) FROM raw_candidates WHERE event_date = ? AND (url IS NULL OR url = ?)',
        (d, '')).fetchone()[0]
    dup_rows = c.execute('''
        SELECT title, COUNT(DISTINCT date) n FROM items
        WHERE date >= date(?, '-7 day') AND date <= ? GROUP BY title HAVING n > 1
    ''', (d, d)).fetchall()
    field_fail = c.execute(
        'SELECT COUNT(*) FROM items WHERE date = ? AND (sum IS NULL OR sum = ? OR why IS NULL OR why = ?)',
        (d, '', '')).fetchone()[0]
    conn.close()

    report = {
        'date': d,
        'raw_candidates': raw,
        'raw_by_source': dict(raw_by_src),
        'selected': selected,
        'rejected': rejected,
        'rejected_reasons': [{'reason': n or '', 'count': c} for n, c in reject_reasons],
        'published': pub,
        'sources': dict(srcs),
        'categories': dict(cats),
        'score_distribution': {str(s): n for s, n in scores},
        'candidates_without_url': no_url,
        'cross_day_duplicate_titles': [{'title': t, 'days': n} for t, n in dup_rows],
        'field_failures': field_fail,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f'=== stats {d} ===')
    print(f'raw candidates      : {raw}' + ('  (' + ' / '.join(f'{s} {n}' for s, n in raw_by_src) + ')' if raw_by_src else ''))
    print(f'selected (候选已选)  : {selected}')
    print(f'rejected (已拒收)    : {rejected}' + ('  ' + ' / '.join(f'{n}×{c}' for n, c in reject_reasons) if reject_reasons else ''))
    print(f'published (items)   : {pub}')
    print('来源分布            : ' + (' / '.join(f'{s} {n}' for s, n in srcs) or '-'))
    print('板块分布            : ' + (' / '.join(f'{c} {n}' for c, n in cats) or '-'))
    print('score 分布          : ' + (' '.join(f'{s}({n})' for s, n in scores) or '-'))
    print(f'候选无原文 URL      : {no_url}（这些候选不能收录）')
    print(f'7 日内跨日重复标题  : {len(dup_rows)}')
    for t, n in dup_rows[:5]:
        print(f'    ×{n} {t[:50]}')
    print(f'published 字段失败   : {field_fail}')
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

    ia = sub.add_parser('import-aihot', help='AIHOT 候选 → raw_candidates（事件日期按北京时间）')
    ia.add_argument('--date', required=True)
    ia.add_argument('--file', required=True)
    ia.set_defaults(func=cmd_import_aihot)

    li = sub.add_parser('list', help='列出当天候选')
    li.add_argument('--date', required=True)
    li.set_defaults(func=cmd_list)

    ac = sub.add_parser('accept', help='精选结果 → items（整批校验 + 单事务 + 7 日对称去重）')
    ac.add_argument('--date', required=True)
    ac.add_argument('--file', required=True)
    ac.set_defaults(func=cmd_accept)

    rp = sub.add_parser('replace', help='历史替换：整批校验后单事务 DELETE 旧 + INSERT 新')
    rp.add_argument('--from', dest='from_', required=True)
    rp.add_argument('--to', required=True)
    rp.add_argument('--files', required=True, help='逗号分隔的精选 JSON 列表')
    rp.add_argument('--allow-missing', action='store_true',
                    help='允许区间内有日期没有对应文件（生产 runbook 禁止，仅调试用）')
    rp.set_defaults(func=cmd_replace)

    rs = sub.add_parser('reset', help='把被拒候选复位回 candidate')
    rs.add_argument('--date', help='限定日期（默认全部）')
    rs.add_argument('--status', default='rejected', help='要复位的原状态（默认 rejected；all = 所有非 candidate）')
    rs.set_defaults(func=cmd_reset)

    st = sub.add_parser('stats', help='统计')
    st.add_argument('--date', required=True)
    st.add_argument('--json', action='store_true', help='输出结构化 JSON 验收报告')
    st.set_defaults(func=cmd_stats)

    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == '__main__':
    main()
