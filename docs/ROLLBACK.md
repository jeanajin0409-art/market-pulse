# 回滚与生产切换手册（dealhot 新链路）

配套分支：**只有 `fix/dealhot-raw-candidates-pipeline`**（流水线 + 闸门 + SKILL + 回归测试）。
早先的 `hotfix/skill-aihot-v1-2026-11-13`（AIHOT 地址）与 `docs/dealhot-sop-2026-11-13`（口径写错）**已删除** ——
前者内容被 SKILL.md 全文取代，两个分支都改 SKILL.md 会互相冲突，合一个就够。
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
- ⚠️ **候选不一定能重抓**：IT桔子按日期窗口能重抓，但 **AIHOT 公开 API 只有 7 天窗口** ——
  超过 7 天的 AIHOT 候选**重抓不回来**。所以**完整回滚 = 还原 DB 备份**，不是「删表重抓」

## 2. 发布回滚（页面层）

`build.py build` 只写三个文件：`index.html`、`archive/<date>.html`、`archive/index.html` → 都进 git，回滚就是 revert：

```bash
git revert <commit>          # 或 git revert --no-commit <commit> 后手工调整
git push origin main         # GitHub Pages 约 1 分钟重新发布
```

注意：**页面回滚不改 DB**；反过来若只回滚了 DB，必须重新 `build` 才能让页面与 DB 一致。

## 3. 生产切换 runbook（验收通过后按序执行）

### 3.1 合分支（先记回滚点）

**只合一个分支**：`fix/dealhot-raw-candidates-pipeline`。它已包含 AIHOT v1 的 SKILL.md 全文重写，
早先那个 `hotfix/skill-aihot-v1-2026-11-13`（同样改 SKILL.md）已被内容取代并删除 ——
两个分支都改 SKILL.md 会冲突，合一个就够。

```bash
git checkout main && git pull --ff-only origin main
git rev-parse HEAD > /tmp/dealhot_cutover_rollback_point.txt   # 回滚点

python3 tests/run_tests.py                  # 先跑回归（16 类场景）
git merge --no-ff fix/dealhot-raw-candidates-pipeline
python3 scripts/pipeline.py migrate        # 生产 DB 加表 + 加列（幂等）
git push origin main
```

冲突自检（合并前先演练）：

```bash
git merge-tree $(git merge-base main origin/fix/dealhot-raw-candidates-pipeline) \
    main origin/fix/dealhot-raw-candidates-pipeline | grep -c '<<<<<<<'   # 期望 0
```

### 3.2 把 7/24–7/27 的旧数据换成重做后的 20 条

**用事务化的 `replace`，不要手写 DELETE** —— 中途失败会留下半迁移状态（旧行已删、新行没进）。

```bash
cp dealhot.db "dealhot.db.bak.pre-redo.$(date +%Y%m%d-%H%M%S)"

# ① 先把候选抓进 raw_candidates（不碰 items）
for D in 2026-07-24 2026-07-25 2026-07-26 2026-07-27; do
  python3 scripts/pipeline.py fetch --date $D
done

# ② 一次事务替换（先整批校验 → 通过后 DELETE 旧 + INSERT 新；失败整批回滚、旧数据不动）
python3 scripts/pipeline.py replace --from 2026-07-24 --to 2026-07-27 --min-items 3 \
  --files samples/2026-07-24.json,samples/2026-07-25.json,samples/2026-07-26.json,samples/2026-07-27.json

# ③ 重新渲染四天 —— 必须用 --archive-only：**历史日的 build 不能改主页**
#    （否则主页 header / 今日导读会退回 2026-07-27，线上首页日期倒退）
for D in 2026-07-24 2026-07-25 2026-07-26 2026-07-27; do
  python3 scripts/pipeline.py stats --date $D
  python3 scripts/build.py build   --date $D --archive-only
done

# ④ 主页由**下一次正常发布**重建：日常 job 跑的是当天日期的 build（不带 --archive-only），
#    它从 DB 重新渲染整个主页 —— 此时 DB 里已是重做后的条目，主页日期 = 当天，不会倒退
```

- **样稿里的 raw 引用是稳定键**（`raw_source` + `raw_source_id` = IT桔子 event_id），重新 fetch 后 id 可能变，
  稳定键不会 → 不用改样稿
- 这会改变 `archive/2026-07-2*.html` 与主页：那四天从 21/25 条变成每天 5 条（旧内容是「最新 20 条」被重复写了四天 + 1 条日报聚合）
- `build` 的零写入保证：校验不过就退出，三个目标文件 byte-unchanged
- 完成后 `git add -A && git commit -m "dealhot 7/24-7/27 重做（精选 20 条）" && git push origin main`

### 3.3 切日常 job

reminder 里的每日流程换成：

```
pipeline.py fetch → pipeline.py list → 人工精选写 JSON → pipeline.py accept
→ pipeline.py stats → build.py build → build.py post → 发 #dealhot → 回报 #all → reminder ack
```

⚠️ **必须 accept 才能 build**：`fetch` 只落 raw_candidates；当天没有 accept 的条目时，`build` 会以「当日没有任何条目」退出 2（这是设计，不是 bug）。

⚠️ **主页由这一步重建**：日常 job 的 `build`（不带 `--archive-only`）会用**当天日期**从 DB 重新渲染整个主页 ——
此时 DB 里已是重做后的 7/24–7/27 条目，主页头部日期 = 当天，不会倒退。
**绝不能**用历史日期的 build 去重建主页（那会把头部日期退回 7/27）。

### 3.4 回滚

| 层 | 动作 |
|---|---|
| 页面 | `git revert <commit> && git push origin main` |
| 数据 | 用 `dealhot.db.bak.*` 还原，再重新 build |
| 代码 | `git revert -m 1 <merge commit> && git push origin main`，再按 §1 回滚 schema |
| 提醒 | reminder 本身不切（仍是同一条 `#4d2c06ea`），只是执行步骤变了；出问题时按老步骤跑 |

### 3.5 旧日期为什么不能直接重建（不是 bug）

`publish_gate` 现在要求正式条目**有 raw 溯源**（`raw_id` → 同日候选 → `status='selected'`）。
旧链路写进生产的条目**没有 raw_id**，所以：

- 用新链路重建**旧日期**会被闸门拦下（实测旧数据 86 项拦截）→ 这是预期行为
- 已提交的 `archive/<旧日期>.html` **不需要重建**，它们在 git 里
- 确需重建某个旧日期 → 必须用新链路重新抓取 + 重新精选（`pipeline.py fetch` → `accept` → `build --archive-only`）

## 4. 切前检查清单（每项都要实测跑一遍）

- [ ] `python3 tests/run_tests.py` → **16/16 passed**
- [ ] `python3 scripts/pipeline.py migrate` 跑两次，结果一致（幂等）
- [ ] `fetch` 之后 `items` 行数**不变**（只进 raw_candidates）
- [ ] `accept` 前后 `items` 行数差 = 精选条数；一条不合格时**整批 0 条落库**且退出码非 0
- [ ] `replace` 失败时区间行数不变（旧 84 行原样）；成功时一次替换完成
- [ ] `replace` 区间缺日期 / 日期重复 / 日期越界 / 某天空文件 → 拒绝且**不删任何旧数据**（生产禁用 --allow-missing）
- [ ] `accept` 空 items → 非零退出、0 条落库
- [ ] 批内重复（同 event_hash 的两条）→ 整批拒绝、0 条落库
- [ ] `post` 在坏数据上 → 非零退出且**不输出 post 正文**
- [ ] 缺 raw 证据（raw_id 为空 / 指向的候选不存在 / 状态不是 selected）的条目 → 闸门拦下
- [ ] 跨日 url / content_hash / event_hash 重复（标题不同）→ 闸门拦下
- [ ] `fetch` 端点异常 → 非零退出（rc=5），失败端点出现在 `stats` 的 last_fetch_errors
- [ ] 在**旧数据**上 `build` → exit 3 且**不写任何文件**（11/13 旧数据实测拦 61 项：首页 URL / 空 why /
      未解析轮次 / 批量同分，跨日重复 0 —— 旧数据的重复出现在更早的日期对之间，不在 11/13 自己身上）
- [ ] JS 结构校验失败时 `build` → exit 4 且三个目标文件 byte-unchanged
- [ ] 在样稿上 `build` → 闸门通过、JS check 三行 OK（node --check 通过）、Archive page items = 5
- [ ] 历史日 build 用 `--archive-only` → `index.html` byte-unchanged（主页日期不倒退）
- [ ] 切 job 后**第一次日常发布**（当天日期、不带 --archive-only）→ 主页重建，头部日期 = 当天，
      且主页里的 7/24–7/27 已是重做后的 20 条
- [ ] `why` 同时出现在 `index.html` / `archive/<date>.html` / post 文本里
- [ ] `post` 文案里是「北京时间 (UTC+8)」，没有裸 `CST`
- [ ] `git status` 干净；`.secrets/` 未被 stage
