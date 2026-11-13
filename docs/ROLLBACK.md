# 回滚与生产切换手册（dealhot 新链路）

配套分支：`fix/dealhot-raw-candidates-pipeline`（流水线 + 闸门 + SKILL）、`hotfix/skill-aihot-v1-2026-11-13`（AIHOT 地址）。
本文档只讲**怎么回滚**和**验收通过后怎么切生产**，顺序不可颠倒。

## 1. Schema 迁移的回滚

`python3 scripts/pipeline.py migrate` 只做两件**纯增量**的事：

| 动作 | 影响 |
|---|---|
| `CREATE TABLE IF NOT EXISTS raw_candidates (...)` | 新增表，不动已有表 |
| `ALTER TABLE items ADD COLUMN raw_id INTEGER` | 新增**可空**列，不改已有行 |

因此回滚也是纯减法：

```bash
# ① 先备份（务必）
cp dealhot.db "dealhot.db.bak.$(date +%Y%m%d-%H%M%S)"

# ② 回滚（SQLite ≥ 3.35 支持 DROP COLUMN；本机 3.51）
sqlite3 dealhot.db "DROP TABLE IF EXISTS raw_candidates;"
sqlite3 dealhot.db "ALTER TABLE items DROP COLUMN raw_id;"
```

- 校验：`SELECT COUNT(*) FROM items;` 应与迁移前一致，且 `PRAGMA table_info(items)` 里不再有 `raw_id`
- 若 SQLite < 3.35（无 DROP COLUMN）：直接用备份还原 `dealhot.db`，再重跑 migrate
- **raw_candidates 里的候选不丢信息**：IT桔子按日期窗口可重新抓回，AIHOT 7 天内可重抓；被 `accept` 拒绝的候选本来就没进 items

## 2. 发布回滚（页面层）

`build.py build` 只写三个文件：`index.html`、`archive/<date>.html`、`archive/index.html` → 都进 git，回滚就是 revert：

```bash
git revert <commit>          # 或 git revert --no-commit <commit> 后手工调整
git push origin main         # GitHub Pages 约 1 分钟重新发布
```

注意：**页面回滚不改 DB**；反过来若只回滚了 DB，必须重新 `build` 才能让页面与 DB 一致。

## 3. 生产切换 runbook（验收通过后按序执行）

### 3.1 合分支（先记回滚点）

```bash
git checkout main && git pull --ff-only origin main
git rev-parse HEAD > /tmp/dealhot_cutover_rollback_point.txt   # 回滚点

git merge --no-ff fix/dealhot-raw-candidates-pipeline
git merge --no-ff hotfix/skill-aihot-v1-2026-11-13
python3 scripts/pipeline.py migrate        # 生产 DB 加表 + 加列（幂等）
git push origin main
```

### 3.2 把 7/24–7/27 的旧数据换成重做后的 20 条

```bash
cp dealhot.db "dealhot.db.bak.pre-redo.$(date +%Y%m%d-%H%M%S)"

sqlite3 dealhot.db "DELETE FROM items WHERE date BETWEEN '2026-07-24' AND '2026-07-27';"

for D in 2026-07-24 2026-07-25 2026-07-26 2026-07-27; do
  python3 scripts/pipeline.py fetch  --date $D
  python3 scripts/pipeline.py accept --date $D --file samples/$D.json
  python3 scripts/pipeline.py stats  --date $D
  python3 scripts/build.py build   --date $D
done
```

- 这会改变 `archive/2026-07-2*.html` 与主页：那四天从 21/25 条变成每天 5 条（旧内容是「最新 20 条」被重复写了四天 + 1 条日报聚合）
- 每一步 `build` 前闸门会先跑；不通过就停在原地，不会写出坏页面
- 完成后 `git add -A && git commit -m "dealhot 7/24-7/27 重做（精选 20 条）" && git push origin main`

### 3.3 切日常 job

reminder 里的每日流程换成：

```
pipeline.py fetch → pipeline.py list → 人工精选写 JSON → pipeline.py accept
→ pipeline.py stats → build.py build → build.py post → 发 #dealhot → 回报 #all → reminder ack
```

⚠️ **必须 accept 才能 build**：`fetch` 只落 raw_candidates；当天没有 accept 的条目时，`build` 会以「当日没有任何条目」退出 2（这是设计，不是 bug）。

### 3.4 回滚

| 层 | 动作 |
|---|---|
| 页面 | `git revert <commit> && git push origin main` |
| 数据 | 用 `dealhot.db.bak.*` 还原，再重新 build |
| 代码 | `git revert -m 1 <merge commit> && git push origin main`，再按 §1 回滚 schema |
| 提醒 | reminder 本身不切（仍是同一条 `#4d2c06ea`），只是执行步骤变了；出问题时按老步骤跑 |

## 4. 切前检查清单（每项都要实测跑一遍）

- [ ] `python3 scripts/pipeline.py migrate` 跑两次，结果一致（幂等）
- [ ] `fetch` 之后 `items` 行数**不变**（只进 raw_candidates）
- [ ] `accept` 前后 `items` 行数差 = 精选条数；被拒条目逐条有原因
- [ ] 在**旧数据**上 `build` → exit 3 且**不写任何文件**
- [ ] 在样稿上 `build` → 闸门通过、JS check 两行 OK、Archive page items = 5
- [ ] `post` 文案里是「北京时间 (UTC+8)」，没有裸 `CST`
- [ ] `git status` 干净；`.secrets/` 未被 stage
