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
import os
import re
import sqlite3
import subprocess
import sys
import urllib.parse
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

# Paths —— 可用环境变量覆盖，方便在副本上跑样稿而不动生产 DB / 生产文件
#   DEALHOT_DB      DB 路径（默认 <repo>/dealhot.db）
#   DEALHOT_INDEX   index.html 输出路径
#   DEALHOT_ARCHIVE archive 目录
DEALHOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get('DEALHOT_DB', DEALHOT_DIR / 'dealhot.db'))
INDEX_PATH = Path(os.environ.get('DEALHOT_INDEX', DEALHOT_DIR / 'index.html'))
ARCHIVE_DIR = Path(os.environ.get('DEALHOT_ARCHIVE', DEALHOT_DIR / 'archive'))

# --- DB helpers ---
def get_conn():
    return sqlite3.connect(DB_PATH)


def atomic_write(path, content):
    """写临时文件再 os.replace —— 保证目标文件要么是旧内容、要么是新内容，不会半写。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp-build')
    tmp.write_text(content)
    os.replace(tmp, path)

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
    # why (推荐理由) 为空即失败 —— 发布硬闸门同样要求 why 非空
    if not item.get('why'):
        issues.append('why (推荐理由) missing')
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
    # 推荐理由 (投资信号 / 行业拐点 / 持仓相关 / 风险信号)
    if it.get("why"):
            parts.append(f'  why:{js_escape(it["why"])}')
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
        SELECT date, t, src, cat, regions, score, title, url, sum, why, ma_cap, tags, commit_sha
        FROM items
        ORDER BY date DESC, score DESC, id ASC
    ''').fetchall()
    cols = ['date', 't', 'src', 'cat', 'regions', 'score', 'title', 'url', 'sum', 'why', 'ma_cap', 'tags', 'commit_sha']
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
            SELECT date, t, src, cat, regions, score, title, url, sum, why, ma_cap, tags, commit_sha
            FROM items
            WHERE date = ?
            ORDER BY score DESC, id ASC
        ''', (date_str,)).fetchall()
    else:
        rows = c.execute('''
            SELECT date, t, src, cat, regions, score, title, url, sum, why, ma_cap, tags, commit_sha
            FROM items
            ORDER BY date DESC, score DESC, id ASC
        ''').fetchall()
    cols = ['date', 't', 'src', 'cat', 'regions', 'score', 'title', 'url', 'sum', 'why', 'ma_cap', 'tags', 'commit_sha']
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
def generate_archive_index(extra_date=None, extra_count=0):
    """Generate archive/index.html with list of all archives.

    extra_date/extra_count：正在构建但尚未落盘的那一天（落盘前文件还不存在），
    否则往期索引会漏掉当天。
    """
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
    if extra_date and not any(d == extra_date for d, _n, _f in items):
        items.append((extra_date, extra_count, None))
        items.sort(key=lambda x: x[0], reverse=True)
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
    c.execute('SELECT date, t, src, cat, regions, score, title, url, sum, why, ma_cap, tags FROM items WHERE date = ?', (today,))
    cols = ['date', 't', 'src', 'cat', 'regions', 'score', 'title', 'url', 'sum', 'why', 'ma_cap', 'tags']
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

# --- 发布硬闸门（fail-closed：任一不过 → 停止 publish，不写任何文件） ---
GATE_HOMEPAGE_HOSTS = {'www.itjuzi.com', 'itjuzi.com', 'aihot.virxact.com', 'aihot.news'}
GATE_BIGTECH_FORBIDDEN_INDUSTRIES = {'医疗健康', '企业服务', '传统制造', '教育', '金融', '消费'}
GATE_UNRESOLVED_ROUND = re.compile(r'#\d+')
GATE_CDATA = re.compile(r'<!\[CDATA\[', re.I)


def publish_gate(date_str):
    """返回 (errors, stats)。errors 非空 = 禁止 publish（不写任何文件）。"""
    errors = []
    c = get_conn().cursor()
    rows = c.execute('''SELECT id, title, url, sum, why, cat, score, raw_id
                         FROM items WHERE date = ?''', (date_str,)).fetchall()
    if not rows:
        return [f'当日没有任何条目（{date_str}）'], {}

    raw_industry = {}
    try:
        for rid, industry in c.execute('SELECT id, category FROM raw_candidates'):
            raw_industry[rid] = industry
    except sqlite3.Error:
        pass

    score_counts = {}
    for r in rows:
        score_counts[r[6]] = score_counts.get(r[6], 0) + 1
    top_score, top_n = max(score_counts.items(), key=lambda kv: kv[1])

    for (iid, title, url, sum_, why, cat, score, raw_id) in rows:
        t = (title or '').strip()
        u = (url or '').strip()
        if not t:
            errors.append(f'#{iid} title 为空')
            continue
        if GATE_CDATA.search(t) or GATE_CDATA.search(sum_ or ''):
            errors.append(f'#{iid} 含 CDATA：{t[:30]}')
        if GATE_UNRESOLVED_ROUND.search(t) or GATE_UNRESOLVED_ROUND.search(sum_ or ''):
            errors.append(f'#{iid} 轮次未解析：{t[:30]}')
        if t.startswith('AI HOT 日报'):
            errors.append(f'#{iid} daily RSS 聚合标题当条目：{t[:30]}')
        if not u:
            errors.append(f'#{iid} 缺 url：{t[:30]}')
        elif urllib.parse.urlparse(u).path in ('', '/'):
            errors.append(f'#{iid} 首页 URL（须具体原文链接）：{t[:30]}')
        if not (sum_ or '').strip():
            errors.append(f'#{iid} sum 为空：{t[:30]}')
        if not (why or '').strip():
            errors.append(f'#{iid} why 为空：{t[:30]}')
        if cat == 'bigtech':
            ind = raw_industry.get(raw_id)
            if ind and ind in GATE_BIGTECH_FORBIDDEN_INDUSTRIES:
                errors.append(f'#{iid} bigtech 错分（行业「{ind}」不是大厂）：{t[:30]}')

    if len(rows) >= 3 and top_n == len(rows):
        errors.append(f'批量同分：{len(rows)} 条全部 {top_score} 分')
    elif len(rows) >= 5 and top_n / len(rows) > 0.6:
        errors.append(f'批量同分：{top_n}/{len(rows)} 条同为 {top_score} 分')

    since = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=7)).strftime('%Y-%m-%d')
    dup = c.execute('''SELECT title, COUNT(DISTINCT date) n FROM items
                        WHERE date >= ? AND date <= ? GROUP BY title HAVING n > 1''',
                     (since, date_str)).fetchall()
    for title, n in dup:
        errors.append(f'跨日重复（{n} 天）：{(title or "")[:36]}')

    # --- 统计（raw/selected/published、来源、板块、score 分布、重复拦截、字段失败） ---
    raw = 0
    selected = 0
    dup_blocked = 0
    try:
        raw = c.execute('SELECT COUNT(*) FROM raw_candidates WHERE event_date = ?',
                        (date_str,)).fetchone()[0]
        selected = c.execute("SELECT COUNT(*) FROM raw_candidates WHERE event_date = ? AND status = 'selected'",
                             (date_str,)).fetchone()[0]
        pub_titles = {r[1] for r in rows}
        pub_urls = {r[2] for r in rows}
        dup_blocked = c.execute('''SELECT COUNT(*) FROM raw_candidates
                                   WHERE event_date = ? AND status = 'candidate'
                                     AND (title IN (%s) OR url IN (%s))''' % (
            ','.join('?' * len(pub_titles)), ','.join('?' * len(pub_urls))),
            (date_str, *pub_titles, *pub_urls)).fetchone()[0]
    except sqlite3.Error:
        pass

    srcs = c.execute('SELECT src, COUNT(*) FROM items WHERE date = ? GROUP BY src ORDER BY 2 DESC',
                     (date_str,)).fetchall()
    cats = c.execute('SELECT cat, COUNT(*) FROM items WHERE date = ? GROUP BY cat ORDER BY 2 DESC',
                     (date_str,)).fetchall()
    field_fail = sum(1 for r in rows if not (r[3] or '').strip() or not (r[4] or '').strip()
                     or not (r[2] or '').strip())
    stats = {
        'raw': raw, 'selected': selected, 'published': len(rows),
        'sources': srcs, 'cats': cats, 'scores': score_counts,
        'dup_blocked': dup_blocked, 'field_fail': field_fail,
    }
    return errors, stats


def cmd_build(args):
    """Full build: index.html + archive/<date>.html + archive/index.html."""
    today = args.date or bj_today_str()
    print(f'=== build for {today} ===\n')
    # Step 0: 发布硬闸门（fail-closed）
    gate_errors, gate_stats = publish_gate(today)
    if gate_errors:
        print(f'✗ 发布闸门未通过（{len(gate_errors)} 项），已停止，不写任何文件：')
        for e in gate_errors:
            print(f'    - {e}')
        return 3
    if gate_stats:
        print('  ✓ 发布闸门通过')
        print(f"    raw {gate_stats['raw']} / selected {gate_stats['selected']} / published {gate_stats['published']}"
              f" · 来源 {dict(gate_stats['sources'])}")
        print(f"    板块 {dict(gate_stats['cats'])} · score {gate_stats['scores']}"
              f" · 重复拦截 {gate_stats['dup_blocked']} · 字段失败 {gate_stats['field_fail']}")
    # Step 1: dry-run check first
    c = get_conn().cursor()
    rows_today = c.execute('SELECT COUNT(*) FROM items WHERE date = ?', (today,)).fetchone()[0]
    if rows_today == 0:
        print(f'✗ No items for {today} in DB. Aborting.')
        return 2
    # Step 2: 在内存里渲染三份产物（先不落盘）
    new_main = update_main_header(today)
    new_items = generate_main()
    new_main_with_items = re.sub(
        r'const ITEMS = \[.*?\];',
        f'const ITEMS = {new_items}',
        new_main, count=1, flags=re.S
    )
    archive_path = ARCHIVE_DIR / f'{today}.html'
    archive_html = generate_archive_html(today, today_only=True)
    new_idx = generate_archive_index(
        extra_date=today,
        extra_count=len(re.findall(r'^\s*t:"', archive_html, re.M)))

    # Step 3: 全部校验通过后才允许写盘（校验基于内存内容，不依赖已写文件）
    def check_js_content(content, require_items=True):
        issues = []
        opens = content.count('{')
        closes = content.count('}')
        if opens != closes:
            issues.append(f'brace imbalance {{={opens}, }}={closes}')
        if require_items:
            if 'const ITEMS = [' not in content:
                issues.append('ITEMS array missing')
            js_required = ['d', 't', 'src', 'cat', 'regions', 'score', 'title', 'url', 'why']
            for field in js_required:
                if f'  {field}:' not in content:
                    issues.append(f'missing field: {field}')
        else:
            # 往期索引是链接列表页，没有 ITEMS 数组，只校验当天链接存在
            if f'{today}.html' not in content:
                issues.append(f'archive index 缺当天链接 {today}.html')
        if issues:
            return False, '; '.join(issues)
        return True, 'all structural checks passed'

    checks = [('index.html', new_main_with_items, True),
              (f'archive/{today}.html', archive_html, True),
              ('archive/index.html', new_idx, False)]
    failed = False
    for name, content, require_items in checks:
        ok, msg = check_js_content(content, require_items)
        status = '✓' if ok else '✗'
        print(f'  {status} JS check {name}: {msg[:200] if not ok else "OK"}')
        if not ok:
            failed = True
    if failed:
        print('✗ 结构校验未通过，已停止：三个目标文件保持原样（byte-unchanged），未写入任何内容。')
        return 4

    # Step 4: 校验全部通过 → 原子替换（临时文件 + os.replace）
    atomic_write(INDEX_PATH, new_main_with_items)
    print(f'✓ index.html written: {len(new_main_with_items)} bytes')
    atomic_write(archive_path, archive_html)
    print(f'✓ archive/{today}.html written: {len(archive_html)} bytes')
    atomic_write(ARCHIVE_DIR / 'index.html', new_idx)
    print(f'✓ archive/index.html updated')
    # Step 5: 统计
    main_count = len(re.findall(r'^\s*t:"', new_main_with_items, re.M))
    print(f'  ✓ Main page items: {main_count}')
    arch_count = len(re.findall(r'^\s*t:"', archive_html, re.M))
    print(f'  ✓ Archive page items: {arch_count}')
    return 0


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='cmd', required=True)
    dr = sub.add_parser('dry-run', help='Validate DB and preview changes (no file writes)')
    dr.add_argument('--date', help='Date in YYYY-MM-DD (default: today Beijing)')
    dr.set_defaults(func=cmd_dry_run)
    b = sub.add_parser('build', help='Full build: index.html + archive + archive index')
    b.add_argument('--date', help='Date in YYYY-MM-DD (default: today Beijing)')
    b.set_defaults(func=cmd_build)
    # 注：旧的 `fetch` 子命令已删除 —— 它会直接写 items（错误日期参数 + 首页 URL + score 恒 80 +
    # 分类错位），是绕过新架构的后门。抓取一律走 scripts/pipeline.py fetch。
    p = sub.add_parser('post', help='Generate #dealhot channel post in 5c42a5a6 template format')
    p.add_argument('--date', help='Date in YYYY-MM-DD (default: today Beijing)')
    p.set_defaults(func=cmd_post)
    args = parser.parse_args()
    sys.exit(args.func(args))

def build_dealhot_post(date_str):
    """Generate #dealhot 频道 post 文本（per 5c42a5a6 模板）."""
    c = get_conn().cursor()
    rows = c.execute('''
        SELECT title, score, cat, src, regions, sum, why
        FROM items
        WHERE date = ?
        ORDER BY score DESC, id ASC
    ''', (date_str,)).fetchall()
    if not rows:
        return None
    cats = {}
    for title, score, cat, src, regions, sum_, why in rows:
        cats.setdefault(cat, []).append((title, score, src, why or ''))
    lead_lines = []
    for title, score, cat, src, regions, sum_, why in rows[:5]:
        label = title.replace('。', '').replace('，', ',')[:50]
        lead_lines.append(f'- **{label}**')
    cat_lines = []
    for cat, items in cats.items():
        cat_lines.append(f'**{cat}（{len(items)}）**')
        for i, (title, score, src, why) in enumerate(items, 1):
            cat_lines.append(f'{i}. {title}（{score}）')
            reason = re.sub(r'\s+', ' ', why).strip()
            if reason:
                cat_lines.append(f'   — {reason[:60]}')
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
    post = f"""📊 **DealHOT {cst_stamp} 更新**（{len(rows)} 条新条目） · {dt_bj.year}-{dt_bj.month:02d}-{dt_bj.day:02d} 08:10 北京时间 (UTC+8)

🔗 <https://jeanajin0409-art.github.io/market-pulse/>

## 今日导读
{chr(10).join(lead_lines)}

## {date_str.replace("-","-")} 新增 {len(rows)} 条按板块

{chr(10).join(cat_lines)}来源：{src_text}

—— Mimi-Timer-Mini（dealhot daily job, 08:10 北京时间 (UTC+8)）
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
