# S9 Day5 收官验收与冻结文档

Document Type: Closeout / Freeze
Date: 2026-02-20
Scope: S9（P0+P1）

---

## 1) 收官验收结论

### 1.1 功能验收
- [x] S9 Day1：`/cn/admin/leads/team-brief/daily-brief/` 每日摘要页上线
- [x] S9 Day2：报价模板（basic/pro/custom）可套用并记录 `proposal_template_applied`
- [x] S9 Day3：周报“归因联动看板”（owner × channel × reason × status）上线
- [x] S9 Day4：today 页新增“复购到期提醒（won+7d）”分组与计数
- [x] 周报页面中文文案统一为“周报摘要”

### 1.2 稳定性验收
- [x] Admin 线索页 API（list/update/events/export）可用
- [x] 页面构建脚本可独立执行：
  - `build_admin_team_brief_leads.py`
  - `build_team_brief_today.py`
  - `build_team_brief_daily_brief.py`
  - `build_sales_weekly_digest.py`
- [x] 不影响 Top3/Telegram/支付主链路

### 1.3 运营可执行性验收
- [x] 日维度：today/daily-brief 可直接产出跟进清单
- [x] 周维度：weekly 可直接产出归因复盘
- [x] 成交后：won 线索具备复购提醒入口（+7d）

---

## 2) 冻结范围（Freeze Scope）

为避免再次进入“无休止补丁”，S9 以下内容冻结（除 P0 Bug 外不改）：

1. Team Brief 线索页结构（主布局、核心按钮组）
2. Today 页面分组结构（高优先/今日/逾期/到期/复购到期）
3. Weekly 页面核心指标结构与归因联动表
4. 报价模板分类（basic/pro/custom）
5. S9 事件字段命名（proposal_template_applied / close_reason_set 等）

---

## 3) 允许改动的白名单（Post-Freeze Allowed Changes）

仅允许以下两类改动进入 main：

1. **P0 Bug 修复**（阻塞功能、错误数据、500/501、页面不可用）
2. **指标驱动优化**（必须附带目标指标与验收方式）

不接受“感觉上更好”的 UI/文案碎改。

---

## 4) 日常运行命令（Runbook）

### 4.1 每日（运营开始前）
```bash
PYTHONPATH=/root/rareearth-sentinel python3 /root/rareearth-sentinel/scripts/build_team_brief_today.py
```

```bash
PYTHONPATH=/root/rareearth-sentinel python3 /root/rareearth-sentinel/scripts/build_team_brief_daily_brief.py
```

### 4.2 每周（复盘前）
```bash
PYTHONPATH=/root/rareearth-sentinel python3 /root/rareearth-sentinel/scripts/build_sales_weekly_digest.py
```

### 4.3 Team Brief 主页面刷新
```bash
PYTHONPATH=/root/rareearth-sentinel python3 /root/rareearth-sentinel/scripts/build_admin_team_brief_leads.py
```

---

## 5) 回归检查清单（Regression Checklist）

- [ ] `/cn/admin/leads/team-brief/` 可打开且无加载报错
- [ ] `/cn/admin/leads/team-brief/today/` 5 个分组均可见
- [ ] `/cn/admin/leads/team-brief/daily-brief/` 复制与导出可用
- [ ] `/cn/admin/leads/team-brief/weekly/` 归因联动表可见
- [ ] `team_brief_lead_events.jsonl` 有持续新增事件

---

## 6) 下一阶段建议（S10 候选，仅列项不立刻开工）

1. S10-P0：`today-list` 后端 API（替代前端 fallback 分类）
2. S10-P1：won/lost 原因编码标准化与趋势图
3. S10-P1：复购触达模板 AB 测试（中英）
4. S10-P1：Admin 漏斗与 Team Brief 看板联动

> 注：S10 启动前需单独立项文档确认。
