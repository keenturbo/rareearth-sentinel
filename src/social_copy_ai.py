import concurrent.futures
import hashlib
import json
import os
import re
from datetime import datetime, timezone

import google.generativeai as genai

from .utils import extract_json

GENERATOR_VERSION = "v2.8.10"

FORBIDDEN_PROMISE_WORDS = [
    "稳赚",
    "必涨",
    "保本",
    "稳赚不赔",
    "guaranteed",
    "sure win",
    "risk-free",
]

SUBJECT_HINTS_ZH = [
    "投资者",
    "投资动作",
    "投资",
    "采购方",
    "采购",
    "政策",
    "政府",
    "企业",
    "供应链团队",
]
SUBJECT_HINTS_EN = [
    "investor",
    "investor action",
    "procurement",
    "policy",
    "policy actor",
    "government",
    "company",
    "supply-chain",
]
ADVICE_HINTS = [
    "超配",
    "减持",
    "规避",
    "布局",
    "配置",
    "优先跟踪",
    "buy",
    "sell",
    "overweight",
    "underweight",
    "trim",
    "reduce",
    "add",
    "hedge",
    "rotate",
    "de-risk",
]

STALE_ABSOLUTE_PATTERNS_ZH = [
    r"中国正式暂停对美稀土出口",
    r"中国暂停对美稀土出口",
    r"中国(?:已)?(?:全面)?停止对美稀土出口",
    r"对美稀土(?:出口)?全面禁运",
    r"对美稀土出口已暂停",
]
STALE_ABSOLUTE_PATTERNS_EN = [
    r"china has formally suspended rare earth exports to the united states",
    r"china has (?:fully )?halted rare earth exports to the united states",
    r"complete embargo on rare earth exports to the us",
]

PERSONA_ZH = (
    "你是『关键矿产情报主笔（给机构投资者写晨报）』，"
    "表达要求：短句、事实优先、不喊口号、先结论后依据。"
)
PERSONA_EN = (
    "You are the lead critical-minerals intelligence editor for institutional investors. "
    "Write in concise, decision-oriented, evidence-first prose."
)


REQUIRED_CN_LABELS = ["核心事实：", "影响路径：", "投资动作：", "反方风险：", "证据锚点：", "⚠️ 风险提示："]
REQUIRED_EN_LABELS = ["Core fact:", "Impact path:", "Investor action:", "Counter-risk:", "Evidence anchor:", "⚠️ Risk note:"]



def _iso_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")



def _clean_text(text):
    text = str(text or "")
    text = text.replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = text.replace("…", "")
    text = text.replace("...", "")
    text = text.replace("TL;DR", "")
    text = text.replace("Key angles", "")
    text = text.replace("要点 •", "")
    return text.strip()



def _has_subject(text, lang="zh"):
    low = str(text or "").lower()
    hints = SUBJECT_HINTS_ZH if lang == "zh" else SUBJECT_HINTS_EN
    return any(h.lower() in low for h in hints)



def _has_forbidden(text):
    low = str(text or "").lower()
    return any(w.lower() in low for w in FORBIDDEN_PROMISE_WORDS)



def _extract_line(text, prefixes):
    for line in str(text or "").splitlines():
        raw = line.strip()
        for p in prefixes:
            if raw.lower().startswith(p.lower()):
                return raw[len(p):].strip(" ：:")
    return ""



def _contains_stale_absolute_claim(text):
    t = str(text or "")
    low = t.lower()
    if any(re.search(p, t, re.I) for p in STALE_ABSOLUTE_PATTERNS_ZH):
        return True
    if any(re.search(p, low, re.I) for p in STALE_ABSOLUTE_PATTERNS_EN):
        return True
    return False



def _has_fresh_anchor(text, lang="zh"):
    t = str(text or "")
    low = t.lower()
    if not t:
        return False

    if lang == "zh":
        source_hints = ["根据", "据", "最新", "当前", "公开", "公告", "披露", "商务部", "海关", "会议"]
        time_hints = [r"20\d{2}", r"\d+月", r"\d+日", r"截至", r"同比", r"环比", r"本周", r"本月"]
        return any(h in t for h in source_hints) or any(re.search(p, t) for p in time_hints)

    source_hints = ["according to", "based on", "latest", "current", "public", "announced", "reported", "disclosure"]
    time_hints = [r"20\d{2}", r"as of", r"q[1-4]", r"month", r"week", r"yoy", r"mom"]
    return any(h in low for h in source_hints) or any(re.search(p, low) for p in time_hints)



def _sanitize_freshness(text, lang="zh"):
    t = str(text or "")
    if lang == "zh":
        t = re.sub(
            r"中国正式暂停对美稀土出口",
            "根据最新公开政策信息，中美稀土贸易仍受许可与政策博弈影响",
            t,
        )
        t = re.sub(
            r"中国暂停对美稀土出口",
            "根据最新公开政策信息，中美稀土贸易仍受许可与政策博弈影响",
            t,
        )
        t = re.sub(
            r"中国(?:已)?(?:全面)?停止对美稀土出口",
            "根据最新公开政策信息，中美稀土贸易仍受许可与政策博弈影响",
            t,
        )
        t = re.sub(
            r"对美稀土(?:出口)?全面禁运",
            "根据最新公开政策信息，中美稀土贸易仍受许可与政策博弈影响",
            t,
        )
        t = re.sub(
            r"对美稀土出口已暂停",
            "根据最新公开政策信息，中美稀土贸易仍受许可与政策博弈影响",
            t,
        )
        return t

    t = re.sub(
        r"China has formally suspended rare earth exports to the United States",
        "Based on current public policy disclosures, China-US rare earth trade remains subject to licensing and policy frictions",
        t,
        flags=re.I,
    )
    t = re.sub(
        r"China has (?:fully )?halted rare earth exports to the United States",
        "Based on current public policy disclosures, China-US rare earth trade remains subject to licensing and policy frictions",
        t,
        flags=re.I,
    )
    t = re.sub(
        r"complete embargo on rare earth exports to the US",
        "heightened policy and licensing constraints in China-US rare earth trade",
        t,
        flags=re.I,
    )
    return t



def _looks_like_numbered_scenario(text, lang="zh"):
    t = str(text or "").strip()
    if not t:
        return False
    low = t.lower()
    if re.match(r"^\s*(\d+[\.)]|[一二三四五六七八九十]+[、\.])", t):
        return True
    if lang == "zh":
        if ("触发条件" in t and "概率" in t) or re.search(r"概率\s*\d+%?", t):
            return True
    else:
        if ("trigger condition" in low and "probability" in low) or re.search(r"\bprobability\s*\d+%?", low):
            return True
    return False



def _harden_core_fact_line(text, lang="zh"):
    lines = str(text or "").splitlines()
    out = []
    if lang == "zh":
        prefix = "核心事实："
        safe_fact = "根据最新公开政策信息，中美稀土贸易仍受许可与政策博弈影响，供给不确定性上升。"
    else:
        prefix = "Core fact:"
        safe_fact = "Based on current public policy disclosures, China-US rare earth trade remains subject to licensing and policy frictions, with elevated supply uncertainty."

    for ln in lines:
        raw = ln.strip()
        if raw.startswith(prefix):
            core = raw[len(prefix):].strip()
            core = _sanitize_freshness(core, lang=lang)
            if _contains_stale_absolute_claim(core) or not _has_fresh_anchor(core, lang=lang):
                raw = f"{prefix} {safe_fact}"
            else:
                raw = f"{prefix} {core}"
        out.append(raw)
    return "\n".join(out).strip()



def _is_fact_sentence(text):
    t = str(text or "")
    if not t:
        return False
    if any(k in t.lower() for k in ADVICE_HINTS):
        return False
    fact_tokens = [
        r"\d",
        r"%",
        r"亿美元",
        r"亿元",
        r"million",
        r"billion",
        r"project",
        r"tariff",
        r"duty",
        r"confirmed",
        r"announced",
        r"section\s*232",
        r"终裁",
        r"宣布",
        r"确认",
        r"启动",
        r"注资",
        r"投资",
        r"会议",
    ]
    return any(re.search(tok, t, re.I) for tok in fact_tokens)



def _word_count(text):
    return len(re.findall(r"\S+", str(text or "")))



def _char_count_no_space(text):
    return len(re.sub(r"\s+", "", str(text or "")))



def _norm_compare_any(text):
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(text or "").lower())



def _is_similar_any(a, b):
    na = _norm_compare_any(a)
    nb = _norm_compare_any(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    sa, sb = set(na), set(nb)
    union = sa | sb
    if not union:
        return False
    return (len(sa & sb) / len(union)) >= 0.72



def _clip_cn(text, limit=56):
    t = _clean_text(text)
    if len(t) <= limit:
        return t
    out = t[:limit]
    p = max(out.rfind("。"), out.rfind("；"), out.rfind("，"))
    if p >= int(limit * 0.6):
        out = out[:p]
    return out.rstrip("，。；; ")



def _clip_en_words(text, max_words=22):
    t = _clean_text(text)
    words = t.split()
    if len(words) <= max_words:
        return t
    return " ".join(words[:max_words]).rstrip(" ,;:.")



def _pick_first_non_empty(*vals):
    for v in vals:
        s = _clean_text(v)
        if s:
            return s
    return ""



def _pick_factish(text, lang="zh"):
    raw = _clean_text(text)
    if not raw:
        return ""
    if lang == "zh":
        parts = re.split(r"[。！？；\n]+", raw)
    else:
        parts = re.split(r"[.!?;\n]+", raw)
    parts = [_clean_text(p) for p in parts if _clean_text(p)]
    for p in parts:
        if _is_fact_sentence(p) and not any(k in p.lower() for k in ADVICE_HINTS):
            return p
    return parts[0] if parts else ""



def _build_rule_fallback_bundle(top3_data, daily_zh, daily_en, fingerprint, reason="rule_fallback"):
    items = (top3_data or {}).get("items") or []
    first = items[0] if items else {}

    zh_head = _pick_first_non_empty((daily_zh or {}).get("headline"), first.get("title_zh"), first.get("title"))
    en_head = _pick_first_non_empty((daily_en or {}).get("headline"), first.get("title_en"), first.get("title"))
    zh_val = _pick_first_non_empty(first.get("value_zh"), first.get("report_contains_zh"), zh_head)
    en_val = _pick_first_non_empty(first.get("value_en"), first.get("report_contains_en"), en_head)

    zh_core = _sanitize_freshness(_clip_cn(zh_head or zh_val, 52), lang="zh")
    if not _has_fresh_anchor(zh_core, lang="zh"):
        zh_core = "根据最新公开政策信息，中美稀土贸易仍受许可与政策博弈影响，供给不确定性上升。"
    zh_impact = _clip_cn(_pick_factish(zh_val, lang="zh") or "供应链重排速度提升，海外溢价与国内重估预期同步增强。", 62)
    if re.match(r"^\s*\d+\.", zh_impact) or "概率" in zh_impact:
        zh_impact = "供应链重排速度提升，海外溢价与国内重估预期同步增强。"

    zh_evidence = _clip_cn(_pick_factish(zh_val + "。" + _pick_first_non_empty(first.get("title_zh"), ""), lang="zh") or zh_val, 72)
    if (
        _is_similar_any(zh_evidence, zh_impact)
        or _is_similar_any(zh_evidence, zh_core)
        or any(k in zh_evidence for k in ["减持", "增配", "规避", "布局", "配置", "优先跟踪", "锁定"])
        or _looks_like_numbered_scenario(zh_evidence, lang="zh")
    ):
        zh_evidence = "根据最新公开政策与市场数据，关键矿产贸易许可与关税约束仍在持续。"

    en_core = _sanitize_freshness(_clip_en_words(en_head or en_val, 18), lang="en")
    if not _has_fresh_anchor(en_core, lang="en"):
        en_core = "Based on current public policy disclosures, China-US rare earth trade remains subject to licensing and policy frictions."
    en_impact = _clip_en_words(_pick_factish(en_val, lang="en") or "Supply-chain repricing is widening external premiums and domestic revaluation expectations.", 20)
    if re.match(r"^\s*\d+\.", en_impact) or "probability" in en_impact.lower():
        en_impact = "Supply-chain repricing is widening external premiums and domestic revaluation expectations."

    en_evidence = _clip_en_words(_pick_factish(en_val + ". " + _pick_first_non_empty(first.get("title_en"), ""), lang="en") or en_val, 24)
    if (
        _is_similar_any(en_evidence, en_impact)
        or _is_similar_any(en_evidence, en_core)
        or any(k in en_evidence.lower() for k in ["buy", "sell", "overweight", "underweight", "trim", "reduce", "add", "hedge", "rotate", "de-risk"])
        or _looks_like_numbered_scenario(en_evidence, lang="en")
    ):
        en_evidence = "According to current public disclosures, trade licensing and tariff constraints in critical minerals remain in effect."

    cn_topic = "\n".join([
        f"核心事实：{zh_core}",
        f"影响路径：{zh_impact}",
        "投资动作：优先跟踪政策资金、加工产能与订单兑现节奏。",
        "反方风险：若海外供给兑现快于预期，短期溢价与估值或回撤。",
        f"证据锚点：{zh_evidence}",
        "⚠️ 风险提示：不构成投资建议，请按风险承受能力决策。",
    ]).strip()

    en_topic = "\n".join([
        f"Core fact: {en_core}",
        f"Impact path: {en_impact}",
        "Investor action: Track policy funding, processing ramp-up, and order conversion before sizing positions.",
        "Counter-risk: If non-China supply ramps faster than expected, short-term premiums and valuations may retrace.",
        f"Evidence anchor: {en_evidence}",
        "⚠️ Risk note: Not investment advice. Manage triggers and position size.",
    ]).strip()

    cn_topic = _harden_core_fact_line(cn_topic, lang="zh")
    en_topic = _harden_core_fact_line(en_topic, lang="en")

    output = {
        "generated_at": _iso_now(),
        "generator_version": GENERATOR_VERSION,
        "source_fingerprint": fingerprint,
        "cn_social": cn_topic,
        "en_social": en_topic,
        "cn_topic": cn_topic,
        "en_topic": en_topic,
        "quality_flags": {
            "subject_ok": True,
            "evidence_fact_ok": True,
            "no_profit_claim_ok": True,
            "persona_enabled": False,
            "freshness_guard_enabled": True,
        },
        "fallback_used": True,
        "validator_reason": reason,
    }
    return output



def _validate_payload(payload):
    if not isinstance(payload, dict):
        return False, "not_dict"

    required = ["cn_social", "en_social", "cn_topic", "en_topic"]
    for k in required:
        if not str(payload.get(k, "")).strip():
            return False, f"missing_{k}"

    cn_social = _clean_text(payload.get("cn_social"))
    en_social = _clean_text(payload.get("en_social"))
    cn_topic = _clean_text(payload.get("cn_topic"))
    en_topic = _clean_text(payload.get("en_topic"))

    cn_social = _sanitize_freshness(cn_social, lang="zh")
    en_social = _sanitize_freshness(en_social, lang="en")
    cn_topic = _harden_core_fact_line(_sanitize_freshness(cn_topic, lang="zh"), lang="zh")
    en_topic = _harden_core_fact_line(_sanitize_freshness(en_topic, lang="en"), lang="en")

    combined_text = cn_social + "\n" + en_social + "\n" + cn_topic + "\n" + en_topic
    if _contains_stale_absolute_claim(combined_text):
        return False, "stale_claim"

    if _has_forbidden(combined_text):
        return False, "forbidden_word"

    if not (_has_subject(cn_social + "\n" + cn_topic, lang="zh") or "投资动作：" in cn_topic):
        return False, "subject_zh"
    if not (_has_subject(en_social + "\n" + en_topic, lang="en") or "Investor action:" in en_topic):
        return False, "subject_en"

    cn_core = _extract_line(cn_social + "\n" + cn_topic, ["核心事实：", "核心事实:"])
    en_core = _extract_line(en_social + "\n" + en_topic, ["Core fact:"])
    if not _has_fresh_anchor(cn_core, lang="zh"):
        return False, "core_anchor_zh"
    if not _has_fresh_anchor(en_core, lang="en"):
        return False, "core_anchor_en"

    cn_ev = _extract_line(cn_social + "\n" + cn_topic, ["证据锚点：", "证据锚点:"])
    en_ev = _extract_line(en_social + "\n" + en_topic, ["Evidence anchor:", "Evidence anchors:"])
    if _looks_like_numbered_scenario(cn_ev, lang="zh"):
        return False, "evidence_zh_numbered"
    if _looks_like_numbered_scenario(en_ev, lang="en"):
        return False, "evidence_en_numbered"
    if not _is_fact_sentence(cn_ev):
        return False, "evidence_zh"
    if not _is_fact_sentence(en_ev):
        return False, "evidence_en"
    if any(k in cn_ev for k in ["减持", "增配", "规避", "布局", "配置", "优先跟踪"]):
        return False, "evidence_zh_action"
    if any(k in en_ev.lower() for k in ["buy", "sell", "overweight", "underweight", "trim", "reduce", "add", "hedge", "rotate", "de-risk"]):
        return False, "evidence_en_action"

    cn_impact = _extract_line(cn_social + "\n" + cn_topic, ["影响路径：", "影响路径:"])
    en_impact = _extract_line(en_social + "\n" + en_topic, ["Impact path:"])
    if re.match(r"^\s*\d+\.", cn_impact):
        return False, "impact_cn_numbered"
    if re.match(r"^\s*\d+\.", en_impact):
        return False, "impact_en_numbered"
    if _is_similar_any(cn_impact, cn_ev):
        return False, "dup_cn_impact_evidence"
    if _is_similar_any(en_impact, en_ev):
        return False, "dup_en_impact_evidence"
    if _is_similar_any(cn_core, cn_ev):
        return False, "dup_cn_core_evidence"
    if _is_similar_any(en_core, en_ev):
        return False, "dup_en_core_evidence"

    if not all(lbl in cn_topic for lbl in REQUIRED_CN_LABELS):
        return False, "missing_cn_labels"
    if not all(lbl in en_topic for lbl in REQUIRED_EN_LABELS):
        return False, "missing_en_labels"

    for lbl in REQUIRED_CN_LABELS[:-1]:
        if cn_topic.count(lbl) > 1:
            return False, "dup_cn_label"
    for lbl in REQUIRED_EN_LABELS[:-1]:
        if en_topic.count(lbl) > 1:
            return False, "dup_en_label"

    if re.search(r"要点\s*[•\-]", cn_topic):
        return False, "noise_cn"
    if "tl;dr" in en_topic.lower():
        return False, "noise_en"

    if _char_count_no_space(cn_social) > 220:
        return False, "cn_social_too_long"
    if _word_count(en_social) > 120:
        return False, "en_social_too_long"

    if "..." in en_social or "..." in en_topic:
        return False, "ellipsis_en"

    payload["cn_social"] = cn_social
    payload["en_social"] = en_social
    payload["cn_topic"] = cn_topic
    payload["en_topic"] = en_topic
    return True, "ok"



def _compute_fingerprint(top3_data, daily_zh, daily_en, report_ids):
    base = {
        "generator_version": GENERATOR_VERSION,
        "top3": top3_data or {},
        "daily_zh": daily_zh or {},
        "daily_en": daily_en or {},
        "report_ids": sorted(list(report_ids or []))[:5000],
    }
    raw = json.dumps(base, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()



def _cache_path(config):
    return os.path.join(config.data_dir, "status", "social_copy_generated.json")



def _load_cache(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}



def _save_cache(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)



def _cache_publishable(cached):
    if not isinstance(cached, dict):
        return False
    if not all(cached.get(k) for k in ("cn_social", "en_social", "cn_topic", "en_topic")):
        return False
    combined = "\n".join([
        str(cached.get("cn_social") or ""),
        str(cached.get("en_social") or ""),
        str(cached.get("cn_topic") or ""),
        str(cached.get("en_topic") or ""),
    ])
    if _contains_stale_absolute_claim(combined):
        return False

    cn_core = _extract_line(
        str(cached.get("cn_topic") or "") + "\n" + str(cached.get("cn_social") or ""),
        ["核心事实：", "核心事实:"],
    )
    en_core = _extract_line(
        str(cached.get("en_topic") or "") + "\n" + str(cached.get("en_social") or ""),
        ["Core fact:"],
    )
    if not _has_fresh_anchor(cn_core, lang="zh"):
        return False
    if not _has_fresh_anchor(en_core, lang="en"):
        return False

    return True



def _cache_usable(cached, fingerprint, strict=True):
    if not _cache_publishable(cached):
        return False
    if not strict:
        return True
    if cached.get("source_fingerprint") != fingerprint:
        return False
    if str(cached.get("generator_version") or "") != GENERATOR_VERSION:
        return False
    return True



def _build_prompt(top3_data, daily_zh, daily_en):
    top_items = (top3_data or {}).get("items") or []
    brief_items = []
    for item in top_items[:3]:
        brief_items.append(
            {
                "title_zh": item.get("title_zh") or item.get("title"),
                "title_en": item.get("title_en") or item.get("title"),
                "value_zh": item.get("value_zh"),
                "value_en": item.get("value_en"),
                "report_contains_zh": item.get("report_contains_zh"),
                "report_contains_en": item.get("report_contains_en"),
                "risk_score": item.get("risk_score"),
                "impact_score": item.get("impact_score"),
            }
        )

    payload = {
        "top3": brief_items,
        "daily_zh": {
            "headline": (daily_zh or {}).get("headline"),
            "body": (daily_zh or {}).get("body"),
            "bullets": (daily_zh or {}).get("bullets"),
            "strategy": (daily_zh or {}).get("strategy") or (daily_zh or {}).get("strategies"),
            "tactics": (daily_zh or {}).get("tactics") or (daily_zh or {}).get("tactical"),
        },
        "daily_en": {
            "headline": (daily_en or {}).get("headline"),
            "body": (daily_en or {}).get("body"),
            "bullets": (daily_en or {}).get("bullets"),
            "strategy": (daily_en or {}).get("strategy") or (daily_en or {}).get("strategies"),
            "tactics": (daily_en or {}).get("tactics") or (daily_en or {}).get("tactical"),
        },
    }

    return f"""
Role (Chinese persona): {PERSONA_ZH}
Role (English persona): {PERSONA_EN}

You are generating final publish-ready social copy and homepage topic copy for a rare-earth intelligence product.
Return ONLY strict JSON (no markdown) with keys:
- cn_social
- en_social
- cn_topic
- en_topic

Hard rules:
1) Subject must be explicit (investor/procurement/policy actor).
2) Evidence anchor must be a fact sentence (entity + event + number/time), never an action suggestion.
3) Never use profit-guarantee language.
4) No links in output text.
5) Remove TL;DR/noise wording.
6) English must be complete sentences (no truncation).
7) No machine-assembly artifacts: no "要点 •", no duplicated labels, no repeated "影响路径".
8) Avoid absolute stale claims like "formally suspended exports" unless a dated official source is explicitly provided.
9) Core fact must include a time/source qualifier (e.g., 根据/据/最新公开信息 or according to/based on/current disclosure).
10) Impact path and Evidence anchor must be non-duplicate; do not repeat the same sentence across fields.
11) Evidence anchor must not begin with numbered scenario bullets (e.g., "1.", "2.").

Length:
- cn_social <= 220 Chinese chars (excluding line breaks)
- en_social <= 120 words
- cn_topic should be concise but richer than social copy
- en_topic should be concise but richer than social copy

Required field structure in each text:
CN social/topic lines should include exactly once:
- 核心事实：...
- 影响路径：...
- 投资动作：...
- 反方风险：...
- 证据锚点：...
- ⚠️ 风险提示：...

EN social/topic lines should include exactly once:
- Core fact: ...
- Impact path: ...
- Investor action: ...
- Counter-risk: ...
- Evidence anchor: ...
- ⚠️ Risk note: ...

Input data JSON:
{json.dumps(payload, ensure_ascii=False)}
"""



def _generate_with_timeout(model, prompt, timeout_seconds):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(model.generate_content, prompt)
        return future.result(timeout=timeout_seconds)



def _response_text(resp):
    try:
        return resp.text or ""
    except Exception:
        pass
    try:
        cand = resp.candidates[0] if getattr(resp, "candidates", None) else None
        parts = getattr(getattr(cand, "content", None), "parts", []) if cand else []
        return "".join(getattr(p, "text", "") for p in parts if getattr(p, "text", ""))
    except Exception:
        return ""



def generate_social_copy_bundle(config, top3_data, daily_zh, daily_en, report_ids, timeout_seconds=8):
    """
    Returns dict with cn_social/en_social/cn_topic/en_topic and metadata,
    never None (v2.8.4): returns validated model output, cache fallback, or rule fallback.
    """
    fingerprint = _compute_fingerprint(top3_data, daily_zh, daily_en, report_ids)
    cache_file = _cache_path(config)
    cached = _load_cache(cache_file)
    if _cache_usable(cached, fingerprint, strict=True):
        return cached

    api_key = str(getattr(config, "gemini_api_key", "") or "").strip()
    model_name = str(getattr(config, "gemini_model", "") or "").strip()

    def _save_and_return_fallback(reason):
        fb = _build_rule_fallback_bundle(top3_data, daily_zh, daily_en, fingerprint, reason=reason)
        _save_cache(cache_file, fb)
        return fb

    if not api_key or not model_name:
        if _cache_usable(cached, fingerprint, strict=False):
            return cached
        return _save_and_return_fallback("no_api")

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name, generation_config={"temperature": 0.2})
        prompt = _build_prompt(top3_data, daily_zh, daily_en)
        resp = _generate_with_timeout(model, prompt, timeout_seconds=max(20, int(timeout_seconds or 8)))
        raw = _response_text(resp)
        data = extract_json(raw)
        ok, reason = _validate_payload(data)
        if not ok:
            if _cache_usable(cached, fingerprint, strict=False):
                return cached
            return _save_and_return_fallback(f"validator_{reason}")

        output = {
            "generated_at": _iso_now(),
            "generator_version": GENERATOR_VERSION,
            "source_fingerprint": fingerprint,
            "cn_social": data.get("cn_topic") or data.get("cn_social", ""),
            "en_social": data.get("en_topic") or data.get("en_social", ""),
            "cn_topic": data.get("cn_topic") or data.get("cn_social", ""),
            "en_topic": data.get("en_topic") or data.get("en_social", ""),
            "quality_flags": {
                "subject_ok": True,
                "evidence_fact_ok": True,
                "no_profit_claim_ok": True,
                "persona_enabled": True,
                "freshness_guard_enabled": True,
            },
            "fallback_used": False,
            "validator_reason": reason,
        }
        _save_cache(cache_file, output)
        return output
    except Exception:
        if _cache_usable(cached, fingerprint, strict=False):
            return cached
        return _save_and_return_fallback("gemini_exception")
