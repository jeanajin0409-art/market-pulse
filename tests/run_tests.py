#!/usr/bin/env python3
"""tests/run_tests.py · dealhot 新链路回归测试（无第三方依赖）

覆盖 task #1 验收要求的 8 类场景：
  1. 整批原子拒绝（一条不合格 → 0 条落库、非零退出）
  2. raw 引用不存在 / 日期错 / 状态不是 candidate
  3. 7 日去重对称窗口（title / url / content_hash，未来侧也要拦）
  4. 旧 build.py fetch 入口已禁用
  5. JS check 失败 → 三个目标文件 byte-unchanged、非零退出
  6. 历史 replace 中途失败 → 旧数据原样不动
  7. why 出现在 main + archive + post 三处
  8. UTC → 北京时间（UTC+8）事件日期换算

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
    """一次性测试环境：临时 DB + 临时 index.html / archive 目录。"""

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
        # 页面模板 + 往期索引模板（build 会读它们当骨架）
        src_index = (REPO / 'index.html').read_text()
        self.index.write_text(src_index)
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

    def seed_item(self, date, title, url, why='推荐理由内容示例', score=80, cat='minority',
                  src='IT桔子', raw_id=None, sum_='摘要内容示例，长度足够通过字段闸门校验。'):
        conn = self.conn()
        conn.execute(
            'INSERT INTO items (date, t, src, cat, regions, score, title, url, sum, why, ma_cap, tags, commit_sha, raw_id)'
            ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (date, f'{int(date[5:7])}月{int(date[8:10])}日', src, cat, '["中国"]', score, title, url,
             sum_, why, '', '[]', 'seed', raw_id))
        conn.commit()
        conn.close()

    def seed_raw(self, date, title, url, content_hash, source_id='sid-1', source='IT桔子',
                 status='candidate'):
        conn = self.conn()
        conn.execute('''
            INSERT INTO raw_candidates
              (source, source_id, url, event_date, published_at, fetched_at, title, summary, category,
               score, round_name, amount, parties, content_hash, raw_json, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (source, source_id, url, date, '', '2026-01-01T00:00:00+00:00', title, '', '先进制造',
              None, '', '', '', content_hash, '{}', status))
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


# ---------------------------------------------------------------- tests
def t1_batch_atomic_reject(f):
    """整批原子拒绝：1 条合法 + 1 条非法 → 0 条落库，rc != 0，拒绝原因持久化"""
    f.migrate()
    f.seed_raw('2026-07-24', '候选一', 'https://example.com/a', 'hash-a')
    f.seed_raw('2026-07-24', '候选二', 'https://example.com/b', 'hash-b')
    payload = {'date': '2026-07-24', 'items': [
        {'raw_id': 1, 'title': '合法条目', 'url': 'https://example.com/a', 'cat': 'minority',
         'regions': ['中国'], 'score': 88, 'sum': '这是一条合法的摘要内容，长度足够通过闸门。',
         'why': '这是一条足够长的合法推荐理由示例文本', 'tags': ['投融资']},
        {'raw_id': 2, 'title': '非法条目', 'url': 'https://www.itjuzi.com/', 'cat': 'minority',
         'regions': ['中国'], 'score': 80, 'sum': '这是一条非法的摘要内容，长度足够通过闸门。',
         'why': '这是一条足够长的非法推荐理由示例文本', 'tags': ['投融资']},
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
    f.seed_raw('2026-07-24', '候选', 'https://example.com/a', 'hash-a')
    f.seed_raw('2026-07-25', '别的日期', 'https://example.com/b', 'hash-b')
    base = {'title': '条目', 'url': 'https://example.com/a', 'cat': 'minority',
            'regions': ['中国'], 'score': 88,
            'sum': '这是一条摘要内容，长度足够通过闸门校验。', 'why': '这是一条足够长的推荐理由示例文本', 'tags': []}
    # raw_id 不存在
    p = write_json(f.tmp / 'a.json', {'date': '2026-07-24', 'items': [dict(base, raw_id=9999)]})
    r = run([PIPE, 'accept', '--date', '2026-07-24', '--file', p], f.env)
    assert r.returncode != 0 and '不存在' in r.stdout, f'raw_id 不存在应被拒: {r.stdout}'
    # raw 日期错（引用的是 07-25 的候选，却按 07-24 收录）
    conn = f.conn()
    rid2 = conn.execute("SELECT id FROM raw_candidates WHERE event_date='2026-07-25'").fetchone()[0]
    conn.close()
    p = write_json(f.tmp / 'b.json', {'date': '2026-07-24', 'items': [dict(base, raw_id=rid2)]})
    r = run([PIPE, 'accept', '--date', '2026-07-24', '--file', p], f.env)
    assert r.returncode != 0 and '日期不符' in r.stdout, f'日期错应被拒: {r.stdout}'
    # 状态不是 candidate
    conn = f.conn()
    conn.execute("UPDATE raw_candidates SET status='selected' WHERE event_date='2026-07-24'")
    conn.commit()
    conn.close()
    p = write_json(f.tmp / 'c.json', {'date': '2026-07-24', 'items': [dict(base, raw_id=1)]})
    r = run([PIPE, 'accept', '--date', '2026-07-24', '--file', p], f.env)
    assert r.returncode != 0 and '状态不是 candidate' in r.stdout, f'状态不对应被拒: {r.stdout}'
    assert f.items_count('2026-07-24') == 0


def t3_dedupe_symmetric(f):
    """7 日对称窗口：过去侧 / 未来侧 / title / url / content_hash 都要拦"""
    f.migrate()
    f.seed_raw('2026-07-24', '候选', 'https://example.com/new', 'hash-new')
    item = {'raw_id': 1, 'title': '重复的标题', 'url': 'https://example.com/new', 'cat': 'minority',
            'regions': ['中国'], 'score': 88, 'sum': '这是一条摘要内容，长度足够通过闸门校验。',
            'why': '这是一条足够长的推荐理由示例文本', 'tags': []}
    # 未来侧同标题
    f.seed_item('2026-07-27', '重复的标题', 'https://example.com/other')
    p = write_json(f.tmp / 'a.json', {'date': '2026-07-24', 'items': [item]})
    r = run([PIPE, 'accept', '--date', '2026-07-24', '--file', p], f.env)
    assert r.returncode != 0 and '重复' in r.stdout, f'未来侧重复应被拦: {r.stdout}'
    # 过去侧同 url（先把上一轮拒绝留下的 rejected 状态复位）
    run([PIPE, 'reset', '--date', '2026-07-24'], f.env)
    f.seed_item('2026-07-21', '别的标题', 'https://example.com/new')
    p = write_json(f.tmp / 'b.json', {'date': '2026-07-24', 'items': [dict(item, title='新标题')]})
    r = run([PIPE, 'accept', '--date', '2026-07-24', '--file', p], f.env)
    assert r.returncode != 0 and '重复' in r.stdout, f'过去侧 url 重复应被拦: {r.stdout}'
    # content_hash 重复（已发布条目 raw_id 指向同一候选）
    conn = f.conn()
    conn.execute('DELETE FROM items')
    conn.commit()
    conn.close()
    run([PIPE, 'reset', '--date', '2026-07-24'], f.env)
    f.seed_item('2026-07-26', '同事件不同措辞', 'https://example.com/dup', raw_id=1)
    p = write_json(f.tmp / 'c.json', {'date': '2026-07-24', 'items': [dict(item, title='新标题')]})
    r = run([PIPE, 'accept', '--date', '2026-07-24', '--file', p], f.env)
    assert r.returncode != 0 and ('内容重复' in r.stdout or '重复' in r.stdout), f'content_hash 重复应被拦: {r.stdout}'
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


def t5_js_check_zero_write(f):
    """JS 结构校验失败 → 三个目标文件 byte-unchanged、非零退出"""
    f.migrate()
    f.seed_item('2026-07-24', '括号不闭合的标题 {', 'https://example.com/a', why='推荐理由内容示例')
    targets = [f.index, f.archive / 'index.html', f.archive / '2026-07-24.html']
    before = {p: (p.read_bytes() if p.exists() else None) for p in targets}
    r = run([BUILD, 'build', '--date', '2026-07-24'], f.env)
    assert r.returncode != 0, 'JS check 失败应当非零退出'
    for p, b in before.items():
        now = p.read_bytes() if p.exists() else None
        assert now == b, f'{p.name} 不应被写入'
    day_page = f.archive / '2026-07-24.html'
    assert not day_page.exists(), '当天归档不应被创建'


def t6_replace_atomic(f):
    """历史 replace：中途失败 → 旧数据原样不动；全部通过 → 一次替换"""
    f.migrate()
    for i in range(3):
        f.seed_item('2026-07-24', f'旧条目{i}', f'https://example.com/old{i}')
    f.seed_raw('2026-07-24', '新候选', 'https://example.com/new', 'hash-new')
    good = {'raw_id': 1, 'title': '新条目', 'url': 'https://example.com/new', 'cat': 'minority',
            'regions': ['中国'], 'score': 88, 'sum': '这是一条摘要内容，长度足够通过闸门校验。',
            'why': '这是一条足够长的推荐理由示例文本', 'tags': []}
    bad = dict(good, title='坏条目', url='https://www.itjuzi.com/')
    p = write_json(f.tmp / 'r.json', {'date': '2026-07-24', 'items': [good, bad]})
    r = run([PIPE, 'replace', '--from', '2026-07-24', '--to', '2026-07-27',
             '--files', p], f.env)
    assert r.returncode != 0, 'replace 失败应非零退出'
    assert f.items_count('2026-07-24') == 3, 'replace 失败时旧数据应原样不动'
    conn = f.conn()
    titles = [t[0] for t in conn.execute("SELECT title FROM items WHERE date='2026-07-24'")]
    conn.close()
    assert '旧条目0' in titles, '旧行应仍在'
    # 全部通过 → 一次替换（先复位被拒候选）
    run([PIPE, 'reset', '--date', '2026-07-24'], f.env)
    p = write_json(f.tmp / 'r2.json', {'date': '2026-07-24', 'items': [good]})
    r = run([PIPE, 'replace', '--from', '2026-07-24', '--to', '2026-07-27',
             '--files', p], f.env)
    assert r.returncode == 0, f'replace 应成功: {r.stdout}{r.stderr}'
    assert f.items_count('2026-07-24') == 1, '旧行应被替换为新行'


def t7_why_in_all_outputs(f):
    """why 必须出现在 main + archive + post 三处"""
    f.migrate()
    f.seed_raw('2026-07-24', '候选', 'https://example.com/a', 'hash-a')
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
    assert rows.get('北京时间 07-27 07:30 的条目') == '2026-07-27', \
        f"UTC 23:30 应换算成北京时间次日: {rows}"
    assert rows.get('北京时间 07-26 23:00 的条目') == '2026-07-26', \
        f"UTC 15:00 应换算成北京时间当日: {rows}"


TESTS = [
    ('整批原子拒绝', t1_batch_atomic_reject),
    ('raw 引用校验', t2_raw_link_validation),
    ('7 日对称去重', t3_dedupe_symmetric),
    ('旧 fetch 入口禁用', t4_old_fetch_disabled),
    ('JS check 失败零写入', t5_js_check_zero_write),
    ('replace 原子性', t6_replace_atomic),
    ('why 全链路', t7_why_in_all_outputs),
    ('UTC→北京时间', t8_utc_to_beijing),
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
