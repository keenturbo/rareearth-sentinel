# RareEarth Sentinel Output Chains v1

> Document Type: Output Chain Registry  
> Scope: current AI output chains in production / active iteration

---

## 1. Extractor Chain

### Purpose
把抓取到的原文转成结构化 record，是所有后续输出链路的基础。

### Inputs
- cleaned content
- optional context summary
- config thresholds

### Outputs
- `summary_zh` / `summary_en`
- `expert_summary`
- `key_evidence`
- `assumptions`
- `risk_score`
- `recommendations_*`
- 其他结构化字段

### Hard Constraints
- 输入污染必须先过 input guard
- 证据句数不足时应拒绝或降级
- 过短内容不得强行分析

### Validator / Fallback
- prompt_qc logs
- input guard reject reasons
- analyze_fail 统计

### Reviewer
- Admin / Operator

### Runtime Tier
- **Tier 1 (Main Pipeline)**

---

## 2. Premium Brief Chain

### Purpose
为高价值事件生成付费深度报告底稿与多视角 brief。

### Inputs
- structured records
- premium pipeline ranking
- optional index / market context

### Outputs
- `conclusion`
- `fact_checks`
- `tracking_nodes`
- `value_proposition`
- `impact_score`
- 多视角 brief（policy / industry / capital）

### Hard Constraints
- 事实与结论必须可追溯到 record 或 brief 上游
- 重大事件才允许多视角展开
- 不能把建议句写成事实结论

### Validator / Fallback
- 若 premium 生成失败，至少保留基础 brief 或跳过特定视角

### Reviewer
- Admin / Operator

### Runtime Tier
- **Tier 1 (Main Pipeline)**

---

## 3. Social Copy Chain

### Purpose
生成可直接复制到社媒的引流文案（中英、外链版 / 私信版）。

### Inputs
- `top3_list.json`
- `daily_report_zh.json` / `daily_report_en.json`
- `reports_index.jsonl`

### Outputs
- `cn_social`
- `en_social`
- `cn_topic`
- `en_topic`
- cache file: `data/status/social_copy_generated.json`

### Hard Constraints
- subject 必须清晰
- evidence anchor 不得是动作句
- 不得出现 stale absolute claim
- 中英输出不得有拼装噪声、重复标签、TL;DR 残留

### Validator / Fallback
- built-in validator in `src/social_copy_ai.py`
- cache fallback
- rule fallback

### Reviewer
- Admin / Operator

### Runtime Tier
- **Tier 2 (Independent Build / Independent Timer preferred)**

---

## 4. Homepage Topic Chain

### Purpose
将首页“今日重点专题 / Today’s Focus”做成高密度但稳定的引导内容。

### Inputs
- `top3_list.json`
- daily reports
- optional social bundle cache

### Outputs
- 中文首页专题块
- 英文首页专题块

### Hard Constraints
- stale claim 禁止
- 首页展示层允许 dedupe，但不能改 Telegram 后端数据结构
- 信息密度要高，但不能因去重而严重降价值

### Validator / Fallback
- AI topic 优先
- fallback 为聚合专题卡 / Top1 简版

### Reviewer
- Admin / Operator（抽检）

### Runtime Tier
- **Tier 1.5**（当前挂在重建流程附近；逻辑上接近主流程，但治理上按独立链路看待）

---

## 5. Content Variants Chain

### Purpose
为高价值事件生成三种长内容：
- 雪球长帖版
- 哨兵研判版
- 深水区版本

### Inputs
- records from `intel.jsonl`
- optional premium brief payload
- today-first selection logic

### Outputs
- `data/status/content_variants.jsonl`
- admin page: `/cn/admin/content-variants/`

### Hard Constraints
- 优先今日新增高价值事件
- fact pack 必须分层：`hard_facts / soft_inferences / unknowns`
- `deep_dive` 必须保守，禁止把强判断放进“原文可证”
- 三版本不得高度相似

### Validator / Fallback
- `src/content_variants_ai.py` validator
- rule fallback bundle
- admin page 显示 fallback reason

### Reviewer
- Admin / Operator

### Runtime Tier
- **Tier 2 (Independent Build / Independent Timer preferred)**

---

## 6. Registry Rules

新增链路前必须回答：
1. 输入文件来自哪里？
2. 是否已有上游链路能复用？
3. 硬约束是什么？
4. fallback 是什么？
5. reviewer 是谁？
6. 运行属于哪个 tier？

若回答不完整，则不应进入编码。
