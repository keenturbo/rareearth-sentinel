# RareEarth Sentinel Handoff

Last updated: 2026-01-20

新窗口启动请先 get docs/CONTRIBUTING.md

This handoff is split into two guides:
- Development guide: docs/DEV_GUIDE.md
- Operations guide: docs/OPS_GUIDE.md

Quick summary:
- Product: CN/EN rare earth intelligence platform with two paid products (reports + task execution).
- Primary domain: https://sentinel.dataspices.com
- Admin (NPM Basic Auth): /cn/admin/premium/, /cn/admin/orders/, /cn/admin/task-orders/, /cn/admin/sources/, /cn/admin/keywords/, /cn/admin/indices/

Use the guides above for full details and checklists.

---

## 18) 维护 ToDo & 验证清单（新增 2026-01-28）

### P0（立即处理 / 会影响稳定运行或付费）
1) **PayPal Webhook 验签确认**
   - 确认 `PAYPAL_WEBHOOK_ID` 为 REST Webhook（`WH-` 前缀）
   - 实测 webhook 回调、验签结果、订单状态更新  
2) **主流程稳定性（06:00）**
   - 验证 `rareearth-sentinel.timer` 每日触发并完成  
   - 重点排查 Gemini 超时与 premium 生成异常  
3) **Top3 自动推送稳定性（10/15/20）**
   - 确认 `rareearth-top3.timer` 正常触发三次  
   - 确认 `top3_list.json` & `top3_status.json` 正常生成/更新  
4) **付费页 404 风险复查**
   - 确认首页 & Top3 推送不再引用 `/pay/<id>.html`  
   - fallback Top3 与非 premium 情况需覆盖  

---

### P1（本周内完成 / 体验与可控性）
1) **日报兜底验证（中英）**
   - 失败时展示“上次生成于 …”  
   - 超过 48h 自动规则摘要  
2) **Premium 生成稳定性**
   - 验证 `finish_reason=1` 兜底有效  
   - 可用 `scripts/rebuild_premium.py` 重建当天  
3) **Admin API 路径反代完整性**
   - `/api/admin/keywords/*`  
   - `/api/admin/sources/*`  
   - `/api/admin/indices/*`  
   - `/api/paypal/*`  
4) **关键词库清理/审核**
   - 检查 `enabled` 数量是否影响抓取时长  
   - 必要时分批启用  

---

### P2（中期优化 / 提升转化）
1) **Top3 “X/LinkedIn 一键复制”字段**  
2) **英文外宣文案与模板固化（短格式输出切换）**  
3) **报表可追踪性（统一验证路径展示）**  

---

### P3（延期 / 半成品或未启动）
1) **订阅制支付系统**  
2) **用户账户体系**  
3) **Ranking 数据生成脚本**  
4) **$99/$999 高档位报告**  

---

### 半成品/需验证列表
- **Top3 生成逻辑**：去重 + 重大事件三视角需持续观察  
- **PayPal Webhook**：create/capture 可用，验签需实测  
- **日报缓存**：48h 缓存规则需实测（中英）  

---

### 红线（必须记住）
- 禁止截断 `src/reporter.py` / `scripts/pay_confirm_server.py` / `main.py`  
- 禁止使用 `# ... unchanged ...`  
- 修改大文件只做局部 patch  
- 服务器命令一行一块  
- 禁止写入密钥/Token 到仓库  
- 禁止使用 rg（仅 grep）  
- `FieldStorage` 不能用于 bool 判断  

---

## 验证命令清单
> 执行路径默认 `/root/rareearth-sentinel`；如不同请替换路径。

### P0-1 PayPal Webhook 验签确认
1) 检查 Webhook ID 是否 REST（WH- 前缀）
```bash
grep -n "PAYPAL_WEBHOOK_ID" /root/rareearth-sentinel/.env
```
2) 检查 webhook 请求/验签日志
```bash
journalctl -u rareearth-pay-confirm-20260111n --since "today" --no-pager | grep -i webhook
```
3) 检查订单状态是否被更新
```bash
tail -n 50 /root/rareearth-sentinel/data/pay/orders.jsonl
```

### P0-2 主流程稳定性（06:00）
1) 定时器与服务状态
```bash
systemctl status rareearth-sentinel.timer
```
```bash
systemctl status rareearth-sentinel.service
```
2) 当天日志
```bash
journalctl -u rareearth-sentinel.service --since "today" --no-pager | tail -n 200
```
3) 数据更新时间
```bash
ls -l /root/rareearth-sentinel/data/intel.jsonl
```

### P0-3 Top3 自动推送稳定性（10/15/20）
1) 定时器与服务状态
```bash
systemctl status rareearth-top3.timer
```
```bash
systemctl status rareearth-top3.service
```
2) 当天日志
```bash
journalctl -u rareearth-top3.service --since "today" --no-pager | tail -n 200
```
3) Top3 状态文件
```bash
ls -l /root/rareearth-sentinel/data/status/top3_list.json
```
```bash
cat /root/rareearth-sentinel/data/status/top3_status.json
```

### P0-4 付费页 404 风险复查
1) 确认静态目录
```bash
grep -n "STATIC_DIR" /root/rareearth-sentinel/.env
```
2) 检查旧 `/pay/` 链接残留
```bash
grep -R "/pay/" -n /root/rareearth-sentinel/static | head -n 20
```
3) 检查新 `/task/pay/` 链接是否存在
```bash
grep -R "/task/pay/" -n /root/rareearth-sentinel/static | head -n 20
```

### P1-1 日报兜底验证（中英）
1) 查看 CN 日报缓存/生成时间
```bash
cat /root/rareearth-sentinel/data/status/daily_report_zh.json
```
2) 查看 EN 日报缓存/生成时间
```bash
cat /root/rareearth-sentinel/data/status/daily_report_en.json
```

### P1-2 Premium 生成稳定性
1) 仅重建当天 Premium
```bash
PYTHONPATH=/root/rareearth-sentinel python3 /root/rareearth-sentinel/scripts/rebuild_premium.py
```
2) 检查索引更新
```bash
tail -n 5 /root/rareearth-sentinel/data/premium/reports_index.jsonl
```

### P1-3 Admin API 路径反代完整性
1) 关键词 API
```bash
curl -I -u "$NPM_USER:$NPM_PASS" https://sentinel.dataspices.com/api/admin/keywords/
```
2) 信源 API
```bash
curl -I -u "$NPM_USER:$NPM_PASS" https://sentinel.dataspices.com/api/admin/sources/
```
3) 行情 API
```bash
curl -I -u "$NPM_USER:$NPM_PASS" https://sentinel.dataspices.com/api/admin/indices/
```
4) PayPal API
```bash
curl -I -u "$NPM_USER:$NPM_PASS" https://sentinel.dataspices.com/api/paypal/create-order
```

### P1-4 关键词库清理/审核
1) 定位自定义关键词文件
```bash
find /root/rareearth-sentinel -name "custom_keywords.json"
```
2) 统计 enabled 数量（路径按实际调整）
```bash
python3 -c "import json; p='/root/rareearth-sentinel/data/custom_keywords.json'; d=json.load(open(p)); print(sum(1 for x in d if x.get('enabled')))"
```

### P2-1 Top3 “X/LinkedIn 一键复制”字段
```bash
grep -R -n -E "x_copy|twitter|linkedin|social_copy" /root/rareearth-sentinel/src /root/rareearth-sentinel/templates
```

### P2-2 英文外宣文案与模板固化
```bash
grep -R -n -E "short|brief|telegram" /root/rareearth-sentinel/src /root/rareearth-sentinel/templates
```

### P2-3 报表可追踪性（统一验证路径）
```bash
grep -R -n -E "verification|trace|验证路径" /root/rareearth-sentinel/src /root/rareearth-sentinel/templates
```

### P3-1 订阅制支付系统（延后）
```bash
grep -R -n -E "subscription|recurring" /root/rareearth-sentinel/src /root/rareearth-sentinel/scripts
```

### P3-2 用户账户体系（延后）
```bash
grep -R -n -E "signup|login|password" /root/rareearth-sentinel/src /root/rareearth-sentinel/templates
```

### P3-3 Ranking 数据生成脚本（未完成）
```bash
ls /root/rareearth-sentinel/scripts | grep -i ranking
```

### P3-4 $99/$999 高档位报告（隐藏未实现）
```bash
grep -R -n -E "tier.*(99|999)" /root/rareearth-sentinel/src /root/rareearth-sentinel/templates
```

### 半成品/需验证列表
- Top3 生成逻辑：见 P0-3  
- PayPal Webhook：见 P0-1  
- 日报缓存：见 P1-1

---

## 19) 2026-02-15 阶段性回顾（v2.8 ~ v2.8.10）

### 19.1 本阶段已完成（按模块）

#### A) 首页“今日重点专题”与文案链路
- 首页已从旧的固定展示逻辑切换到可接入 AI 文案链路（含回退）。
- 重要修复：
  - 过时绝对化表述拦截（如“暂停对美稀土出口”类语句）。
  - 证据锚点编号句拦截（如“证据锚点：1. ...”）。
  - 缓存版本推进到 `v2.8.10`，避免旧缓存污染。
- 当前已人工验证：`grep "证据锚点：1\."` 在首页无命中。

#### B) Admin 社媒页 `/cn/admin/social`
- 已实现结构化可发布输出（中英），并连续热修：
  - 去掉旧三视角硬拼接与模板噪声（TL;DR/要点/异常标点）。
  - 动作建议句与证据锚点分离。
  - 编号风险句并入“反方风险”。
  - 过时绝对化事实守卫。
- 当前状态：文案可发布，仍需持续观察次日自动构建是否回归。

#### C) Gemini 角色化（persona）
- 已为主要生成链路注入 persona：
  - `src/social_copy_ai.py`
  - `src/extractor.py`（analyze/analyze_en/analyze_daily_report）
  - `src/premium/briefs.py`
- 目标：减少拼装感、主语更明确、可发布性更高。

#### D) 运维与一致性
- 服务器补丁已回推 GitHub，`HEAD` 与 `origin/main` 对齐。
- 已通过 `.git/info/exclude` 处理运行产物，降低 `git pull` 冲突概率。

---

### 19.2 本阶段关键提交（节选）
> 仅列核心节点，完整历史请用 `git log --oneline --since="2026-02-10"`。

- `245b0adf...` admin social 自动结构化文案首版
- `b0ae3d15...` 引流文案模板增强
- `7ffcccdc...` / `265bd09d...` / `9a0e9446...` persona 扩展到 social/extractor/premium
- `bf2965b...` build_admin_orders 接入 AI 文案生成器
- `e72194e...` 服务器热修回推（main.py 接线修正）
- `624928ed...` / `ab43f9c1...` / `783937dc...` v2.8.4 social 稳定化
- `a248b27d...` v2.8.7 后端 fallback 根修
- `727a5e5a...` v2.8.8 反方风险编号清洗
- `03083537...` v2.8.9 首页新鲜度守卫增强
- `085893ae...` v2.8.10 证据锚点编号句拦截

---

### 19.3 当前网站状态评估（2026-02-15）

#### 1) 产品可用性
- CN 首页、EN 首页、Admin、支付、Top3 推送链路均可运行。
- 社媒页与首页专题达到“可发布/可读”水平，但仍需观测自动运行后的稳定一致性。

#### 2) 内容质量（从“已赋予 persona 的 Gemini 角色”视角）
- 优势：
  - 专业框架完整（事实→路径→动作→风险→证据）。
  - 风险提示与合规意识明显增强。
  - 过时绝对句与编号证据句已纳入守卫。
- 风险点：
  - 自动运行时仍可能受超时影响触发 fallback；需继续监控。
  - 个别日样本在“影响路径/证据锚点”仍可能出现语义靠近，需要持续回归测试。

#### 3) 稳定性
- 主要风险仍在外部超时（抓取/Gemini timeout）与缓存命中策略。
- 已有 fallback，但建议以“次日 06:00 自动运行结果”为最终验收口径。

---

### 19.4 明日 06:00 自动运行后的必做核验（新增）

1) 检查首页是否再出现“证据锚点编号句”
```bash
grep -n "证据锚点：1\\." /root/rareearth-sentinel/static/cn/index.html | head -n 20
```

2) 检查首页是否回流过时绝对表述
```bash
grep -n "中国暂停对美稀土出口\|中国正式暂停对美稀土出口\|对美稀土出口已暂停" /root/rareearth-sentinel/static/cn/index.html | head -n 20
```

3) 检查社媒页是否回归旧模板/坏句
```bash
grep -n "TL;DR\|要点 •\|证据锚点：减持\|影响路径：1\\." /root/rareearth-sentinel/static/cn/admin/social/index.html | head -n 20
```

4) 检查 AI 缓存状态
```bash
python3 - <<'PY'
import os,json
p='/root/rareearth-sentinel/data/status/social_copy_generated.json'
print('exists:', os.path.exists(p))
if os.path.exists(p):
    d=json.load(open(p,'r',encoding='utf-8'))
    print('generated_at:', d.get('generated_at'))
    print('fallback_used:', d.get('fallback_used'))
    print('validator_reason:', d.get('validator_reason'))
    print('cn_topic_len:', len((d.get('cn_topic') or '').strip()))
PY
```

5) 检查 06:00 主流程日志中的 timeout 密度
```bash
journalctl -u rareearth-sentinel.service --since "today 05:50:00" --until "today 09:30:00" --no-pager | grep -Ei "timeout|timed out|ReadTimeout" | tail -n 100
```

---

### 19.5 仍未关闭的高优先事项
- **PayPal Webhook ID 仍需确认 WH-REST 形态**（若未修，将影响自动验签/解锁可靠性）。
- **抓取与模型超时治理**（外部超时仍是内容稳定性的第一风险源）。
- **技术债**：Python 3.10 与 `google.generativeai` 弃用告警，建议规划迁移窗口。
