# AGENTS.md

## Mission
- RareEarth Sentinel 是情报哨兵，不是资讯搬运站。
- 目标优先级：稳定 > 可信 > 可运营 > 可变现。
- 所有 AI 输出必须区分事实、推断、建议，禁止混写。

## Core Product Priorities
1. 每日抓取/分析/存储主链路稳定。
2. 首页/日报/Top3 口径一致且不过时。
3. 社媒与三版本输出可审核、可回退、可复盘。
4. 新功能默认独立运行，稳定后再考虑并主流程。

## Output Chains
- Extractor: `src/extractor.py`
- Premium brief: `src/premium/briefs.py`, `src/premium/pipeline.py`
- Social copy: `src/social_copy_ai.py`
- Homepage topic: `main.py` + `src/reporter.py`
- Content variants: `src/content_variants_ai.py`, `scripts/build_content_variants.py`

## Hard Constraints
- 输入污染必须先拦截；抓到内容 ≠ 可分析输入。
- 强约束优先于 prompt 指令；能做 validator 就不要只写提示词。
- 每次错误修复至少沉淀一种产物：文档 / validator / fallback / test / runbook。
- 高风险内容（deep_dive、对外发布文案）默认人工审核，不自动外发。
- 新链路先独立命令或独立 timer，不直接塞进默认主流程。

## Forbidden Patterns
- 不得把建议句写成证据锚点。
- 不得把强判断放进“原文可证”。
- 不得使用过时绝对表述（stale claim）。
- 不得因补丁方便而破坏现有稳定链路。
- 不得把口头经验长期留在聊天里而不入仓库。

## Build / Run Order
- 主流程：`python3 main.py`
- 重建静态页：`python3 main.py --rebuild`
- 三版本：`python3 main.py --build-content-variants`
- 三版本 admin：`python3 scripts/build_admin_content_variants.py`
- 数据集：`python3 main.py --build-datasets`
- 产品页：`python3 main.py --build-products`

## Error -> Rule Feedback Protocol
- 发现错误 → 记录 symptom → 归因 root cause → 选择 fix type → 提交到仓库。
- 同类错误重复出现两次以上，必须升级为仓库规则或 validator。

## Deep Links
- Main harness doc: `docs/HARNESS_ENGINEERING_V1.md`
- Output chains: `docs/OUTPUT_CHAINS_V1.md`
- Error feedback protocol: `docs/ERROR_FEEDBACK_PROTOCOL_V1.md`
- Ops handoff: `docs/HANDOFF.md`
