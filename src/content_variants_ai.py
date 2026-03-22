import concurrent.futures
import hashlib
import json
import re
from datetime import datetime, timezone

import google.generativeai as genai

from .utils import extract_json

GENERATOR_VERSION = "variants-v1.3"

FORBIDDEN_PROMISE_WORDS = [
    "稳赚",
    "必涨",
    "保本",
    "稳赚不赔",
    "稳赚不赔",
    "guaranteed",
    "sure win",
    "risk-free",
    "can’t lose",
]

DEEP_DIVE_REQUIRED_MARKERS = ["原文可证", "合理推断", "待验证点"]
DEEP_DIVE_HARD_JUDGMENT_WORDS = [
    "必然",
    "绝对",
    "注定",
    "将直接",
    "已经形成",
    "完全替代",
    "彻底摆脱",
    "等同于禁运",
    "系统性瘫痪",
]
DEEP_DIVE_CONSERVATIVE_MARKERS = [
    "可能",
    "若",
    "倾向于",
    "仍需验证",
    "仍需观察",
    "不排除",
    "基于现有信息",
    "现有公开信息下",
    "在当前信息下",
    "初步看",
    "短期看",
    "尚待确认",
    "需继续观察",
    "需继续验证",
    "based on current information",
    "at this stage",
    "subject to verification",
    "pending confirmation",
]
ACTION_HINTS = [
    "强化",
    "推动",
    "加速",
    "监控",
    "布局",
    "锁定",
    "引导",
    "对冲",
    "增配",
    "减持",
    "关注",
    "跟踪",
    "配置",
    "建议",
    "优先",
    "trim",
    "reduce",
    "add",
    "hedge",
    "rotate",
    "de-risk",
    "buy",
    "sell",
    "overweight",
    "underweight",
    "track",
    "watch",
]
RESEARCH_LIKE_TOKENS = [
    "nature",
    "science",
    "journal",
    "study",
    "paper",
    "research",
    "university",
    "laboratory",
    "lab",
    "catalyst",
    "electrolyte",
    "通讯",
    "论文",
    "研究",
    "实验室",
    "大学",
    "期刊",
    "催化",
]
XUEQIU_OVERHEAT_WORDS = [
    "资本狂欢",
    "杀伤力",
    "版图会被重写",
    "版图将被重写",
    "盲盒",
    "王炸",
    "爆表",
    "炸裂",
    "重写全球",
    "重写版图",
    "狂飙",
]

STYLE_MODES = [
    "争议切入",
    "变量切入",
    "产业链切入",
    "资本重估切入",
]

PERSONA = (
    "你是 RareEarth Sentinel 的长内容主笔，面向投资者与供应链决策者写作。"
    "要求：事实优先、推断克制、句子自然、避免模板腔。"
)


def _iso_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(text):
    s = str(text or "")
    s = s.replace("\r", "\n")
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def _clip_text(text, limit):
    s = _clean_text(text)
    if not limit or len(s) <= limit:
        return s
    return s[:limit].rstrip("，。；;,. ") + "…"


def _normalize_compare(text):
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(text or "").lower())


def _is_similar(a, b):
    na = _normalize_compare(a)
    nb = _normalize_compare(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    sa, sb = set(na), set(nb)
    union = sa | sb
    if not union:
        return False
    return (len(sa & sb) / len(union)) >= 0.78


def _safe_list(value, limit=5):
    if value is None:
        return []
    if isinstance(value, list):
        items = [str(v).strip() for v in value if str(v).strip()]
    elif isinstance(value, str):
        items = [v.strip("-• ").strip() for v in value.splitlines() if v.strip()]
    else:
        items = []
    deduped = []
    for item in items:
        if not any(_is_similar(item, old) for old in deduped):
            deduped.append(item)
    return deduped[:limit]


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _extract_numbers(text, limit=6):
    s = _clean_text(text)
    if not s:
        return []
    parts = re.split(r"[\n。！？；;]+", s)
    picked = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if re.search(r"\d", part) and not any(_is_similar(part, old) for old in picked):
            picked.append(_clip_text(part, 120))
        if len(picked) >= limit:
            break
    return picked


def _safe_response_text(response):
    try:
        return response.text or ""
    except Exception:
        pass
    try:
        cand = response.candidates[0] if getattr(response, "candidates", None) else None
        parts = getattr(getattr(cand, "content", None), "parts", []) if cand else []
        return "".join(getattr(part, "text", "") for part in parts if getattr(part, "text", ""))
    except Exception:
        return ""


def _generate_with_timeout(model, prompt, timeout_seconds):
    if not timeout_seconds or timeout_seconds <= 0:
        return model.generate_content(prompt)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(model.generate_content, prompt)
        return future.result(timeout=timeout_seconds)


def _merge_unique(target, values, limit=6):
    for value in values:
        s = _clean_text(value)
        if not s:
            continue
        if any(_is_similar(s, old) for old in target):
            continue
        target.append(s)
        if len(target) >= limit:
            break
    return target


def _content_excerpt(record, limit=2200):
    content = _clean_text(record.get("content"))
    return _clip_text(content, limit)


def _extract_premium_block(premium_payload):
    if not isinstance(premium_payload, dict):
        return {}
    if any(k in premium_payload for k in ("conclusion", "fact_checks", "tracking_nodes")):
        return premium_payload
    for key in ("zh", "brief_zh", "premium", "premium_zh"):
        value = premium_payload.get(key)
        if isinstance(value, dict) and any(k in value for k in ("conclusion", "fact_checks", "tracking_nodes")):
            return value
    return {}


def _style_mode(record_id):
    text = str(record_id or "default")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    idx = int(digest[:8], 16) % len(STYLE_MODES)
    return STYLE_MODES[idx]


def _looks_like_actionable(text):
    s = _clean_text(text)
    if not s:
        return False
    return any(token.lower() in s.lower() for token in ACTION_HINTS)


def _normalize_inference_sentence(text):
    s = _clean_text(text)
    if not s:
        return ""
    s = re.sub(r"^(基于现有信息[，,\s]*){2,}", "基于现有信息，", s)
    s = re.sub(r"^(现有公开信息下[，,\s]*){2,}", "现有公开信息下，", s)
    s = re.sub(r"^(based on current information[,:\s]*){2,}", "Based on current information, ", s, flags=re.I)
    s = re.sub(r"([，,]){2,}", "，", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip("，, ")


def _soften_inference(text):
    s = _normalize_inference_sentence(text)
    if not s:
        return ""
    replacements = [
        ("将导致", "可能导致"),
        ("将带来", "可能带来"),
        ("已形成", "有形成趋势"),
        ("直接挑战", "可能形成挤压"),
        ("绝对垄断", "仍占据显著主导地位"),
        ("系统性瘫痪", "明显的系统性压力"),
        ("等同于禁运", "接近禁运式压力"),
        ("将实质性延缓", "可能延缓"),
        ("将明显削弱", "可能削弱"),
        ("将打破", "可能改变"),
        ("必然导致", "可能导致"),
        ("注定会", "可能会"),
        ("彻底摆脱", "逐步降低依赖"),
        ("完全替代", "部分替代"),
        ("will materially delay", "may delay"),
        ("will directly", "may directly"),
        ("will significantly", "may significantly"),
        ("inevitably", "may"),
        ("fully replace", "partially replace"),
    ]
    for old, new in replacements:
        s = s.replace(old, new)
    s = _normalize_inference_sentence(s)
    if not any(marker.lower() in s.lower() for marker in DEEP_DIVE_CONSERVATIVE_MARKERS):
        s = "基于现有信息，" + s
        s = s.replace("基于现有信息，可能", "可能")
    return s


def _normalize_unknown(text):
    s = _clean_text(text)
    if not s:
        return ""
    s = re.sub(r"^(已验证|待证实|推断|Verified|Unverified|Inferred)[:：]\s*", "", s, flags=re.I)
    return s


def _is_research_like_fact_pack(fact_pack):
    action = _clean_text(fact_pack.get("action_type")).lower()
    if action in {"discovery", "research", "technology"}:
        return True
    blob = " ".join(
        [
            str(fact_pack.get("title") or ""),
            str(fact_pack.get("summary_zh") or ""),
            str((fact_pack.get("premium") or {}).get("conclusion") or ""),
        ]
    ).lower()
    return any(token in blob for token in RESEARCH_LIKE_TOKENS)


def _looks_like_softened_inference(text, unknown_text=""):
    s = _normalize_inference_sentence(text)
    low = s.lower()
    if not s:
        return False
    if _contains_conservative_markers(s):
        return True
    pattern_hits = [
        r"若[^。；\n]+",
        r"如果[^。；\n]+",
        r"取决于",
        r"需看",
        r"要看",
        r"后续需看",
        r"关键在于.*是否",
        r"是否[^。；\n]+仍",
        r"仍待",
        r"still depends on",
        r"will depend on",
        r"subject to",
        r"remains contingent on",
    ]
    if any(re.search(p, low, re.I) for p in pattern_hits):
        return True
    if _section_line_count(unknown_text) >= 2 and any(tok in low for tok in ["是否", "取决于", "需看", "要看", "depends", "contingent"]):
        return True
    return False


def _contains_conservative_markers(text):
    s = str(text or "").lower()
    return any(marker.lower() in s for marker in DEEP_DIVE_CONSERVATIVE_MARKERS)


def _is_xueqiu_overheated_style(title, content):
    blob = " ".join([str(title or ""), str(content or "")]).lower()
    if any(token.lower() in blob for token in XUEQIU_OVERHEAT_WORDS):
        return True
    if re.search(r"[？?].*[？?]", str(title or "")):
        return True
    if re.search(r"会被重写吗|会改写吗|谁会赢|是不是要", str(title or "")):
        return True
    return False


def _cool_down_xueqiu_style(title, content):
    repl = [
        ("资本狂欢", "资本重估"),
        ("杀伤力", "影响力"),
        ("版图会被重写吗", "版图将如何变化"),
        ("版图将被重写", "版图正在变化"),
        ("重写全球", "影响全球"),
        ("重写版图", "改变版图"),
        ("盲盒", "新变量"),
        ("王炸", "关键变量"),
        ("爆表", "上升"),
        ("炸裂", "显著"),
        ("狂飙", "走强"),
    ]
    new_title = _clean_text(title)
    new_content = _clean_text(content)
    changed = False
    for old, new in repl:
        if old in new_title:
            new_title = new_title.replace(old, new)
            changed = True
        if old in new_content:
            new_content = new_content.replace(old, new)
            changed = True
    new_title = re.sub(r"[？?]+$", "", new_title).strip()
    new_title = new_title.replace("全球稀土版图将如何变化", "全球稀土版图变化的新变量")
    if new_title != _clean_text(title):
        changed = True
    return new_title, new_content, changed


def _split_fact_layers(record, premium_payload=None):
    premium = _extract_premium_block(premium_payload)

    hard_facts = []
    hard_facts = _merge_unique(hard_facts, _safe_list(record.get("key_evidence"), limit=4), limit=6)
    hard_facts = _merge_unique(hard_facts, _safe_list(premium.get("fact_checks"), limit=4), limit=6)
    hard_facts = _merge_unique(hard_facts, _safe_list(premium.get("key_numbers"), limit=4), limit=6)
    if not hard_facts:
        hard_facts = _merge_unique(hard_facts, _extract_numbers(record.get("summary_zh"), limit=3), limit=6)

    soft_inferences = []
    for candidate in [
        _clean_text(record.get("expert_summary")),
        _clean_text(premium.get("conclusion")),
    ]:
        if not candidate:
            continue
        if _looks_like_actionable(candidate):
            continue
        candidate = _soften_inference(candidate)
        if candidate:
            _merge_unique(soft_inferences, [candidate], limit=5)

    unknowns = []
    unknowns = _merge_unique(unknowns, [_normalize_unknown(x) for x in _safe_list(record.get("assumptions"), limit=3)], limit=5)
    unknowns = _merge_unique(unknowns, [_normalize_unknown(x) for x in _safe_list(premium.get("confidence_notes"), limit=3)], limit=5)
    if not unknowns:
        unknowns = [
            "原文未覆盖更多执行细节，仍需跟踪后续项目、政策或资金落地。",
            "关键时间表、产能兑现与政策执行强度仍待验证。",
        ]

    return {
        "hard_facts": hard_facts[:6],
        "soft_inferences": soft_inferences[:5],
        "unknowns": unknowns[:5],
    }


def build_fact_pack(config, record, premium_payload=None):
    premium = _extract_premium_block(premium_payload)
    grounding = _split_fact_layers(record, premium)
    summary_zh = _clean_text(record.get("summary_zh"))
    expert_summary = _soften_inference(record.get("expert_summary"))
    title = _clean_text(record.get("title"))
    key_evidence = _safe_list(record.get("key_evidence"), limit=4)
    assumptions = _safe_list(record.get("assumptions"), limit=4)

    if not key_evidence:
        key_evidence = grounding.get("hard_facts", [])[:3]
    if not assumptions:
        assumptions = grounding.get("unknowns", [])[:3]

    pack = {
        "record_id": str(record.get("id") or "").strip(),
        "title": title,
        "url": str(record.get("resolved_url") or record.get("url") or "").strip(),
        "content_excerpt": _content_excerpt(record, limit=getattr(config, "content_variants_max_content_chars", 2200)),
        "summary_zh": summary_zh,
        "expert_summary": expert_summary,
        "risk_score": _safe_int(record.get("risk_score"), 0),
        "importance_score": _safe_int(record.get("importance_score"), 0),
        "country_primary": _clean_text(record.get("country_primary")),
        "action_type": _clean_text(record.get("action_type")),
        "minerals": _safe_list(record.get("minerals"), limit=8),
        "entities": _safe_list(record.get("entities"), limit=8),
        "key_evidence": key_evidence,
        "assumptions": assumptions,
        "recommendations_strategy": _safe_list(record.get("recommendations_strategy"), limit=4),
        "recommendations_tactical": _safe_list(record.get("recommendations_tactical"), limit=4),
        "premium": {
            "conclusion": _soften_inference(premium.get("conclusion")),
            "fact_checks": _safe_list(premium.get("fact_checks"), limit=4),
            "fact_sources": _safe_list(premium.get("fact_sources"), limit=4),
            "key_numbers": _safe_list(premium.get("key_numbers"), limit=4),
            "tracking_nodes": _safe_list(premium.get("tracking_nodes"), limit=4),
            "confidence_notes": _safe_list(premium.get("confidence_notes"), limit=4),
            "value_proposition": _clean_text(premium.get("value_proposition")),
        },
        "grounding": grounding,
        "meta": {
            "audience_fit": _clean_text(record.get("audience_fit") or premium.get("audience_fit") or "mixed"),
            "style_mode": _style_mode(record.get("id") or title),
            "generated_at": _iso_now(),
        },
    }
    return pack


def _build_variants_prompt(fact_pack):
    payload = {
        "title": fact_pack.get("title"),
        "summary_zh": fact_pack.get("summary_zh"),
        "expert_summary": fact_pack.get("expert_summary"),
        "risk_score": fact_pack.get("risk_score"),
        "importance_score": fact_pack.get("importance_score"),
        "country_primary": fact_pack.get("country_primary"),
        "action_type": fact_pack.get("action_type"),
        "minerals": fact_pack.get("minerals"),
        "entities": fact_pack.get("entities"),
        "key_evidence": fact_pack.get("key_evidence"),
        "assumptions": fact_pack.get("assumptions"),
        "recommendations_strategy": fact_pack.get("recommendations_strategy"),
        "recommendations_tactical": fact_pack.get("recommendations_tactical"),
        "premium": fact_pack.get("premium"),
        "grounding": fact_pack.get("grounding"),
        "style_mode": (fact_pack.get("meta") or {}).get("style_mode", "变量切入"),
    }

    return f"""
角色设定：{PERSONA}

你要基于同一份事实包，产出三种不同层次的中文长内容。
只输出严格 JSON，不要 markdown，不要解释，不要补充说明。

输出 JSON 结构固定为：
{{
  "xueqiu_long": {{"title": "...", "content": "..."}},
  "sentinel_pro": {{"title": "...", "content": "..."}},
  "deep_dive": {{"title": "...", "content": "..."}}
}}

硬规则：
1. 只能基于输入事实包写作，不得发明新事实。
2. 若属于推断，必须使用“可能/若/倾向于/仍需验证”等克制表述。
3. 禁止使用“稳赚/必涨/完全替代/彻底摆脱/排华已完成”等绝对化表达。
4. 三个版本必须职责明显不同，不能只是同义改写。
5. 雪球长帖版：偏公开传播、投资讨论感强、可读性优先，不要写成研报提纲。
6. 哨兵研判版：偏结构化内部稿，必须清楚包含“事件结论 / 影响路径 / 需要跟踪”。
7. 深水区版本：必须明确写出以下三个小标题并逐段展开：
   - 原文可证
   - 合理推断
   - 待验证点
8. 深水区版本中，“原文可证”只允许使用硬事实，不得混入建议句和强判断词；“合理推断”必须保守；“待验证点”必须具体。
9. 深水区的“合理推断”段请优先使用这些表达之一：基于现有信息 / 在当前信息下 / 可能 / 若后续 / 仍需验证。
10. 如果事实包证据偏少，深水区版本必须明显保守。
11. 风格模式：{payload['style_mode']}。请让雪球长帖版避免固定模板感。
12. 内容面向中国投资者与供应链读者，默认中文。

长度建议：
- xueqiu_long.content：700-1400字
- sentinel_pro.content：500-1000字
- deep_dive.content：700-1400字

事实包：
{json.dumps(payload, ensure_ascii=False)}
"""


def _normalize_variant_item(item):
    if not isinstance(item, dict):
        return {"title": "", "content": ""}
    return {
        "title": _clip_text(_clean_text(item.get("title")), 80),
        "content": _clean_text(item.get("content")),
    }


def generate_variants(config, fact_pack, timeout_seconds=20):
    api_key = str(getattr(config, "gemini_api_key", "") or "").strip()
    model_name = str(getattr(config, "gemini_model", "") or "").strip()
    if not api_key or not model_name:
        return None

    prompt = _build_variants_prompt(fact_pack)
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name, generation_config={"temperature": 0.35})
        response = _generate_with_timeout(model, prompt, timeout_seconds=timeout_seconds)
        raw = _safe_response_text(response)
        data = extract_json(raw)
        if not isinstance(data, dict):
            return None
        return {
            "xueqiu_long": _normalize_variant_item(data.get("xueqiu_long")),
            "sentinel_pro": _normalize_variant_item(data.get("sentinel_pro")),
            "deep_dive": _normalize_variant_item(data.get("deep_dive")),
        }
    except Exception:
        return None


def _contains_forbidden_promise(text):
    low = str(text or "").lower()
    return any(word.lower() in low for word in FORBIDDEN_PROMISE_WORDS)


def _pair_overlap_score(a, b):
    na = _normalize_compare(a)
    nb = _normalize_compare(b)
    if not na or not nb:
        return 0.0
    sa, sb = set(na), set(nb)
    union = sa | sb
    if not union:
        return 0.0
    return len(sa & sb) / len(union)


def _extract_section(text, heading):
    s = str(text or "")
    if not s or heading not in s:
        return ""
    next_headings = "|".join(re.escape(h) for h in DEEP_DIVE_REQUIRED_MARKERS)
    pattern = re.compile(
        rf"(?:^|\n)\s*(?:#{{1,6}}\s*)?{re.escape(heading)}\s*(?:[:：]\s*)?(.*?)(?=(?:\n\s*(?:#{{1,6}}\s*)?(?:{next_headings})\s*(?:[:：]\s*)?(?:\n|$))|$)",
        re.S,
    )
    m = pattern.search(s)
    return m.group(1).strip() if m else ""


def _section_line_count(text):
    count = 0
    for line in str(text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("-") or s.startswith("•"):
            count += 1
    return count


def validate_variants(fact_pack, variants):
    issues = []
    if not isinstance(variants, dict):
        return False, {"passed": False, "issues": ["not_dict"], "reason": "not_dict", "fallback_used": True}

    failed_field = ""
    debug_preview = ""
    required = ["xueqiu_long", "sentinel_pro", "deep_dive"]
    for key in required:
        item = variants.get(key) or {}
        title = _clean_text(item.get("title"))
        content = _clean_text(item.get("content"))
        if not title:
            issues.append(f"missing_{key}_title")
        if not content:
            issues.append(f"missing_{key}_content")
        if _contains_forbidden_promise(title) or _contains_forbidden_promise(content):
            issues.append(f"forbidden_{key}")
            if not failed_field:
                failed_field = key
                debug_preview = _clip_text((title + "\n" + content).strip() or content or title, 220)

    if issues:
        payload = {"passed": False, "issues": issues, "reason": issues[0], "fallback_used": True}
        if failed_field:
            payload["failed_field"] = failed_field
            payload["debug_preview"] = debug_preview
        return False, payload

    x = variants["xueqiu_long"]["content"]
    s = variants["sentinel_pro"]["content"]
    d = variants["deep_dive"]["content"]

    if _pair_overlap_score(x, s) >= 0.84:
        issues.append("xueqiu_vs_pro_too_similar")
    if _pair_overlap_score(x, d) >= 0.84:
        issues.append("xueqiu_vs_deep_too_similar")
    if _pair_overlap_score(s, d) >= 0.86:
        issues.append("pro_vs_deep_too_similar")

    if "事件结论" not in s or "影响路径" not in s or "需要跟踪" not in s:
        issues.append("sentinel_pro_missing_sections")

    for marker in DEEP_DIVE_REQUIRED_MARKERS:
        if marker not in d:
            issues.append(f"deep_dive_missing_{marker}")

    deep_hard = _extract_section(d, "原文可证")
    deep_infer = _normalize_inference_sentence(_extract_section(d, "合理推断"))
    deep_unknown = _extract_section(d, "待验证点")

    if any(word in deep_hard for word in DEEP_DIVE_HARD_JUDGMENT_WORDS):
        issues.append("deep_dive_hard_facts_too_soft")
    if not _looks_like_softened_inference(deep_infer, deep_unknown):
        issues.append("deep_dive_inference_not_softened")

    evidence_count = len(_safe_list(fact_pack.get("key_evidence"), limit=5))
    if evidence_count < 2:
        if not _looks_like_softened_inference(d, deep_unknown):
            issues.append("deep_dive_not_conservative_enough")
        if _section_line_count(deep_unknown) < 2:
            issues.append("deep_dive_unknowns_too_few")

    if len(_clean_text(x)) < 250:
        issues.append("xueqiu_too_short")
    if len(_clean_text(s)) < 180:
        issues.append("sentinel_pro_too_short")
    if len(_clean_text(d)) < 260:
        issues.append("deep_dive_too_short")

    deep_issues = [i for i in issues if i.startswith("deep_dive")]
    if deep_issues and not failed_field:
        failed_field = "deep_dive"
        debug_preview = _clip_text(d, 260)

    passed = not issues
    payload = {
        "passed": passed,
        "issues": issues,
        "reason": "ok" if passed else issues[0],
        "fallback_used": not passed,
    }
    if failed_field:
        payload["failed_field"] = failed_field
        payload["debug_preview"] = debug_preview
    return passed, payload


def fallback_variants(fact_pack, reason="fallback"):
    title = fact_pack.get("title") or "关键矿产事件"
    summary = fact_pack.get("summary_zh") or fact_pack.get("expert_summary") or title
    hard_facts = _safe_list((fact_pack.get("grounding") or {}).get("hard_facts"), limit=4)
    soft_inferences = _safe_list((fact_pack.get("grounding") or {}).get("soft_inferences"), limit=3)
    unknowns = _safe_list((fact_pack.get("grounding") or {}).get("unknowns"), limit=3)
    key_evidence = _safe_list(fact_pack.get("key_evidence"), limit=3)
    recommendations = _safe_list(fact_pack.get("recommendations_strategy"), limit=2) + _safe_list(fact_pack.get("recommendations_tactical"), limit=2)
    premium = fact_pack.get("premium") or {}
    research_like = _is_research_like_fact_pack(fact_pack)

    xueqiu_lines = [
        f"【{_clip_text(title, 28)}】",
        _clip_text(summary, 180),
    ]
    if hard_facts:
        xueqiu_lines.append("我先抓三点事实：")
        for item in hard_facts[:3]:
            xueqiu_lines.append(f"- {_clip_text(item, 70)}")
    if soft_inferences:
        xueqiu_lines.append("这件事真正值得看的是：")
        for item in soft_inferences[:2]:
            xueqiu_lines.append(f"- {_clip_text(item, 80)}")
    xueqiu_lines.append("现在还不能下过满的结论，后面要继续看项目、政策和资金是否真正落地。")

    if research_like:
        pro_conclusion = premium.get("conclusion") or "该事件显示相关技术路线有新进展，但距离产业兑现仍需继续验证。"
        tracking_pool = _safe_list(premium.get("tracking_nodes"), limit=3) or unknowns or [
            "论文结果到中试放大的时间与成本仍待验证。",
            "规模化生产的一致性、寿命与良率仍待确认。",
            "后续是否出现产业化合作、示范线或资本投入值得继续跟踪。",
        ]
        pro_lines = [
            f"事件结论：{_clip_text(pro_conclusion, 120)}",
            "影响路径：",
        ]
        for item in (hard_facts[:2] + soft_inferences[:1])[:3]:
            pro_lines.append(f"- {_clip_text(item, 110)}")
        pro_lines.append("需要跟踪：")
        for item in tracking_pool[:3]:
            pro_lines.append(f"- {_clip_text(item, 110)}")
    else:
        pro_lines = [
            f"事件结论：{_clip_text(premium.get('conclusion') or summary, 120)}",
            "影响路径：",
        ]
        for item in (hard_facts[:2] + soft_inferences[:2])[:3]:
            pro_lines.append(f"- {_clip_text(item, 110)}")
        pro_lines.append("需要跟踪：")
        for item in (_safe_list(premium.get("tracking_nodes"), limit=3) or recommendations or unknowns)[:3]:
            pro_lines.append(f"- {_clip_text(item, 110)}")

    if research_like:
        deep_unknown_pool = _safe_list(premium.get("tracking_nodes"), limit=3) or unknowns or [
            "实验室结果到中试放大的时间与成本仍待验证。",
            "寿命、良率与规模化一致性是否成立仍需继续观察。",
            "后续是否出现产业合作、示范线或资本投入仍待披露。",
        ]
    else:
        deep_unknown_pool = unknowns or [
            "后续法律披露或项目更新是否改变当前判断，仍待验证。",
            "资金、审批与执行节奏是否受影响，仍需继续观察。",
            "空头指控与公司回应哪一方更接近项目实际状态，仍需结合后续披露判断。",
        ]

    deep_lines = [
        "原文可证：",
    ]
    for item in (key_evidence or hard_facts or [summary])[:3]:
        deep_lines.append(f"- {_clip_text(item, 110)}")
    deep_lines.append("合理推断：")
    infer_pool = soft_inferences or ["基于现有信息，该事件可能影响后续供应链定价与政策预期，但强度仍需结合后续披露验证。"]
    for item in infer_pool[:2]:
        deep_lines.append(f"- {_clip_text(_soften_inference(item), 110)}")
    deep_lines.append("待验证点：")
    for item in deep_unknown_pool[:3]:
        item = _normalize_unknown(item)
        item = item.replace("假设", "").replace("属实", "").replace("具有参考价值", "值得继续观察")
        deep_lines.append(f"- {_clip_text(item, 110)}")

    x_title, x_content, _ = _cool_down_xueqiu_style(title, "\n".join(xueqiu_lines).strip())

    return {
        "xueqiu_long": {
            "title": _clip_text(x_title, 40),
            "content": x_content,
        },
        "sentinel_pro": {
            "title": _clip_text(f"哨兵研判｜{title}", 50),
            "content": "\n".join(pro_lines).strip(),
        },
        "deep_dive": {
            "title": _clip_text(f"深水区｜{title}", 50),
            "content": "\n".join(deep_lines).strip(),
        },
        "_fallback_reason": reason,
    }


def generate_content_variants_bundle(config, record, premium_payload=None, timeout_seconds=20):
    fact_pack = build_fact_pack(config, record, premium_payload=premium_payload)
    variants = generate_variants(
        config,
        fact_pack,
        timeout_seconds=timeout_seconds or getattr(config, "content_variants_timeout_seconds", 20),
    )

    if not variants:
        variants = fallback_variants(fact_pack, reason="gemini_exception")
        validator = {
            "passed": False,
            "issues": ["gemini_exception"],
            "reason": "gemini_exception",
            "fallback_used": True,
        }
    else:
        x_item = variants.get("xueqiu_long") or {}
        cooled_title, cooled_content, cooled = _cool_down_xueqiu_style(x_item.get("title"), x_item.get("content"))
        if cooled or _is_xueqiu_overheated_style(x_item.get("title"), x_item.get("content")):
            variants["xueqiu_long"] = {
                "title": _clip_text(cooled_title, 80),
                "content": _clean_text(cooled_content),
            }

        passed, validator = validate_variants(fact_pack, variants)
        if not passed:
            reason = validator.get("reason") or "validator_failed"
            variants = fallback_variants(fact_pack, reason=reason)
            base_validator = {
                "passed": False,
                "issues": validator.get("issues", []),
                "reason": reason,
                "fallback_used": True,
            }
            for key in ("failed_field", "debug_preview"):
                if validator.get(key):
                    base_validator[key] = validator.get(key)
            validator = base_validator
        else:
            validator["fallback_used"] = False

    return {
        "generated_at": _iso_now(),
        "generator_version": GENERATOR_VERSION,
        "model": str(getattr(config, "gemini_model", "") or ""),
        "fact_pack": fact_pack,
        "variants": {
            "xueqiu_long": variants.get("xueqiu_long", {}),
            "sentinel_pro": variants.get("sentinel_pro", {}),
            "deep_dive": variants.get("deep_dive", {}),
        },
        "validator": validator,
    }
