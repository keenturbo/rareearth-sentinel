# Harness Engineering 应用现状（2026-03-30）

> 截止日期：2026-03-30
> 文档类型：状态快照（非实施文档）
> 面向读者：项目 owner / 未来的 Agent / 新窗口上下文恢复

---

## 一句话概括

RareEarth Sentinel 已经从"裸 prompt + 补丁驱动"进化到
"有规则入口、有输出链路治理、有错误闭环、有健康观测"的阶段。
但 Harness 应用深度在各链路间不均匀——`content_variants` 最深，
其余链路仍以早期 validator/fallback 为主。

---

## 仓库里 Harness 资产清单

### 文档层

| 文件 | 职责 |
|---|---|
| `AGENTS.md` | Agent 入口：使命、优先级、硬约束、禁止模式、运行顺序 |
| `docs/HARNESS_ENGINEERING_V1.md` | 总纲：repo as brain、constraint-first、feedback loop、automation tiers |
| `docs/OUTPUT_CHAINS_V1.md` | 5 条 AI 输出链路注册表（extractor / premium / social / homepage / variants） |
| `docs/ERROR_FEEDBACK_PROTOCOL_V1.md` | 错误分类、severity、root cause、fix type、closeout 规则 |

### 脚本层

| 文件 | 职责 |
|---|---|
| `scripts/build_harness_health.py` | 汇总 variants / social / prompt_qc 健康指标 → `data/status/harness_health.json` |
| `scripts/log_harness_feedback.py` | 结构化追加错误记录 → `data/status/harness_feedback.jsonl` |

### CLI 入口

```
python3 main.py --build-harness-health
```

### 数据产物

| 文件 | 内容 |
|---|---|
| `data/status/harness_health.json` | 最新一次健康快照（fallback_rate / today_hit / top reasons） |
| `data/status/harness_feedback.jsonl` | 累积错误回灌记录 |

---

## 各输出链路 Harness 成熟度

| 链路 | validator | fallback | observability | 多轮闭环修复 | 成熟度 |
|---|---|---|---|---|---|
| **content_variants** | ✅ 多层 | ✅ 规则+科研分支 | ✅ failed_field / debug_preview / reason 细分 | ✅ P0.1~P0.4 连续4轮 | ★★★★☆ |
| **social_copy** | ✅ | ✅ cache+rule | ⚠️ 有 validator_reason，无 debug_preview | 部分 | ★★★☆☆ |
| **extractor / prompt_qc** | ✅ input guard | ⚠️ 跳过 | ⚠️ prompt_qc_logs 有统计 | 部分 | ★★☆☆☆ |
| **homepage topic** | ⚠️ 简单 | ✅ fallback 聚合卡 | ⚠️ 无独立 debug | 少 | ★★☆☆☆ |
| **premium brief** | ⚠️ 基础 | ⚠️ 基础 | ⚠️ 有 generation_status | 少 | ★☆☆☆☆ |

---

## content_variants 链路详情（当前最深）

### 已具备的 Harness 能力

1. **fact pack 三层分离**：hard_facts / soft_inferences / unknowns
2. **validator 多维校验**：
   - forbidden promise 检测
   - deep_dive 三段标记检测（原文可证 / 合理推断 / 待验证点）
   - 保守表达判断（关键词 + 句式兜底）
   - 三版本相似度检测
   - sentinel_pro 结构完整性检测
3. **section parser**：兼容冒号格式 + Markdown heading + 无冒号标题
4. **fallback 分支**：通用 / 科研论文专用
5. **xueqiu 降温**：区分真违规（forbidden）与偏热风格（cool down）
6. **observability**：
   - `failed_field`
   - `debug_preview`
   - reason 细分（gemini_timeout / gemini_parse_error / gemini_exception）
   - `retry_attempted`
7. **Gemini 单次轻量重试**
8. **prompt payload 裁剪**（`_trim_fact_pack_for_prompt`）
9. **today-first 筛选**

### 近期热修历史（P0.1 ~ P0.4）

| 版本 | 修了什么 | 结果 |
|---|---|---|
| P0.1 | 放宽保守词 + 推断清洗 + forbidden observability | 部分通过：observability 生效，deep_dive 误杀未根治 |
| P0.2 | 句式兜底 + xueqiu 拆分 + fallback 模板轻化 | 部分通过：暴露 parser 根因 |
| P0.3 | section parser 支持 Markdown heading | **通过**：deep_dive 误杀消失，首次出现 ok |
| P0.4 | Gemini retry + reason 细分 + prompt 裁剪 | 未通过：gemini_exception 仍为黑盒，未改善通过率 |

### 当前已知遗留

- `gemini_exception` 异常细节仍不够透明（P0.5 观测补丁待做）
- 自动生成质量尚未达到"可直接发布"水平
- 当前实际发帖工作流仍以人工高质量改写为主

---

## 实际日常怎么用

### 运行

```bash
# 主情报抓取+分析+重建
python3 main.py

# 重建静态页
python3 main.py --rebuild

# 生成三版本内容（雪球/研判/深水区）
python3 main.py --build-content-variants

# 构建三版本 admin 审核页
python3 scripts/build_admin_content_variants.py

# 生成 Harness 健康快照
python3 main.py --build-harness-health

# 记录一条错误回灌
python3 scripts/log_harness_feedback.py \
  --chain content_variants \
  --severity P1 \
  --error-type gemini_exception \
  --symptom "今日5条全fallback" \
  --root-cause gemini_api_instability \
  --fix-type validator_rule \
  --status open
```

### 查看

```bash
# 健康快照
cat data/status/harness_health.json

# 最新 feedback
tail -n 3 data/status/harness_feedback.jsonl

# 最新 variants 前5条 reason
python3 -c "
import json
with open('data/status/content_variants.jsonl') as f:
    rows = [json.loads(l) for l in f if l.strip()]
for r in rows[:5]:
    v = r.get('validator',{})
    print(r.get('generated_at',''), v.get('reason',''), v.get('fallback_used'))
"
```

---

## Harness 当前价值（诚实评估）

### 已经做到的

1. **让问题可见**：不再靠记忆判断"哪里不对"，有结构化 reason / preview
2. **让错误入库**：不再只在聊天里讨论，修复沉淀为 validator / fallback / 文档
3. **让 AI 输出半透明**：尤其 content_variants，从黑盒变成可诊断
4. **帮助识别自动化边界**：明确了"自动草稿可以，直接发布还不行"

### 还没做到的

1. 全仓库统一观测面板（当前只有 JSON 快照）
2. 所有链路同等深度（variants >> 其余）
3. 自动长帖达到可直接发布质量
4. Gemini 异常完全透明

---

## 下一步优先级建议

| 优先级 | 事项 | 说明 |
|---|---|---|
| P0 | Gemini 异常观测补丁 | 把 `gemini_exception` 从黑盒变可诊断 |
| P1 | 评估是否继续投入 variants 自动长帖 | 如果实际发帖以人工为主，variants 可降级为草稿辅助 |
| P2 | 将 social_copy / homepage topic 提升到同等 Harness 深度 | 目前它们的 validator/observability 不如 variants |
| P3 | admin 可视化 Harness 健康看板 | 当前只有 JSON，后续可做页面 |

---

## 整体评分

**7.5 / 10**

已经是一个"有规则、有闭环、有观测"的 AI 内容系统，
但在生成质量、全链路覆盖、异常透明度上仍有提升空间。
