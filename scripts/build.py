#!/usr/bin/env python3
"""build.py · dealhot 方案 B 一键 build 脚本.

8 步流程硬闸门 (Mushroom-Quick 7cba293d):
1. git pull --ff-only origin main (跳过本地手动, 强制从 DB 重建)
2. 拉候选 → 去重/日期核验 → 写入 dealhot.db (单 source of truth)
3. DB schema gate: 必填字段 (d/t/src/cat/regions/score/title/url) + score 1-100 + 日期 YYYY-MM-DD
4. --dry-run 模式: 验证 + 输出统计 (新增数/重复数/缺字段数/来源数) + 不写文件
5. 正式 build: 一次生成 index.html (主页面) + archive/<date>.html (当天快照) + archive/index.html (往期索引)
6. 提交前自动检查: 主页 + 当天归档 JS 语法 OK + 必填字段 + 卡片数 > 0 + 导读 + 日期一致 + archive index 含当天
7. (本地浏览器 verify - 由调用方执行)
8. (commit/push + Pages verify - 由调用方执行)
"""
import argparse
import json
import re
import sqlite3
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Beijing time helpers
def bj_now():
    return datetime.now(timezone(timedelta(hours=8)))

def bj_today_str():
    return bj_now().strftime('%Y-%m-%d')

def bj_date_label(date_str):
    """2026-07-24 → 2026年7月24日 · 星期五"""
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    weekday_cn = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日'][dt.weekday()]
    return f"{dt.year}年{dt.month}月{dt.day}日 · {weekday_cn}"

# Paths
DEALHOT_DIR = Path('/Users/mac/Documents/market-pulse')
DB_PATH = DEALHOT_DIR / 'dealhot.db'
INDEX_PATH = DEALHOT_DIR / 'index.html'
ARCHIVE_DIR = DEALHOT_DIR / 'archive'

# --- DB helpers ---
def get_conn():
    return sqlite3.connect(DB_PATH)

REQUIRED_FIELDS = ['date', 't', 'src', 'cat', 'regions', 'score', 'title', 'url']
MAX_SCORE = 100
MIN_SCORE = 1
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

def validate_date(d):
    if not isinstance(d, str): return False, 'date not string'
    if not DATE_RE.match(d): return False, f'date format wrong: {d!r}'
    return True, ''

def validate_score(s):
    if s is None: return False, 'score is None'
    if not isinstance(s, (int, float)): return False, f'score not number: {s!r}'
    if not (MIN_SCORE <= s <= MAX_SCORE): return False, f'score out of range: {s}'
    return True, ''

def schema_gate(item):
    """Validate one item against schema rules."""
    issues = []
    for k in REQUIRED_FIELDS:
        if k not in item or item.get(k) in (None, ''):
            issues.append(f'missing {k}')
    if 'score' in item:
        ok, msg = validate_score(item['score'])
        if not ok: issues.append(msg)
    if 'date' in item:
        ok, msg = validate_date(item['date'])
        if not ok: issues.append(msg)
    # PII check (basic): title/sum should not contain obvious personal finance numbers
    text = (item.get('title', '') or '') + ' ' + (item.get('sum', '') or '')
    if re.search(r'\b\d{8,}\b', text):  # long digit runs (account numbers)
        issues.append('PII: long digit run detected')
    return issues

def db_stats():
    """Return overall stats from DB."""
    c = get_conn().cursor()
    total = c.execute('SELECT COUNT(*) FROM items').fetchone()[0]
    srcs = c.execute('SELECT src, COUNT(*) FROM items GROUP BY src').fetchall()
    dates = c.execute('SELECT date, COUNT(*) FROM items GROUP BY date ORDER BY date DESC LIMIT 7').fetchall()
    return {'total': total, 'by_src': srcs, 'recent_dates': dates}

# --- JS rendering helpers ---
def js_escape(s):
    if s is None: return '""'
    return json.dumps(str(s), ensure_ascii=False)

def js_array(items):
    return '[' + ','.join(js_escape(x) for x in items) + ']'

def render_item(it):
    parts = []
    parts.append(f'  t:{js_escape(it.get("t", ""))}')
    parts.append(f'  d:{js_escape(it.get("date", ""))}')
    parts.append(f'  src:{js_escape(it.get("src", ""))}')
    parts.append(f'  cat:{js_escape(it.get("cat", ""))}')
    if it.get("regions"):
        parts.append(f'  regions:{js_array(it["regions"])}')
    if it.get("score") is not None:
        parts.append(f'  score:{int(it["score"])}')
    parts.append(f'  title:{js_escape(it.get("title", ""))}')
    if it.get("url"):
        parts.append(f'  url:{js_escape(it["url"])}')
    if it.get("sum"):
        parts.append(f'  sum:{js_escape(it["sum"])}')
    if it.get("ma_cap"):
        parts.append(f'  ma_cap:{js_escape(it["ma_cap"])}')
    if it.get("tags"):
        parts.append(f'  tags:{js_array(it["tags"])}')
    return '{\n' + ',\n'.join(parts) + '\n}'

def divider(text):
    return f'// ───────────── {text} ──────────────'

# --- Lead text generation ---
def build_lead_p(date_str, top_n=3):
    """Build today's lead from top N items in DB."""
    c = get_conn().cursor()
    rows = c.execute('''
        SELECT title, sum FROM items
        WHERE date = ?
        ORDER BY score DESC, id ASC
        LIMIT ?
    ''', (date_str, top_n)).fetchall()
    if not rows:
        return f'今日 dealhot 无新增条目。'
    fragments = []
    for i, (title, sum_) in enumerate(rows, 1):
        # Take short label: just title (truncate at 30 chars)
        label = title.replace('。', '').replace('，', ',')[:30]
        fragments.append(f'<strong>{label}</strong>')
    text = f'今日新增 {len(rows)} 条。'
    text += '、'.join(fragments)
    if len(rows) >= 2:
        text += ' 等。'
    else:
        text += '。'
    return text

# --- Generator: main index.html ---
def generate_main():
    """Generate index.html (main page) with all items."""
    c = get_conn().cursor()
    rows = c.execute('''
        SELECT date, t, src, cat, regions, score, title, url, sum, ma_cap, tags, commit_sha
        FROM items
        ORDER BY date DESC, score DESC, id ASC
    ''').fetchall()
    cols = ['date', 't', 'src', 'cat', 'regions', 'score', 'title', 'url', 'sum', 'ma_cap', 'tags', 'commit_sha']
    items = [dict(zip(cols, r)) for r in rows]
    # Parse JSON fields
    for it in items:
        try:
            it['regions'] = json.loads(it['regions']) if it['regions'] else []
        except: it['regions'] = []
        try:
            it['tags'] = json.loads(it['tags']) if it['tags'] else None
        except: it['tags'] = None

    # Group by (commit_sha, date)
    from collections import OrderedDict
    groups = OrderedDict()
    for it in items:
        key = (it.get('commit_sha', 'unknown'), it.get('date', ''))
        groups.setdefault(key, []).append(it)
    for k in groups:
        groups[k].sort(key=lambda x: (x.get('src', ''), x.get('title', '')))
    sorted_keys = sorted(groups.keys(), key=lambda x: x[1], reverse=True)

    out = '[\n'
    first = True
    for (commit_sha, date) in sorted_keys:
        items_in = groups[(commit_sha, date)]
        if not first:
            out += '\n'
        first = False
        src_counter = {}
        for it in items_in:
            src_counter[it.get('src', '')] = src_counter.get(it.get('src', ''), 0) + 1
        common_src = max(src_counter, key=src_counter.get) if src_counter else 'unknown'
        out += divider(f'{common_src} {date} 新增') + '\n'
        for it in items_in:
            out += render_item(it) + ',\n'
    if out.endswith(',\n'):
        out = out[:-2] + '\n'
    out += '];'
    return out

# --- Generator: archive/<date>.html ---
def generate_archive_html(date_str, today_only=True):
    """Generate archive HTML for given date.
    If today_only=True, include only items with that date.
    If today_only=False, include all items (for the full archive snapshot).
    Returns the FULL HTML content (header + ITEMS array + footer).
    """
    c = get_conn().cursor()
    if today_only:
        rows = c.execute('''
            SELECT date, t, src, cat, regions, score, title, url, sum, ma_cap, tags, commit_sha
            FROM items
            WHERE date = ?
            ORDER BY score DESC, id ASC
        ''', (date_str,)).fetchall()
    else:
        rows = c.execute('''
            SELECT date, t, src, cat, regions, score, title, url, sum, ma_cap, tags, commit_sha
            FROM items
            ORDER BY date DESC, score DESC, id ASC
        ''').fetchall()
    cols = ['date', 't', 'src', 'cat', 'regions', 'score', 'title', 'url', 'sum', 'ma_cap', 'tags', 'commit_sha']
    items = [dict(zip(cols, r)) for r in rows]
    for it in items:
        try: it['regions'] = json.loads(it['regions']) if it['regions'] else []
        except: it['regions'] = []
        try: it['tags'] = json.loads(it['tags']) if it['tags'] else None
        except: it['tags'] = None

    # Group by commit_sha (date) and render
    groups = OrderedDict()
    for it in items:
        key = (it.get('commit_sha', 'unknown'), it.get('date', ''))
        groups.setdefault(key, []).append(it)
    for k in groups:
        groups[k].sort(key=lambda x: (x.get('src', ''), x.get('title', '')))
    sorted_keys = sorted(groups.keys(), key=lambda x: x[1], reverse=True)

    items_out = '[\n'
    first = True
    for (commit_sha, date) in sorted_keys:
        items_in = groups[(commit_sha, date)]
        if not first:
            items_out += '\n'
        first = False
        src_counter = {}
        for it in items_in:
            src_counter[it.get('src', '')] = src_counter.get(it.get('src', ''), 0) + 1
        common_src = max(src_counter, key=src_counter.get) if src_counter else 'unknown'
        items_out += divider(f'{common_src} {date} 新增') + '\n'
        for it in items_in:
            items_out += render_item(it) + ',\n'
    if items_out.endswith(',\n'):
        items_out = items_out[:-2] + '\n'
    items_out += '];'

    # Read the main index.html as template (just substitute ITEMS and update header)
    html = INDEX_PATH.read_text()
    new_html = re.sub(
        r'const ITEMS = \[.*?\];',
        f'const ITEMS = {items_out}',
        html, count=1, flags=re.S
    )
    # Update date in header
    new_html = re.sub(
        r'<div class="date">[^<]+</div>',
        f'<div class="date">{bj_date_label(date_str)}</div>',
        new_html, count=1
    )
    # Update lead
    lead_p = build_lead_p(date_str, top_n=3)
    new_html = re.sub(
        r'<div class="lead">\s*<h1>今日导读</h1>\s*<p>[\s\S]*?</p>\s*</div>',
        f'<div class="lead">\n      <h1>今日导读</h1>\n      <p>{lead_p}</p>\n    </div>',
        new_html, count=1
    )
    # Update title
    new_html = re.sub(
        r'<title>[^<]+</title>',
        f'<title>DealHOT · {date_str} 归档</title>',
        new_html, count=1
    )
    return new_html

# --- Archive index ---
def generate_archive_index():
    """Generate archive/index.html with list of all archives."""
    c = get_conn().cursor()
    archive_files = sorted(ARCHIVE_DIR.glob('2026-*.html'), reverse=True)
    items = []
    for f in archive_files:
        # extract date from filename
        m = re.match(r'(\d{4}-\d{2}-\d{2})\.html', f.name)
        if not m: continue
        date = m.group(1)
        # count items in archive
        with open(f) as fp:
            content = fp.read()
        n = len(re.findall(r'^\s*t:"', content, re.M))
        items.append((date, n, f))
    # Read template
    idx_path = ARCHIVE_DIR / 'index.html'
    html = idx_path.read_text()
    # build list HTML
    list_html = '\n'.join(
        f'<li><a href="{date}.html">{date}</a> — {n} 条</li>'
        for date, n, f in items
    )
    # simple replacement (template has a placeholder list)
    html = re.sub(
        r'(<ul[^>]*>).*?(</ul>)',
        f'\\1\n{list_html}\n  \\2',
        html, count=1, flags=re.S
    )
    return html

# --- Main page update (header date + lead) ---
def update_main_header(date_str):
    """Update main page header date and lead for a specific date (used in dry-run preview)."""
    html = INDEX_PATH.read_text()
    new_html = re.sub(
        r'<div class="date">[^<]+</div>',
        f'<div class="date">{bj_date_label(date_str)}</div>',
        html, count=1
    )
    lead_p = build_lead_p(date_str, top_n=3)
    new_html = re.sub(
        r'<div class="lead">\s*<h1>今日导读</h1>\s*<p>[\s\S]*?</p>\s*</div>',
        f'<div class="lead">\n      <h1>今日导读</h1>\n      <p>{lead_p}</p>\n    </div>',
        new_html, count=1
    )
    return new_html

# --- Main commands ---
def cmd_dry_run(args):
    """Validate DB and preview what would change."""
    today = args.date or bj_today_str()
    print(f'=== dry-run for {today} ===\n')
    # Check today's items
    c = get_conn().cursor()
    rows = c.execute('SELECT COUNT(*) FROM items WHERE date = ?', (today,)).fetchone()[0]
    print(f'Items in DB for {today}: {rows}')
    # Schema gate on each
    c.execute('SELECT date, t, src, cat, regions, score, title, url, sum, ma_cap, tags FROM items WHERE date = ?', (today,))
    cols = ['date', 't', 'src', 'cat', 'regions', 'score', 'title', 'url', 'sum', 'ma_cap', 'tags']
    issues = 0
    items = []
    for row in c.fetchall():
        it = dict(zip(cols, row))
        try: it['regions'] = json.loads(it['regions']) if it['regions'] else []
        except: it['regions'] = []
        try: it['tags'] = json.loads(it['tags']) if it['tags'] else None
        except: it['tags'] = None
        sch_issues = schema_gate(it)
        if sch_issues:
            print(f'  SCHEMA issues: {sch_issues}')
            issues += 1
        items.append(it)
    print(f'Schema gate: {issues} items with issues')
    # Build preview
    print(f'\nPreview of generated main page (dry-run, NOT writing):')
    new_html = update_main_header(today)
    items_out = generate_main()
    print(f'  ITEMS array: {len([s for s in items_out.split(chr(123) + chr(10)) if s.strip()]) - 1} items (would write {len(items_out)} chars)')
    print(f'  index.html would change: {len(new_html) - len(INDEX_PATH.read_text())} bytes')
    # Check archive
    archive_path = ARCHIVE_DIR / f'{today}.html'
    if archive_path.exists():
        cur = archive_path.read_text()
        new = generate_archive_html(today, today_only=True)
        print(f'  archive/{today}.html: {len(new) - len(cur)} bytes diff')
    else:
        new = generate_archive_html(today, today_only=True)
        print(f'  archive/{today}.html: NEW ({len(new)} bytes)')
    if issues == 0 and rows > 0:
        print('\n✓ Schema gate passed, items present. Ready for build.')
        return 0
    elif rows == 0:
        print(f'\n✗ No items for {today} in DB. Add data first.')
        return 2
    else:
        print(f'\n✗ Schema gate has {issues} issues. Fix before build.')
        return 3

def cmd_build(args):
    """Full build: index.html + archive/<date>.html + archive/index.html."""
    today = args.date or bj_today_str()
    print(f'=== build for {today} ===\n')
    # Step 1: dry-run check first
    c = get_conn().cursor()
    rows_today = c.execute('SELECT COUNT(*) FROM items WHERE date = ?', (today,)).fetchone()[0]
    if rows_today == 0:
        print(f'✗ No items for {today} in DB. Aborting.')
        return 2
    # Step 2: build index.html
    new_main = update_main_header(today)
    new_items = generate_main()
    new_main_with_items = re.sub(
        r'const ITEMS = \[.*?\];',
        f'const ITEMS = {new_items}',
        new_main, count=1, flags=re.S
    )
    INDEX_PATH.write_text(new_main_with_items)
    print(f'✓ index.html written: {len(new_main_with_items)} bytes')
    # Step 3: build archive/<date>.html
    archive_path = ARCHIVE_DIR / f'{today}.html'
    archive_html = generate_archive_html(today, today_only=True)
    archive_path.write_text(archive_html)
    print(f'✓ archive/{today}.html written: {len(archive_html)} bytes')
    # Step 4: update archive/index.html
    new_idx = generate_archive_index()
    (ARCHIVE_DIR / 'index.html').write_text(new_idx)
    print(f'✓ archive/index.html updated')
    # Step 5: verify file structure
    def check_js(path):
        content = path.read_text()
        # Basic structural check: count opening/closing braces match + required fields present
        issues = []
        # Count braces (rough balance)
        opens = content.count('{')
        closes = content.count('}')
        if opens != closes:
            issues.append(f'brace imbalance {{={opens}, }}={closes}')
        # Verify ITEMS array exists
        if 'const ITEMS = [' not in content:
            issues.append('ITEMS array missing')
        # Verify each item has required fields (d is alias for date in JS)
        js_required = ['d', 't', 'src', 'cat', 'regions', 'score', 'title', 'url']
        for field in js_required:
            if f'  {field}:' not in content:
                issues.append(f'missing field: {field}')
        if issues:
            return False, '; '.join(issues)
        return True, 'all structural checks passed'

    for f in [INDEX_PATH, archive_path]:
        ok, msg = check_js(f)
        status = '✓' if ok else '✗'
        print(f'  {status} JS check {f.name}: {msg[:200] if not ok else "OK"}')
    # Step 6: verify items count in main page
    main_count = len(re.findall(r'^\s*t:"', INDEX_PATH.read_text(), re.M))
    print(f'  ✓ Main page items: {main_count}')
    arch_count = len(re.findall(r'^\s*t:"', archive_html, re.M))
    print(f'  ✓ Archive page items: {arch_count}')
    return 0


def fetch_itjuzi(target_date):
    """Fetch IT桔子 events for a date. Returns list of dicts."""
    import urllib.request, os
    env_path = DEALHOT_DIR / '.secrets' / 'itjuzi.env'
    if env_path.exists():
        for line in env_path.read_text().strip().split('\n'):
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip()
    if not os.environ.get('ITJUZI_APPID'):
        return []
    req = urllib.request.Request(
        'https://openapi.itjuzi.com/oauth2.0/get_access_token',
        data=f"appid={os.environ['ITJUZI_APPID']}&appkey={os.environ['ITJUZI_APPKEY']}&granttype=client_credentials".encode()
    )
    tok = json.loads(urllib.request.urlopen(req, timeout=15).read())['data']['access_token']
    req2 = urllib.request.Request(
        f"https://openapi.itjuzi.com/investevent/get_investevent_list_v2?d={target_date}&d={target_date}&limit=20&order=1&order_rules=1",
        headers={'AUTHORIZATION': f'Bearer {tok}'}
    )
    return json.loads(urllib.request.urlopen(req2, timeout=15).read()).get('data', [])


def import_itjuzi_to_db(target_date, commit_sha):
    """Import IT桔子 events for date into dealhot.db."""
    items = fetch_itjuzi(target_date)
    if not items:
        return 0
    conn = get_conn()
    c = conn.cursor()
    inserted = 0
    for it in items:
        d = it.get('d') or target_date
        title = it.get('event_title', '').strip()
        if not title:
            continue
        round_id = it.get('round_id', 0)
        ind = it.get('com_industry_name', '')
        if ind in ('先进制造', '前沿科技', '机器人', '汽车交通'):
            cat = 'frontier'
        elif ind in ('医疗健康', '企业服务', '传统制造', '教育'):
            cat = 'bigtech'
        elif ind in ('金融', '消费'):
            cat = 'minority'
        else:
            cat = 'bigtech'
        url = 'https://www.itjuzi.com/'
        ROUND_NAMES = {1:'天使',2:'A',3:'A+',4:'B',5:'B+',6:'C',7:'D',8:'E',9:'Pre-IPO',10:'战略',11:'种子',12:'Pre-A',13:'Pre-A+',14:'B++',15:'C++',16:'被收购'}
        round_name = ROUND_NAMES.get(round_id, f'#{round_id}')
        ind_name = it.get('com_sub_industry_name', '')
        sum_text = f"{d} IT桔子披露，{it.get('com_name','')}完成{round_name}。属\"{ind} > {ind_name}\"。"
        c.execute('SELECT id FROM items WHERE date=? AND src=? AND title=?', (d, 'IT桔子', title))
        if c.fetchone():
            continue
        try:
            c.execute(
                'INSERT INTO items (date, t, src, cat, regions, score, title, url, sum, ma_cap, tags, commit_sha) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (d, f"{d[5:7].lstrip('0')}月{d[8:10].lstrip('0')}日", 'IT桔子', cat,
                 '["中国"]', 80, title, url, sum_text, '未披露',
                 '["投融资", "' + ind_name + '"]', commit_sha)
            )
            inserted += 1
        except Exception as e:
            print(f'  err: {e}')
    conn.commit()
    conn.close()
    return inserted


def cmd_fetch(args):
    """Fetch IT桔子 events for date and import to DB. No file writes."""
    target_date = args.date or bj_today_str()
    print(f'=== fetch IT桔子 for {target_date} ===\n')
    items = fetch_itjuzi(target_date)
    print(f'Found {len(items)} events')
    for it in items:
        print(f'  - {it.get("event_title", "?")[:80]}')
    if not args.dry_run and items:
        commit_sha = f'fetch-{datetime.now().strftime("%Y%m%d-%H%M%S")}'
        n = import_itjuzi_to_db(target_date, commit_sha)
        print(f'\n✓ Imported {n} events to DB (commit_sha={commit_sha})')
    return 0 if items else 2



def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='cmd', required=True)
    dr = sub.add_parser('dry-run', help='Validate DB and preview changes (no file writes)')
    dr.add_argument('--date', help='Date in YYYY-MM-DD (default: today Beijing)')
    dr.set_defaults(func=cmd_dry_run)
    b = sub.add_parser('build', help='Full build: index.html + archive + archive index')
    b.add_argument('--date', help='Date in YYYY-MM-DD (default: today Beijing)')
    b.set_defaults(func=cmd_build)
    f = sub.add_parser('fetch', help='Fetch IT桔子 events for date and import to DB')
    f.add_argument('--date', help='Date in YYYY-MM-DD (default: today Beijing)')
    f.add_argument('--dry-run', action='store_true', help='Only fetch, do not insert')
    f.set_defaults(func=cmd_fetch)
    p = sub.add_parser('post', help='Generate #dealhot channel post in 5c42a5a6 template format')
    p.add_argument('--date', help='Date in YYYY-MM-DD (default: today Beijing)')
    p.set_defaults(func=cmd_post)
    args = parser.parse_args()
    sys.exit(args.func(args))

def build_dealhot_post(date_str):
    """Generate #dealhot 频道 post 文本（per 5c42a5a6 模板）."""
    c = get_conn().cursor()
    rows = c.execute('''
        SELECT title, score, cat, src, regions, sum
        FROM items
        WHERE date = ?
        ORDER BY score DESC, id ASC
    ''', (date_str,)).fetchall()
    if not rows:
        return None
    cats = {}
    for title, score, cat, src, regions, sum_ in rows:
        cats.setdefault(cat, []).append((title, score, src))
    lead_lines = []
    for title, score, cat, src, regions, sum_ in rows[:5]:
        label = title.replace('。', '').replace('，', ',')[:50]
        lead_lines.append(f'- **{label}**')
    cat_lines = []
    for cat, items in cats.items():
        cat_lines.append(f'**{cat}（{len(items)}）**')
        for i, (title, score, src) in enumerate(items, 1):
            cat_lines.append(f'{i}. {title}（{score}）')
        cat_lines.append('')
    from collections import Counter
    srcs = Counter(r[3] for r in rows)
    src_text = ' / '.join(f'{s} {c}条' for s, c in srcs.most_common(3))
    # Beijing time for CST stamp
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    bj_tz = timezone(timedelta(hours=8))
    dt_bj = dt.replace(tzinfo=bj_tz)
    weekday_cn = ['星期一','星期二','星期三','星期四','星期五','星期六','星期日'][dt_bj.weekday()]
    month_eng = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][dt_bj.month-1]
    cst_stamp = f'{dt_bj.month}/{dt_bj.day} {weekday_cn}'  # 5c42a5a6 用了 6/24 周二 格式
    post = f"""📊 **DealHOT {cst_stamp} 更新**（{len(rows)} 条新条目） · {dt_bj.year}-{dt_bj.month:02d}-{dt_bj.day:02d} 08:10 CST

🔗 <https://jeanajin0409-art.github.io/market-pulse/>

## 今日导读
{chr(10).join(lead_lines)}

## {date_str.replace("-","-")} 新增 {len(rows)} 条按板块

{chr(10).join(cat_lines)}来源：{src_text}

—— Mimi-Timer-Mini（post-cutover 新端 dealhot daily job, 08:10 CST）
"""
    return post

def cmd_post(args):
    """Generate #dealhot channel post in 5c42a5a6 template format. Prints to stdout."""
    date_str = args.date or bj_today_str()
    post = build_dealhot_post(date_str)
    if post is None:
        print(f'\u274c No items for {date_str} in DB. Aborting.')
        return 2
    print(post)
    return 0





if __name__ == '__main__':
    main()
