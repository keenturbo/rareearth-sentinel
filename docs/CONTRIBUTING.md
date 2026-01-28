# Contributing Guidelines (RareEarth Sentinel)

> 目标：避免误改、过度工程、误删关键代码；保证每次改动可追溯、可验证。

## 1) 工作流（必须遵守）
- **Clarify → Document → Confirm → Code**：先澄清，再文档，再确认，再编码。
- 优化类需求必须给出 **A/保守、B/激进、C/性能** 三方案后再改。
- **目标驱动**：每次改动都要能被验证（文件生成/页面可打开/日志可见）。

## 2) Karpathy 四条法则（必读）
1. **先思考再动手**：陈述假设，不确定就问，不要默默选择。 
2. **简洁优先**：不要过度工程，不加未要求功能。 
3. **外科手术式修改**：只动必要代码，不顺手改相邻代码。 
4. **目标驱动执行**：定义成功标准并验证到位。

## 3) 本仓库硬性规则（不可违反）
- **禁止截断/占位**：不得使用“# ... unchanged ...”或类似占位。
- **重点文件严禁整文件替换**：
  - `src/reporter.py`
  - `scripts/pay_confirm_server.py`
  - `main.py`
- **小步改动**：避免大面积删除/重写；若 diff 过大必须停下确认。
- **仅改需求相关内容**，不要顺手重构或清理旧代码。

## 4) 服务器命令规范（用户偏好）
- **一条命令一个代码块**，代码块内不写注释。
- 服务器排查 **使用 grep，不用 rg**。

## 5) 常用运维命令与路径
- 拉取代码：`cd /root/rareearth-sentinel && git pull`
- 全流程运行：`python3 main.py`
- 仅重建静态页：`python3 main.py --rebuild`

### 定时器
- `rareearth-sentinel.timer`：**每天 06:00** 主流程
- `rareearth-run-tasks.timer`：**工作日 11:00** 任务执行
- `rareearth-top3.timer`：**10/15/20** Top3 推送

### 支付确认服务
- systemd：`rareearth-pay-confirm-20260111n`
- 端口：`8112`
- NPM 必须转发：`/api/admin/*`、`/api/paypal/*`、`/api/admin/indices/*`

## 6) Top3 / Telegram 规则
- **Top3 来源**：`data/status/top3_list.json`（非 HTML）。
- **Top3 模板**：标题 + 价值 + 报告包含 + pay 链接。
- **英文标题**使用 `title_en`（无则跳过）。
- Telegram Token 只放 `.env`，禁止写入仓库。

## 7) 价格行情上传（Admin）
- 上传 `.xls` → 覆盖：`data/indices/raw/manual/repe.xls`
- 自动流程：
  - `normalize_indices.py --once --manual`
  - `build_index_signals.py --once`
  - `main.py --rebuild`
- 状态：`data/status/indices_upload.json`
- 错误日志：`logs/indices_upload.log`

---

如需交接，请同时阅读：`docs/HANDOFF.md`。
