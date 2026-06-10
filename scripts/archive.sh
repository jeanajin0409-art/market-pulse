#!/usr/bin/env bash
# DealHOT 存档：把当前 index.html 存成 archive/<日期>.html，并重建 archive/index.html（往期列表）。
# 每日更新页面后跑一次：  bash scripts/archive.sh           （默认今天）
#                        bash scripts/archive.sh 2026-06-09 （指定日期，可补档）
set -euo pipefail
cd "$(dirname "$0")/.."                      # 仓库根目录
DATE="${1:-$(date +%Y-%m-%d)}"
mkdir -p archive
SNAP="archive/${DATE}.html"

# 1) 复制当前页面为当日快照，并注入"存档提示条"+修正子目录相对链接
cp index.html "$SNAP"
python3 - "$SNAP" "$DATE" <<'PY'
import sys
path, date = sys.argv[1], sys.argv[2]
h = open(path, encoding="utf-8").read()
banner = (f'<div style="background:#fff7e6;border-bottom:1px solid #f0d9a8;color:#92600a;'
          f'font-size:13px;text-align:center;padding:7px 12px;'
          f'font-family:-apple-system,sans-serif">📅 这是 {date} 的存档快照 · '
          f'<a href="../" style="color:#1558d6;font-weight:600">返回最新</a> · '
          f'<a href="./" style="color:#1558d6">往期存档</a></div>')
h = h.replace("<body>", "<body>\n" + banner, 1)
# 页面里指向 archive/ 的链接，在 archive/ 内部要改成 ./
h = h.replace('href="archive/"', 'href="./"')
open(path, "w", encoding="utf-8").write(h)
PY

# 2) 重建往期索引页（按日期倒序列出所有快照）
names=(周一 周二 周三 周四 周五 周六 周日)
{
cat <<'HEAD'
<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DealHOT · 往期存档</title>
<style>
html,body{background:#fff}
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;color:#1a1a1a;max-width:680px;margin:0 auto;padding:36px 20px;font-size:15px}
a{color:#1558d6;text-decoration:none}a:hover{text-decoration:underline}
.logo span{color:#e8540c}
h1{font-size:22px;margin:0 0 4px}.sub{color:#888;font-size:13px;margin-bottom:22px}
.back{display:inline-block;margin-bottom:18px;font-size:14px}
ul{list-style:none;padding:0;margin:0}
li{border-bottom:1px solid #ececec;padding:13px 2px;display:flex;justify-content:space-between;align-items:center}
li a{font-weight:600}.wd{color:#999;font-size:13px}
</style></head><body>
<a class="back" href="../">← 返回最新</a>
<h1 class="logo">Deal<span>HOT</span> · 往期存档</h1>
<div class="sub">每日一级市场与 AI 动态快照，按日期倒序</div>
<ul>
HEAD
for f in $(ls archive/[0-9]*.html 2>/dev/null | sort -r); do
  d=$(basename "$f" .html)
  u=$(date -j -f "%Y-%m-%d" "$d" +%u 2>/dev/null || echo "")
  wd=""; [ -n "$u" ] && wd="${names[$((u-1))]}"
  echo "<li><a href=\"${d}.html\">${d}</a><span class=\"wd\">${wd}</span></li>"
done
cat <<'FOOT'
</ul>
</body></html>
FOOT
} > archive/index.html

n=$(ls archive/[0-9]*.html 2>/dev/null | wc -l | tr -d ' ')
echo "已存档 → $SNAP；往期索引重建完成（共 $n 期）"
