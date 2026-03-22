# RareEarth Sentinel Error Feedback Protocol v1

> Document Type: Error Governance Protocol  
> Scope: how errors become durable repo rules

---

## 1. Principle

RareEarth Sentinel 的错误处理原则不是“下次注意”，而是：

```text
发现错误
→ 归因
→ 固化修复方式
→ 入仓库
→ 下轮验证
```

任何高价值错误，都应沉淀为仓库资产。

---

## 2. Error Taxonomy

### E1 Input Errors
输入层问题：
- HTML / JS / 广告污染
- 内容过短
- Jina 429 / timeout
- 原文抓取失败

### E2 Output Errors
输出层问题：
- stale claim
- evidence anchor 像建议句
- deep_dive 过度推断
- 三版本相似度过高
- 首页 / 社媒 / variants 口径漂移

### E3 Runtime Errors
运行层问题：
- service 成功但页面未更新
- timer 正常但缓存复用过旧
- build 脚本失败或静默失败
- 主流程耗时异常膨胀

### E4 Docs / Governance Errors
治理层问题：
- 规则只在对话里存在，没有入仓库
- 文档已落后代码状态
- 多个文档口径冲突
- 新链路没有清晰 runtime tier

---

## 3. Severity Levels

### P0
直接影响主站可用性、内容可信度、支付/交付闭环、自动更新。

### P1
不致命，但会显著降低发布质量、审核效率或用户信任。

### P2
次要可用性问题、文档漂移、小范围 UI / wording 问题。

---

## 4. Root Cause Categories

记录错误时，必须至少选一个 root cause：

- `input_guard_missing`
- `prompt_rule_missing`
- `validator_too_weak`
- `validator_too_strict`
- `fallback_missing`
- `selection_logic_drift`
- `runtime_order_wrong`
- `docs_missing`
- `docs_outdated`
- `ops_misconfiguration`

---

## 5. Allowed Fix Types

每个错误修复后，至少要落一种 fix type：

- `doc_rule`：文档规则新增或修正
- `validator_rule`：新增/修正确定性校验
- `fallback_rule`：新增/修正回退逻辑
- `test_case`：新增测试用例
- `runbook_update`：运维手册 / 验收清单更新
- `selection_rule`：筛选逻辑修复
- `timer_split`：运行分层修复

> 单纯改 prompt 不能算完成闭环，除非同时补一项以上 fix type。

---

## 6. Required Artifacts

出现 P0 / P1 错误后，修复动作至少产出其中之一：

1. 文档更新
2. 代码约束（validator / filter）
3. fallback 规则
4. 健康指标新增
5. 测试或回归命令
6. feedback 日志记录

---

## 7. Feedback Record Format

建议统一写入：`data/status/harness_feedback.jsonl`

字段：
- `created_at`
- `chain`
- `severity`
- `error_type`
- `sample_record_id`（可选）
- `symptom`
- `root_cause`
- `fix_type`
- `fix_ref`
- `status`

---

## 8. Closeout Checklist

一个错误只有在满足以下条件后才可标记 `closed`：

- 已完成 root cause 判断
- 已有至少一种 fix type 落地
- 已入仓库（文档 / 代码 / 脚本）
- 已有回归命令或抽检方式
- 下轮验证确认问题未立即复发

---

## 9. Special Rules for RareEarth Sentinel

### 9.1 对 deep_dive 的错误
- 不得只靠 prompt 修复
- 必须优先补 validator / fact pack layering / fallback

### 9.2 对 stale claim 的错误
- 必须进入规则词表或 validator
- 不允许只人工修页面后结束

### 9.3 对 today-first 口径错误
- 必须修 selection logic
- 不允许只在页面层“解释一下”

### 9.4 对输入污染错误
- 优先修 input guard
- 不要直接扩大模型上下文去硬扛脏输入

---

## 10. Anti-Patterns

禁止：
- 只说“下次注意”
- 只改输出文案，不改根因
- 只在聊天里记住问题，不入仓库
- 把同一类问题连续补丁三次还不沉淀规则
