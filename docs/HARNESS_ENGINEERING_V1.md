# RareEarth Sentinel Harness Engineering v1

> Document Type: Implementation Governance Doc  
> Scope: repo-level harness for RareEarth Sentinel  
> Status: active baseline

---

## 1. Why Harness in RareEarth Sentinel

RareEarth Sentinel 的核心问题已经不是“有没有功能”，而是：

- 输入质量波动（HTML/JS/广告污染、超时、429）
- 多条 AI 输出链路质量不稳定（首页、社媒、三版本、premium）
- 错误修复容易停留在局部补丁，缺少统一回灌
- 自动化越来越深，但约束系统和熵管理还不够系统化

因此，本项目的核心公式不是：

```text
model = value
```

而是：

```text
model × harness = value
```

Harness 的任务，是为 Agent / 模型提供一套可持续、可审计、可回退的工作环境。

---

## 2. Core Principles

### 2.1 Repo as Brain
Agent 看不到仓库外的隐性知识，因此：
- 规则要进仓库
- 例外要进仓库
- 运行顺序要进仓库
- 错误回灌方式要进仓库

### 2.2 Constraint-first
“请不要过度推断”是弱约束。  
“检测到 deep_dive 的‘原文可证’段出现强判断词则 fail”是强约束。

优先级如下：
1. 确定性约束（validator / filter / test）
2. fallback
3. prompt 指导

### 2.3 Every Error Must Become a Rule
每个高价值错误都应沉淀为至少一类资产：
- 文档规则
- validator
- fallback
- test case
- runbook

### 2.4 Progressive Autonomy
RareEarth Sentinel 的自动化必须分级：
- 手动触发
- 独立定时
- 并入主流程
- 有条件半自动外发

### 2.5 Entropy Management
代码、文案、prompt、validator 会漂移。  
必须定期做“垃圾回收”：
- stale claim 扫描
- fallback 比例统计
- 文档与实现一致性检查
- 输入污染率检查

---

## 3. Output Chain Governance

项目中当前重点治理 5 条链路：

1. Extractor
2. Premium brief
3. Social copy
4. Homepage topic
5. Content variants

所有链路都必须回答 6 个问题：
- 输入是什么？
- 输出是什么？
- 硬约束是什么？
- fallback 是什么？
- 谁审核？
- 运行在哪个自治等级？

详见：`docs/OUTPUT_CHAINS_V1.md`

---

## 4. Automation Tiers

### Tier 1: Main Pipeline
必须放在主流程中的任务：
- 抓取
- 分析
- 存储
- reporter 重建
- 基础 Telegram / Top3 更新

特点：
- 用户可见价值直接依赖它
- 不宜再无限增重

### Tier 2: Independent Timers / Independent Builds
适合独立触发或独立定时的任务：
- content variants
- admin content variants page
- harness health build
- stale-claim / 质量治理类任务

原则：
- 新链路优先放这里
- 稳定之后才考虑并主流程

### Tier 3: Manual Only
应保留为人工触发：
- 高风险修复
- prompt 大改
- 规则回放分析
- 实验性生成链路

---

## 5. Constraint Map

### 5.1 Input Constraints
- HTML/JS 广告污染必须拦截
- 原文过短要回退或跳过
- 429 / timeout 必须可观察

### 5.2 Output Constraints
- stale claim 禁止
- 证据锚点不得是动作句
- deep_dive 必须区分：原文可证 / 合理推断 / 待验证点
- 三版本不得只是同义改写

### 5.3 Runtime Constraints
- 主流程不能因治理逻辑无限增重
- 新增治理脚本应独立运行
- 失败要有 reason，不允许静默失败

---

## 6. Feedback Loop

标准闭环：

```text
发现错误
→ 记录 symptom
→ 判断 severity
→ 归因 root cause
→ 选择 fix type
→ 提交到仓库
→ 记录到 feedback
→ 下轮验证是否消失
```

执行协议见：`docs/ERROR_FEEDBACK_PROTOCOL_V1.md`

---

## 7. Daily / Weekly Ops

### Daily
- 检查主流程是否完成
- 检查首页/日报是否更新
- 抽检社媒或三版本输出
- 若发现高价值错误，写 feedback 记录

### Weekly
- 查看 variants fallback 比例
- 查看 prompt_qc 原因分布
- 查看 stale claim 是否回归
- 检查 docs 与实现是否漂移

---

## 8. Change Policy

以下改动必须先走文档：
- 新增 AI 输出链路
- 将独立任务并入主流程
- 变更验证标准
- 放宽或收紧 high-value 筛选规则

以下改动可走超小补丁：
- 文案标签修正
- validator 小词表补充
- health 统计项微调

---

## 9. Current P0 Harness Focus

当前 RareEarth Sentinel 的 P0 Harness 重点为：

1. today-first 的 content variants 筛选
2. fact pack 三层分离更干净
3. deep_dive grounding 约束加强
4. 仓库级 Harness 文档与反馈闭环落地

---

## 10. Deep Links

- Agent entry: `AGENTS.md`
- Output registry: `docs/OUTPUT_CHAINS_V1.md`
- Error protocol: `docs/ERROR_FEEDBACK_PROTOCOL_V1.md`
- Handoff / ops history: `docs/HANDOFF.md`
