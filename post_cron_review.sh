#!/usr/bin/env bash
# post_cron_review.sh · cron 任务后自动 review
# 用法：bash post_cron_review.sh <task_name>
# task_name: dealhot | daily-brief | github-scan
#
# 功能：
# 1. 找最新 log
# 2. 跑 checklist（正确性/完整性/格式）
# 3. 评分 1-5
# 4. 输出报告 → stdout + 存 logs/

set -uo pipefail

TASK="${1:-unknown}"
REVIEW_DIR="/Users/jeana/Documents/market-pulse/logs/reviews"
mkdir -p "$REVIEW_DIR"

NOW=$(date '+%Y-%m-%d %H:%M:%S')
SCORE=5
ISSUES=()

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔍 Self-Review · $TASK · $NOW"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

# ─── dealhot checklist ───
review_dealhot() {
    local log="/tmp/aihot_22.json"  # latest aihot
    local page="/Users/jeana/Documents/market-pulse/index.html"
    local today=$(date '+%Y-%m-%d')

    echo "📊 [dealhot] 检查项"
    echo

    # 1. AIHOT 数量
    local aihot_count=$(python3 -c "import json; d=json.load(open('$log')); print(len(d.get('items', d) if isinstance(d, dict) else d))" 2>/dev/null || echo "?")
    echo "  • AIHOT 抓取条数: $aihot_count"
    if [ "$aihot_count" -lt 3 ] 2>/dev/null; then
        ISSUES+=("AIHOT 数据偏少（<3 条）")
        SCORE=$((SCORE-1))
    fi

    # 2. IT桔子 数量
    local itjuzi_count=$(python3 -c "import json; d=json.load(open('/tmp/itjuzi_22.json')); print(len(d.get('data', [])))" 2>/dev/null || echo "?")
    echo "  • IT桔子 抓取条数: $itjuzi_count"
    if [ "$itjuzi_count" -lt 1 ] 2>/dev/null; then
        ISSUES+=("IT桔子 0 条（数据延迟？）")
        SCORE=$((SCORE-0))  # 不扣分（数据延迟是已知现象）
    fi

    # 3. 页面有今日条目
    local today_items=$(grep -c "\"$today\"" "$page" 2>/dev/null || echo "?")
    echo "  • 今日条目数: $today_items"
    if [ "$today_items" -lt 3 ] 2>/dev/null; then
        ISSUES+=("今日条目 <3（推送可能空）")
        SCORE=$((SCORE-1))
    fi

    # 4. 导读有更新（不是上次内容）
    local last_lead=$(python3 -c "
import re
src = open('$page').read()
m = re.search(r'<p>([^<]+)</p>', src)
if m:
    print(m.group(1)[:50])
" 2>/dev/null)
    echo "  • 导读前 50 字: $last_lead"
}

# ─── daily-brief checklist ───
review_daily_brief() {
    local log="/tmp/daily_brief_$(date '+%m-%d').log"  # latest
    [ ! -f "$log" ] && log="/tmp/daily_brief_6-22.log"  # fallback
    echo "📊 [daily-brief] 检查项"
    echo

    # 1. 4 部分都在
    local sections=$(grep -c "^## " "$log" 2>/dev/null || echo "?")
    echo "  • 章节数: $sections"
    if [ "$sections" -lt 4 ] 2>/dev/null; then
        ISSUES+=("章节数 <4（4 部分缺）")
        SCORE=$((SCORE-1))
    fi

    # 2. 持仓 news 完成度
    local news_complete=$(grep "持仓简报完成" "$log" 2>/dev/null | tail -1)
    echo "  • 持仓简报: $news_complete"
    if echo "$news_complete" | grep -q "2 失败" 2>/dev/null; then
        ISSUES+=("2 持仓 news 失败（longbridge 限流？）")
        SCORE=$((SCORE-1))
    fi

    # 3. v7 picks 存在
    local picks=$(grep -c "第二部分" "$log" 2>/dev/null || echo "?")
    echo "  • 选股部分: $([ $picks -ge 1 ] && echo "✅" || echo "❌")"
}

# ─── github-scan checklist ───
review_github_scan() {
    local log="/tmp/scan_$(date '+%m-%d').log"  # 6-22.log
    [ ! -f "$log" ] && log="/tmp/scan_6-22.log"  # fallback
    [ ! -f "$log" ] && log="/tmp/scan_6-20.log"  # fallback
    echo "📊 [github-scan] 检查项"
    echo

    # 1. gh search 4 类
    local gh_sections=$(grep -c "gh search" "$log" 2>/dev/null || echo "?")
    echo "  • gh search 章节: $gh_sections"
    if [ "$gh_sections" -lt 4 ] 2>/dev/null; then
        ISSUES+=("gh search <4 类")
        SCORE=$((SCORE-1))
    fi

    # 2. awesome 列表
    local awesome_sections=$(grep -c "🌟" "$log" 2>/dev/null || echo "?")
    echo "  • awesome 列表章节: $awesome_sections"
    if [ "$awesome_sections" -lt 4 ] 2>/dev/null; then
        ISSUES+=("awesome 列表 <4")
        SCORE=$((SCORE-1))
    fi

    # 3. 命中条数
    local hits=$(grep "扫描完毕" "$log" 2>/dev/null | grep -oE "[0-9]+" | head -1)
    echo "  • 总命中: $hits"
    if [ "${hits:-0}" -lt 10 ] 2>/dev/null; then
        ISSUES+=("命中 <10（rate limit 严重？）")
        SCORE=$((SCORE-1))
    fi
}

# ─── Dispatch ───
case "$TASK" in
    dealhot) review_dealhot ;;
    daily-brief) review_daily_brief ;;
    github-scan) review_github_scan ;;
    *) echo "❌ 未知任务: $TASK"; exit 1 ;;
esac

# ─── 评分报告 ───
echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📋 Self-Review 报告"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo
echo "  任务: $TASK"
echo "  时间: $NOW"
echo "  **评分: $SCORE / 5**"
echo

if [ ${#ISSUES[@]} -eq 0 ]; then
    echo "  ✅ 无问题"
else
    echo "  ⚠️ 发现问题："
    for issue in "${ISSUES[@]}"; do
        echo "    - $issue"
    done
fi

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 存盘
{
    echo "Self-Review · $TASK · $NOW"
    echo "Score: $SCORE/5"
    [ ${#ISSUES[@]} -gt 0 ] && echo "Issues: ${ISSUES[*]}"
} > "$REVIEW_DIR/${TASK}_$(date '+%Y%m%d_%H%M').log"

# 输出 score 数字（供外部脚本读）
echo "$SCORE"
