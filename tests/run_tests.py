#!/usr/bin/env python3
"""tests/run_tests.py · dealhot 新链路回归测试（无第三方依赖）

覆盖 task #1 验收要求的场景：
  1. 整批原子拒绝（一条不合格 → 0 条落库、非零退出）
  2. raw 引用不存在 / 日期错 / 状态不是 candidate
  3. 7 日去重对称窗口（title / url / content_hash，未来侧也要拦）
  4. 旧 build.py fetch 入口已禁用
  5. 校验失败零写入（三个目标文件 byte-unchanged、非零退出）
  6. 历史 replace 原子性（失败旧数据不动 / 成功一次替换）
  7. why 出现在 main + archive + post 三处
  8. UTC → 北京时间（UTC+8）事件日期换算
  9. 批内重复被拒（用**生产** hash 算法构造，不用手填 hash）
 10. replace 缺日期 / 日期越界 / 日期重复被拒
 11. post 也走发布闸门（坏数据 → 非零且不输出正文）
 12. 历史构建用 --archive-only，不动主页
 13. node --check 真语法检查（合法 '{' 不误杀，真语法错被拦）

用法：python3 tests/run_tests.py
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'scripts'))
import pipeline  # noqa: E402  —— 复用生产 hash 算法，测试不许手填 hash

PY = sys.executable
PIPE = str(REPO / 'scripts' / 'pipeline.py')
BUILD = str(REPO / 'scripts' / 'build.py')

ITEMS_DDL = '''
CREATE TABLE items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL, t TEXT NOT NULL, src TEXT NOT NULL, cat TEXT NOT NULL,
  regions TEXT NOT NULL, score INTEGER NOT NULL, title TEXT NOT NULL, url TEXT,
  sum TEXT, ma_cap TEXT, tags TEXT, commit_sha TEXT, why TEXT,
  UNIQUE(date, src, title)
);
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
'''


# ---------------------------------------------------------------- harness
def run(args, env=None):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([PY] + args, capture_output=True, text=True, env=e, cwd=str(REPO))


class Fixture:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='dealhot-test-'))
        self.db = self.tmp / 't.db'
        self.index = self.tmp / 'index.html'
        self.archive = self.tmp / 'archive'
        self.archive.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db)
        conn.executescript(ITEMS_DDL)
        conn.commit()
        conn.close()
        self.index.write_text((REPO / 'index.html').read_text())
        (self.archive / 'index.html').write_text((REPO / 'archive' / 'index.html').read_text())
        self.env = {
            'DEALHOT_DB': str(self.db),
            'DEALHOT_INDEX': str(self.index),
            'DEALHOT_ARCHIVE': str(self.archive),
        }

    def conn(self):
        return sqlite3.connect(self.db)

    def migrate(self):
        r = run([PIPE, 'migrate'], self.env)
        assert r.returncode == 0, f'migrate 失败: {r.stdout}{r.stderr}'

    def seed_item(self, date, title, url, why='推荐理由内容示例文本', score=80, cat='minority',
                  src='IT桔子', raw_id=None, sum_='摘要内容示例，长度足够通过字段闸门校验。'):
        conn = self.conn()
        conn.execute(
            'INSERT INTO items (date, t, src, cat, regions, score, title, url, sum, why, ma_cap, tags, commit_sha, raw_id)'
            ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (date, f'{int(date[5:7])}月{int(date[8:10])}日', src, cat, '["中国"]', score, title, url,
             sum_, why, '', '[]', 'seed', raw_id))
        conn.commit()
        conn.close()

    def seed_raw(self, date, title, url, content_hash=None, source_id='sid-1', source='IT桔子',
                 status='candidate', subject='', amount='', round_name='', parties='', event_hash=None):
        """用**生产算法**算 hash，不手填。"""
        party_list = parties.split('、') if parties else []
        if event_hash is None:
            event_hash = pipeline.event_hash(subject, amount, round_name, party_list, url, True)
        if content_hash is None:
            content_hash = pipeline.content_hash(source, title, amount, round_name, party_list)
        conn = self.conn()
        conn.execute('''
            INSERT INTO raw_candidates
              (source, source_id, url, event_date, published_at, fetched_at, title, summary, category,
               score, round_name, amount, parties, content_hash, event_hash, subject, raw_json, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (source, source_id, url, date, '', '2026-01-01T00:00:00+00:00', title, '', '先进制造',
              None, round_name, amount, parties, content_hash, event_hash, subject, '{}', status))
        conn.commit()
        conn.close()

    def items_count(self, date=None):
        conn = self.conn()
        n = (conn.execute('SELECT COUNT(*) FROM items WHERE date=?', (date,)).fetchone()[0]
             if date else conn.execute('SELECT COUNT(*) FROM items').fetchone()[0])
        conn.close()
        return n

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def write_json(path, payload):
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return str(path)


def item(url, title='候选标题', score=88, cat='minority', why='这是一条足够长的推荐理由示例文本',
         sum_='这是一条足够长的摘要内容，长度足够通过字段闸门校验。', **extra):
    d = {'title': title, 'url': url, 'cat': cat, 'regions': ['中国'], 'score': score,
         'sum': sum_, 'why': why, 'tags': ['投融资']}
    d.update(extra)
    return d


# ---------------------------------------------------------------- tests
def t1_batch_atomic_reject(f):
    """整批原子拒绝：1 条合法 + 1 条非法 → 0 条落库，rc != 0，拒绝原因持久化"""
    f.migrate()
    f.seed_raw('2026-07-24', '候选一', 'https://example.com/a')
    f.seed_raw('2026-07-24', '候选二', 'https://example.com/b')
    payload = {'date': '2026-07-24', 'items': [
        dict(item('https://example.com/a', title='合法条目'), raw_id=1),
        dict(item('https://www.itjuzi.com/', title='非法条目'), raw_id=2),
    ]}
    p = write_json(f.tmp / 'bad.json', payload)
    r = run([PIPE, 'accept', '--date', '2026-07-24', '--file', p], f.env)
    assert r.returncode != 0, '非法批次应当非零退出'
    assert f.items_count('2026-07-24') == 0, '整批拒绝时不应有任何条目落库'
    conn = f.conn()
    rejected = conn.execute("SELECT COUNT(*) FROM raw_candidates WHERE status='rejected'").fetchone()[0]
    note = conn.execute("SELECT note FROM raw_candidates WHERE status='rejected'").fetchone()[0]
    conn.close()
    assert rejected == 1, '拒绝原因应持久化到 raw_candidates'
    assert '首页 URL' in note, f'拒绝原因不对: {note}'


def t2_raw_link_validation(f):
    """raw 引用不存在 / 日期错 / 状态不是 candidate → 全部拒绝"""
    f.migrate()
    f.seed_raw('2026-07-24', '候选', 'https://example.com/a')
    f.seed_raw('2026-07-25', '别的日期', 'https://example.com/b')
    p = write_json(f.tmp / 'a.json', {'date': '2026-07-24', 'items': [dict(item('https://example.com/a'), raw_id=9999)]})
    r = run([PIPE, 'accept', '--date', '2026-07-24', '--file', p], f.env)
    assert r.returncode != 0 and '不存在' in r.stdout, f'raw_id 不存在应被拒: {r.stdout}'
    conn = f.conn()
    rid2 = conn.execute("SELECT id FROM raw_candidates WHERE event_date='2026-07-25'").fetchone()[0]
    conn.close()
    p = write_json(f.tmp / 'b.json', {'date': '2026-07-24', 'items': [dict(item('https://example.com/a'), raw_id=rid2)]})
    r = run([PIPE, 'accept', '--date', '2026-07-24', '--file', p], f.env)
    assert r.returncode != 0 and '日期不符' in r.stdout, f'日期错应被拒: {r.stdout}'
    conn = f.conn()
    conn.execute("UPDATE raw_candidates SET status='selected' WHERE event_date='2026-07-24'")
    conn.commit()
    conn.close()
    p = write_json(f.tmp / 'c.json', {'date': '2026-07-24', 'items': [dict(item('https://example.com/a'), raw_id=1)]})
    r = run([PIPE, 'accept', '--date', '2026-07-24', '--file', p], f.env)
    assert r.returncode != 0 and '状态不是 candidate' in r.stdout, f'状态不对应被拒: {r.stdout}'
    assert f.items_count('2026-07-24') == 0


def t3_dedupe_symmetric(f):
    """7 日对称窗口：过去侧 / 未来侧 / title / url / content_hash 都要拦"""
    f.migrate()
    f.seed_raw('2026-07-24', '候选', 'https://example.com/new')
    it = dict(item('https://example.com/new', title='重复的标题'), raw_id=1)
    f.seed_item('2026-07-27', '重复的标题', 'https://example.com/other')
    p = write_json(f.tmp / 'a.json', {'date': '2026-07-24', 'items': [it]})
    r = run([PIPE, 'accept', '--date', '2026-07-24', '--file', p], f.env)
    assert r.returncode != 0 and '重复' in r.stdout, f'未来侧重复应被拦: {r.stdout}'
    run([PIPE, 'reset', '--date', '2026-07-24'], f.env)
    f.seed_item('2026-07-21', '别的标题', 'https://example.com/new')
    p = write_json(f.tmp / 'b.json', {'date': '2026-07-24', 'items': [dict(it, title='新标题')]})
    r = run([PIPE, 'accept', '--date', '2026-07-24', '--file', p], f.env)
    assert r.returncode != 0 and '重复' in r.stdout, f'过去侧 url 重复应被拦: {r.stdout}'
    conn = f.conn()
    conn.execute('DELETE FROM items')
    conn.commit()
    conn.close()
    run([PIPE, 'reset', '--date', '2026-07-24'], f.env)
    f.seed_item('2026-07-26', '同事件不同措辞', 'https://example.com/dup', raw_id=1)
    p = write_json(f.tmp / 'c.json', {'date': '2026-07-24', 'items': [dict(it, title='新标题')]})
    r = run([PIPE, 'accept', '--date', '2026-07-24', '--file', p], f.env)
    assert r.returncode != 0 and '重复' in r.stdout, f'content_hash 重复应被拦: {r.stdout}'
    assert f.items_count('2026-07-24') == 0


def t4_old_fetch_disabled(f):
    """旧 build.py fetch 入口必须不存在（避免绕过新架构）"""
    r = run([BUILD, 'fetch', '--date', '2026-07-24'], f.env)
    assert r.returncode != 0, 'build.py fetch 应当被禁用（非零退出）'
    combined = r.stdout + r.stderr
    assert 'pipeline.py' in combined or 'invalid choice' in combined or 'unrecognized' in combined, \
        f'报错应指向 pipeline.py: {combined}'
    src = (REPO / 'scripts' / 'build.py').read_text()
    assert 'def fetch_itjuzi' not in src, '旧 fetch_itjuzi 函数不应存在'
    assert 'def import_itjuzi_to_db' not in src, '旧 import_itjuzi_to_db 函数不应存在'
    assert "add_parser('fetch'" not in src, "旧 fetch 子命令不应存在"


def t5_zero_write_on_failure(f):
    """校验失败 → 三个目标文件 byte-unchanged、非零退出"""
    f.migrate()
    # 发布闸门不过（首页 URL）→ build 在写盘前就停 → 零写入
    f.seed_item('2026-07-24', '首页链接条目', 'https://www.itjuzi.com/', why='推荐理由内容示例文本')
    targets = [f.index, f.archive / 'index.html', f.archive / '2026-07-24.html']
    before = {p: (p.read_bytes() if p.exists() else None) for p in targets}
    r = run([BUILD, 'build', '--date', '2026-07-24'], f.env)
    assert r.returncode != 0, '校验失败应当非零退出'
    for p, b in before.items():
        now = p.read_bytes() if p.exists() else None
        assert now == b, f'{p.name} 不应被写入'


def t6_replace_atomic(f):
    """历史 replace：中途失败 → 旧数据原样不动；全部通过 → 一次替换"""
    f.migrate()
    for i in range(3):
        f.seed_item('2026-07-24', f'旧条目{i}', f'https://example.com/old{i}')
    f.seed_raw('2026-07-24', '新候选', 'https://example.com/new')
    good = dict(item('https://example.com/new', title='新条目'), raw_id=1)
    bad = dict(good, title='坏条目', url='https://www.itjuzi.com/')
    p = write_json(f.tmp / 'r.json', {'date': '2026-07-24', 'items': [good, bad]})
    r = run([PIPE, 'replace', '--from', '2026-07-24', '--to', '2026-07-24', '--files', p], f.env)
    assert r.returncode != 0, 'replace 失败应非零退出'
    assert f.items_count('2026-07-24') == 3, 'replace 失败时旧数据应原样不动'
    conn = f.conn()
    titles = [t[0] for t in conn.execute("SELECT title FROM items WHERE date='2026-07-24'")]
    conn.close()
    assert '旧条目0' in titles, '旧行应仍在'
    run([PIPE, 'reset', '--date', '2026-07-24'], f.env)
    p = write_json(f.tmp / 'r2.json', {'date': '2026-07-24', 'items': [good]})
    r = run([PIPE, 'replace', '--from', '2026-07-24', '--to', '2026-07-24', '--files', p], f.env)
    assert r.returncode == 0, f'replace 应成功: {r.stdout}{r.stderr}'
    assert f.items_count('2026-07-24') == 1, '旧行应被替换为新行'


def t7_why_in_all_outputs(f):
    """why 必须出现在 main + archive + post 三处"""
    f.migrate()
    f.seed_raw('2026-07-24', '候选', 'https://example.com/a')
    f.seed_item('2026-07-24', '带推荐理由的条目', 'https://example.com/a',
                why='这是唯一可识别的推荐理由标记XYZ', score=88)
    r = run([BUILD, 'build', '--date', '2026-07-24'], f.env)
    assert r.returncode == 0, f'build 应成功: {r.stdout}{r.stderr}'
    main = f.index.read_text()
    arch = (f.archive / '2026-07-24.html').read_text()
    assert '唯一可识别的推荐理由标记XYZ' in main, 'main 页面缺少 why'
    assert '唯一可识别的推荐理由标记XYZ' in arch, 'archive 页面缺少 why'
    assert '  why:' in main, 'main 页面缺少 why 字段'
    r = run([BUILD, 'post', '--date', '2026-07-24'], f.env)
    assert r.returncode == 0
    assert '唯一可识别的推荐理由标记XYZ' in r.stdout, 'post 缺少 why'


def t8_utc_to_beijing(f):
    """AIHOT publishedAt 是 UTC → 事件日期按北京时间 (UTC+8)"""
    f.migrate()
    payload = [
        {'id': 'a', 'title': '北京时间 07-27 07:30 的条目', 'url': 'https://example.com/a',
         'publishedAt': '2026-07-26T23:30:00.000Z', 'score': 80, 'category': 'ai', 'summary': ''},
        {'id': 'b', 'title': '北京时间 07-26 23:00 的条目', 'url': 'https://example.com/b',
         'publishedAt': '2026-07-26T15:00:00.000Z', 'score': 80, 'category': 'ai', 'summary': ''},
    ]
    p = write_json(f.tmp / 'aihot.json', payload)
    r = run([PIPE, 'import-aihot', '--date', '2026-07-27', '--file', p], f.env)
    assert r.returncode == 0, f'import-aihot 失败: {r.stdout}{r.stderr}'
    conn = f.conn()
    rows = dict(conn.execute("SELECT title, event_date FROM raw_candidates WHERE source='AIHOT'"))
    conn.close()
    assert rows.get('北京时间 07-27 07:30 的条目') == '2026-07-27', f"UTC 23:30 应换算成北京时间次日: {rows}"
    assert rows.get('北京时间 07-26 23:00 的条目') == '2026-07-26', f"UTC 15:00 应换算成北京时间当日: {rows}"


def t9_in_batch_duplicate_rejected(f):
    """批内重复被拒：两个**不同来源、不同标题/URL**但同一事件（生产 event_hash 算法）→ 只能进一条"""
    f.migrate()
    f.seed_raw('2026-07-24', '甲公司完成A轮融资', 'https://example.com/a1',
               source='IT桔子', source_id='sid-a', subject='甲公司', amount='1亿元',
               round_name='A轮', parties='乙基金')
    f.seed_raw('2026-07-24', '甲公司再获A轮融资', 'https://example.com/a2',
               source='另一来源', source_id='sid-b', subject='甲公司', amount='1亿元',
               round_name='A轮', parties='乙基金')
    conn = f.conn()
    h1, h2 = [r[0] for r in conn.execute("SELECT event_hash FROM raw_candidates ORDER BY id")]
    conn.close()
    assert h1 == h2, f'生产 event_hash 算法应给出相同指纹: {h1} vs {h2}'
    payload = {'date': '2026-07-24', 'items': [
        dict(item('https://example.com/a1', title='甲公司完成A轮融资'), raw_id=1),
        dict(item('https://example.com/a2', title='甲公司再获A轮融资'), raw_id=2),
    ]}
    p = write_json(f.tmp / 'dup.json', payload)
    r = run([PIPE, 'accept', '--date', '2026-07-24', '--file', p], f.env)
    assert r.returncode != 0, '批内重复必须整批拒绝'
    assert '批内重复' in r.stdout, f'应报批内重复: {r.stdout}'
    assert f.items_count('2026-07-24') == 0, '批内重复时不应有任何条目落库'


def t10_replace_range_integrity(f):
    """replace 范围完整性：缺日期 / 日期越界 / 日期重复 → 拒绝且不动旧数据"""
    f.migrate()
    for d in ('2026-07-24', '2026-07-25'):
        f.seed_item(d, f'旧条目-{d}', f'https://example.com/old-{d}')
    f.seed_raw('2026-07-24', '新候选', 'https://example.com/new')
    good = dict(item('https://example.com/new', title='新条目'), raw_id=1)
    p24 = write_json(f.tmp / 'd24.json', {'date': '2026-07-24', 'items': [good]})
    # ① 区间 4 天只给 1 个文件 → 拒绝，其余日期不能被删空
    r = run([PIPE, 'replace', '--from', '2026-07-24', '--to', '2026-07-27', '--files', p24], f.env)
    assert r.returncode != 0, '缺日期必须非零退出'
    assert '缺日期' in r.stdout, f'应报缺日期: {r.stdout}'
    assert f.items_count('2026-07-25') == 1, '缺日期时旧数据不能被删'
    # ② 单日区间 + 单文件 → 成功
    r = run([PIPE, 'replace', '--from', '2026-07-24', '--to', '2026-07-24', '--files', p24], f.env)
    assert r.returncode == 0, f'单日区间应成功: {r.stdout}{r.stderr}'
    # ③ 同一日期两个文件 → 拒绝
    p24b = write_json(f.tmp / 'd24b.json', {'date': '2026-07-24', 'items': [good]})
    r = run([PIPE, 'replace', '--from', '2026-07-24', '--to', '2026-07-24',
             '--files', f'{p24},{p24b}'], f.env)
    assert r.returncode != 0 and '重复' in r.stdout, f'日期重复必须拒绝: {r.stdout}'


def t11_post_respects_gate(f):
    """post 也必须过发布闸门：坏数据 → 非零退出且不输出 post 正文"""
    f.migrate()
    f.seed_item('2026-07-24', '首页链接条目', 'https://www.itjuzi.com/', why='推荐理由内容示例文本')
    r = run([BUILD, 'post', '--date', '2026-07-24'], f.env)
    assert r.returncode != 0, '坏数据时 post 必须非零退出'
    assert 'DealHOT' not in r.stdout, f'闸门不过时不得输出 post 正文: {r.stdout}'
    assert '发布闸门' in r.stdout, f'应说明是闸门拦截: {r.stdout}'


def t12_archive_only_keeps_homepage(f):
    """历史构建用 --archive-only → 主页文件 byte-unchanged"""
    f.migrate()
    f.seed_raw('2026-07-24', '候选', 'https://example.com/a')
    f.seed_item('2026-07-24', '条目', 'https://example.com/a', why='推荐理由内容示例文本', score=88)
    before = f.index.read_bytes()
    r = run([BUILD, 'build', '--date', '2026-07-24', '--archive-only'], f.env)
    assert r.returncode == 0, f'archive-only build 应成功: {r.stdout}{r.stderr}'
    assert f.index.read_bytes() == before, 'archive-only 不应改主页'
    assert (f.archive / '2026-07-24.html').exists(), 'archive-only 仍应写当天归档'


def t13_node_syntax_check(f):
    """node --check 真语法检查：真语法错被拦；字符串里合法的 '{' 不误杀

    直接单测模块级 check_page_content（build.py 的产物检查函数）——
    渲染器本身会正确转义，数据层面很难造出坏 JS；这里测的是**检查器本身**。
    """
    import build  # build.py 的模块级函数
    # 格式与 render_item 一致（每字段两空格缩进、字段名后跟冒号）
    good = ('const ITEMS = [\n'
            '{\n'
            '  t:"含花括号 { 的正常标题",\n'
            '  d:"2026-01-01",\n'
            '  src:"来源",\n'
            '  cat:"ai",\n'
            '  regions:["中国"],\n'
            '  score:80,\n'
            '  title:"标题",\n'
            '  url:"https://example.com/a",\n'
            '  why:"推荐理由"\n'
            '},\n'
            '];')
    ok, msg = build.check_page_content(good, True, '2026-01-01')
    assert ok is True, f'字符串里合法的 {{ 不该误杀: {msg}'
    broken = 'const ITEMS = [\n  t:"没闭合的数组",\n'
    ok, msg = build.check_page_content(broken, True, '2026-01-01')
    assert ok is False, f'真语法错必须被拦下（node --check）: {msg}'
    assert 'node' in msg or '语法' in msg, f'应说明是语法检查失败: {msg}'
    # 整页 build 也要用同一个检查器：数据合法时能过
    f.migrate()
    f.seed_raw('2026-07-24', '候选', 'https://example.com/a')
    f.seed_item('2026-07-24', '含花括号 { 的正常标题', 'https://example.com/a',
                why='推荐理由内容示例文本', score=88)
    r = run([BUILD, 'build', '--date', '2026-07-24'], f.env)
    assert r.returncode == 0, f'含 {{ 的合法标题应能构建: {r.stdout}{r.stderr}'


TESTS = [
    ('整批原子拒绝', t1_batch_atomic_reject),
    ('raw 引用校验', t2_raw_link_validation),
    ('7 日对称去重', t3_dedupe_symmetric),
    ('旧 fetch 入口禁用', t4_old_fetch_disabled),
    ('校验失败零写入', t5_zero_write_on_failure),
    ('replace 原子性', t6_replace_atomic),
    ('why 全链路', t7_why_in_all_outputs),
    ('UTC→北京时间', t8_utc_to_beijing),
    ('批内重复被拒', t9_in_batch_duplicate_rejected),
    ('replace 范围完整性', t10_replace_range_integrity),
    ('post 也走闸门', t11_post_respects_gate),
    ('archive-only 不动主页', t12_archive_only_keeps_homepage),
    ('node 真语法检查', t13_node_syntax_check),
]


def main():
    failed = 0
    for name, fn in TESTS:
        f = Fixture()
        try:
            fn(f)
            print(f'  ✓ {name}')
        except AssertionError as e:
            failed += 1
            print(f'  ✗ {name}: {e}')
        except Exception as e:
            failed += 1
            print(f'  ✗ {name}: 异常 {type(e).__name__}: {e}')
        finally:
            f.cleanup()
    print(f'\n{len(TESTS) - failed}/{len(TESTS)} passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
