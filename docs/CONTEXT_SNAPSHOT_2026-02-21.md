# Context Snapshot — 2026-02-21

Document Type: Implementation Snapshot
Purpose: 压缩上下文，避免长对话重复回放；新窗口优先读取本文件。

---

## 1) 当前阶段状态

- S8: 收官（线索->报价->成交->复购提醒基础闭环完成）
- S9: 收官（daily-brief、模板应用、归因联动、won+7d 提醒完成）
- S10: 已完成 P0~P3（today-list API、周报模板转化、daily digest 产物、质量守门）
- S11-P0: 第一批完成（运营总览页 ops 已上线）

关键页面：
- `/cn/admin/leads/team-brief/`
- `/cn/admin/leads/team-brief/today/`
- `/cn/admin/leads/team-brief/daily-brief/`
- `/cn/admin/leads/team-brief/weekly/`
- `/cn/admin/leads/team-brief/ops/`

---

## 2) 当前服务入口

- pay-confirm 服务入口应指向：
  - `scripts/pay_confirm_server_auto_s10.py`

建议核验：
```bash
ps -p $(systemctl show -p MainPID --value rareearth-pay-confirm-20260111n) -o args=
```

---

## 3) 运营日常最小命令

1) 生成每日摘要
```bash
PYTHONPATH=/root/rareearth-sentinel python3 /root/rareearth-sentinel/scripts/build_team_brief_daily_brief.py
```

2) 生成 today 页面
```bash
PYTHONPATH=/root/rareearth-sentinel python3 /root/rareearth-sentinel/scripts/build_team_brief_today.py
```

3) 生成 ops 页面
```bash
PYTHONPATH=/root/rareearth-sentinel python3 /root/rareearth-sentinel/scripts/build_team_brief_ops.py
```

4) 每周生成 weekly
```bash
PYTHONPATH=/root/rareearth-sentinel python3 /root/rareearth-sentinel/scripts/build_sales_weekly_digest.py
```

---

## 4) 冻结项（除 P0 bug 不改）

- `templates/admin_team_brief_today.html` 主交互结构
- `scripts/build_team_brief_daily_brief.py` 输出字段结构
- `scripts/build_sales_weekly_digest.py` 核心口径
- 周包自动交付链路（pay-confirm auto + fulfill）

---

## 5) 未关闭风险

1. Gemini/Jina timeout 导致回退率波动
2. PayPal Webhook 验签一致性（WH-REST）
3. Python3.10 与 `google.generativeai` 弃用警告（技术债）

---

## 6) 下一步建议（最小）

- S11-P0 第二批：
  - 在 leads/today/weekly 顶部统一加入 `ops` 入口（若缺）
  - ops 页增加“今日执行指令”复制成功提示与 last snapshot 对比色
  - 保持只读，不扩展支付/抓取链路
