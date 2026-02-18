import concurrent.futures
import hashlib
import json
import os
import re
from datetime import datetime, timezone

import google.generativeai as genai

from .utils import extract_json

GENERATOR_VERSION = "v2.8.12.1"

FORBIDDEN_PROMISE_WORDS = [
    "稳赚",
    "必涨",
    "保本",
    "稳赚不赔",
    "guaranteed",
    "sure win",
    "risk-free",
]

ACTION_HINTS = [
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
    "你是关键矿产情报主笔，面向机构投资者写晨报。"
    "要求：短句、事实优先、可执行、避免空话。"
)

PERSONA_EN = (
    "You are the lead critical-minerals intelligence editor for institutional investors. "
    "Write concise, specific, evidence-first output with clear actionability."
)

REQUIRED_CN_LABELS = ["核心事实：", "影响路径：", "投资动作：", "反方风险：", "证据锚点：", "⚠️ 风险提示："]
REQUIRED_EN_LABELS = ["Core fact:", "Impact path:", "Investor action:", "Counter-risk:", "Evidence anchor:", "⚠️ Risk note:"]


ASSEMBLY_NOISE_PATTERNS = [
    r"要点\s*[:：]\s*[•\u2022]",
    r"要点\s*[•\u2022]",
    r"TL\s*;?\s*DR\s*:",
    r"\bKey\s*angles?\s*:",
    r"\bKey\s*points?\s*:",
    r"影响路径\s*[:：]\s*要点",
    r"证据锚点\s*[:：]\s*要点",
    r"Impact\s*path\s*:\s*Key\s*angles?",
    r"Evidence\s*anchor\s*:\s*Key\s*angles?",
]


def _iso_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(text):
    s = str(text or "")
    s = s.replace("\r", "\n")
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\bTL\s*;?\s*DR\s*:\s*", "", s, flags=re.I)
    s = re.sub(r"\bKey\s*angles?\s*:\s*", "", s, flags=re.I)
    s = re.sub(r"\bKey\s*points?\s*:\s*", "", s, flags=re.I)
    s = re.sub(r"^(要点|今日要点|要点摘要)\s*[:：]?\s*[•\u2022]?\s*", "", s, flags=re.I | re.M)
    s = s.replace("要点 •", "").replace("要点： •", "").replace("要点: •", "")
    s = s.replace("…", "").replace("...", "")
    return s.strip()


def _normalize_punct(text):
    s = str(text or "")
    s = s.replace("。;", "。").replace("；;", "；").replace(".;", ".").replace(";;", ";")
    s = s.replace("。；", "。").replace("；。", "。")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _ensure_structured_newlines(text, lang="zh"):
    t = str(text or "").replace("\r", "\n")
    if lang == "zh":
        labels = ["核心事实", "影响路径", "投资动作", "反方风险", "证据锚点", "⚠️ 风险提示"]
        for lbl in labels:
            t = re.sub(rf"\s*{re.escape(lbl)}\s*[:：]\s*", f"\n{lbl}：", t)
    else:
        labels = ["Core fact", "Impact path", "Investor action", "Counter-risk", "Evidence anchor", "⚠️ Risk note"]
        for lbl in labels:
            t = re.sub(rf"\s*{re.escape(lbl)}\s*:\s*", f"\n{lbl}: ", t, flags=re.I)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = t.lstrip("\n")
    return t


def _strip_field_noise(text, lang="zh"):
    t = _normalize_punct(_clean_text(text))
    if not t:
        return ""
    t = re.sub(r"^(要点|今日要点|要点摘要)\s*[:：]?\s*", "", t, flags=re.I)
    t = re.sub(r"^(TL\s*;?\s*DR|Key\s*angles?|Key\s*points?|Highlights|Summary)\s*[:：]?\s*", "", t, flags=re.I)
    t = re.sub(r"^[•\u2022\-*]+\s*", "", t)
    if lang == "zh":
        t = re.sub(r"^(核心事实|影响路径|投资动作|反方风险|证据锚点)\s*[:：]\s*", "", t)
    else:
        t = re.sub(r"^(Core fact|Impact path|Investor action|Counter-risk|Evidence anchor)\s*:\s*", "", t, flags=re.I)
    t = re.sub(r"\s*[•\u2022]\s*", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -—:：;；")
    return t


def _contains_assembly_noise(text):
    t = str(text or "")
    return any(re.search(p, t, re.I) for p in ASSEMBLY_NOISE_PATTERNS)


def _sameish(a, b):
    na = re.sub(r"[^\w\u4e00-\u9fff]+", "", str(a or "").lower())
    nb = re.sub(r"[^\w\u4e00-\u9fff]+", "", str(b or "").lower())
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    sa, sb = set(na), set(nb)
    union = sa | sb
    if not union:
        return False
    return (len(sa & sb) / len(union)) >= 0.72


def _contains_stale_absolute_claim(text):
    t = str(text or "")
    low = t.lower()
    if any(re.search(p, t, re.I) for p in STALE_ABSOLUTE_PATTERNS_ZH):
        return True
    if any(re.search(p, low, re.I) for p in STALE_ABSOLUTE_PATTERNS_EN):
        return True
    return False


def _sanitize_stale_claims(text):
    t = str(text or "")
    t = re.sub(
        r"中国(?:正式)?暂停对美稀土出口|中国(?:已)?(?:全面)?停止对美稀土出口|对美稀土(?:出口)?全面禁运|对美稀土出口已暂停",
        "根据今日公开政策信息，中美稀土贸易仍受许可与政策博弈影响",
        t,
        flags=re.I,
    )
    t = re.sub(
        r"China has formally suspended rare earth exports to the United States|China has (?:fully )?halted rare earth exports to the United States|complete embargo on rare earth exports to the US",
        "Based on current public policy disclosures, China-US rare earth trade remains subject to licensing and policy frictions",
        t,
        flags=re.I,
    )
    return t


def _has_forbidden(text):
    low = str(text or "").lower()
    return any(w.lower() in low for w in FORBIDDEN_PROMISE_WORDS)


def _word_count(text):
    return len(re.findall(r"\S+", str(text or "")))


def _char_count_no_space(text):
    return len(re.sub(r"\s+", "", str(text or "")))


def _first_non_empty(*vals):
    for v in vals:
        s = _clean_text(v)
        if s:
            return s
    return ""


def _first_sentence(text, lang="zh"):
    s = _normalize_punct(_clean_text(text))
    if not s:
        return ""
    if lang == "zh":
        parts = re.split(r"[。！？；\n]+", s)
    else:
        parts = re.split(r"[.!?;\n]+", s)
    parts = [p.strip() for p in parts if p.strip()]
    return _strip_field_noise(parts[0] if parts else s, lang=lang)


def _pick_sentence_with_number(text, lang="zh"):
    s = _normalize_punct(_clean_text(text))
    if not s:
        return ""
    parts = re.split(r"[。！？；\n]+", s) if lang == "zh" else re.split(r"[.!?;\n]+", s)
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if re.search(r"\d|%|亿美元|亿元|million|billion|202\d|Q[1-4]|月|日", p, re.I):
            return _strip_field_noise(p, lang=lang)
    return ""


def _pick_action(daily, lang="zh"):
    keys = ["strategy", "strategies", "tactics", "tactical", "action_items", "bullets"]
    for k in keys:
        v = (daily or {}).get(k)
        if isinstance(v, list):
            for x in v:
                t = _clean_text(x)
                if t:
                    return _strip_field_noise(t, lang=lang)
        elif isinstance(v, str):
            t = _clean_text(v)
            if t:
                return _first_sentence(t, lang=lang)
    return ""


def _is_fact_sentence(text):
    t = str(text or "")
    if not t:
        return False
    if any(k in t.lower() for k in ACTION_HINTS):
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
        r"终裁",
        r"宣布",
        r"确认",
        r"启动",
        r"注资",
    ]
    return any(re.search(tok, t, re.I) for tok in fact_tokens)


def _has_numeric_anchor(text):
    t = str(text or "")
    return bool(re.search(r"\d|%|亿美元|亿元|million|billion|202\d|Q[1-4]|月|日", t, re.I))


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


def _clip_cn(text, limit=56):
    t = _normalize_punct(_clean_text(text))
    if len(t) <= limit:
        return t
    out = t[:limit]
    p = max(out.rfind("。"), out.rfind("；"), out.rfind("，"))
    if p >= int(limit * 0.6):
        out = out[:p]
    return out.rstrip("，。；; ")


def _clip_en_words(text, max_words=22):
    t = _normalize_punct(_clean_text(text))
    words = t.split()
    if len(words) <= max_words:
        return t
    return " ".join(words[:max_words]).rstrip(" ,;:.")


def _soft_limit_cn_social(text, max_chars=220):
    lines = []
    for ln in str(text or "").splitlines():
        s = _clean_text(ln)
        if not s:
            continue
        if "：" in s:
            k, v = s.split("：", 1)
            k = k.strip()
            v = _clip_cn(_strip_field_noise(v, lang="zh"), 42)
            s = f"{k}：{v}" if v else f"{k}："
        lines.append(s)
    out = "\n".join(lines).strip()
    if _char_count_no_space(out) > max_chars:
        out = _clip_cn(out, max_chars)
    return out


def _soft_limit_en_social(text, max_words=120):
    lines = []
    for ln in str(text or "").splitlines():
        s = _clean_text(ln)
        if not s:
            continue
        if ":" in s:
            k, v = s.split(":", 1)
            s = f"{k.strip()}: {_clip_en_words(_strip_field_noise(v, lang='en'), 18)}"
        lines.append(s)
    out = "\n".join(lines).strip()
    if _word_count(out) > max_words:
        out = _clip_en_words(out, max_words)
    return out


def _extract_line(text, prefixes):
    for line in str(text or "").splitlines():
        raw = line.strip()
        for p in prefixes:
            if raw.lower().startswith(p.lower()):
                body = raw[len(p):].strip(" ：:")
                return _strip_field_noise(body, lang="en" if "Evidence anchor" in p or "Impact path" in p or "Core fact" in p else "zh")
    return ""


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
    if _contains_stale_absolute_claim(combined) or _has_forbidden(combined):
        return False
    if _contains_assembly_noise(combined):
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
7) No machine-assembly artifacts: no \"要点 •\", no duplicated labels, no repeated \"影响路径\".
8) Avoid absolute stale claims like \"formally suspended exports\" unless a dated official source is explicitly provided.
9) Core fact should include a source/time qualifier if available.
10) Impact path and Evidence anchor must be non-duplicate.
11) Evidence anchor must not begin with numbered scenario bullets.

Length:
- cn_social <= 220 Chinese chars (soft; concise)
- en_social <= 120 words (soft; concise)
- cn_topic/en_topic can be richer than social but concise.

Required label structure:
CN topic lines include exactly once:
- 核心事实：...
- 影响路径：...
- 投资动作：...
- 反方风险：...
- 证据锚点：...
- ⚠️ 风险提示：...

EN topic lines include exactly once:
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


def _build_rule_fallback_bundle(top3_data, daily_zh, daily_en, fingerprint, reason="rule_fallback"):
    items = (top3_data or {}).get("items") or []
    first = items[0] if items else {}

    zh_head = _first_non_empty((daily_zh or {}).get("headline"), first.get("title_zh"), first.get("title"), "今日关键矿产信号")
    en_head = _first_non_empty((daily_en or {}).get("headline"), first.get("title_en"), first.get("title"), "Today's critical minerals signal")
    zh_val = _first_non_empty(first.get("value_zh"), first.get("report_contains_zh"), zh_head)
    en_val = _first_non_empty(first.get("value_en"), first.get("report_contains_en"), en_head)

    zh_core = _clip_cn(_strip_field_noise(f"根据今日Top信号，{_first_sentence(zh_head, 'zh')}", lang="zh"), 72)
    en_core = _clip_en_words(_strip_field_noise(f"According to today's top signal, {_first_sentence(en_head, 'en')}", lang="en"), 20)

    zh_impact = _clip_cn(_strip_field_noise(_first_sentence(zh_val, "zh") or "供应链重排速度提升，海外溢价与国内重估预期同步增强。", lang="zh"), 66)
    if _looks_like_numbered_scenario(zh_impact, lang="zh"):
        zh_impact = "供应链重排速度提升，海外溢价与国内重估预期同步增强。"

    en_impact = _clip_en_words(_strip_field_noise(_first_sentence(en_val, "en") or "Supply-chain repricing is widening external premiums and domestic revaluation expectations.", lang="en"), 20)
    if _looks_like_numbered_scenario(en_impact, lang="en"):
        en_impact = "Supply-chain repricing is widening external premiums and domestic revaluation expectations."

    zh_action = _clip_cn(_strip_field_noise(_pick_action(daily_zh, "zh") or "优先跟踪政策资金、加工产能与订单兑现节奏。", lang="zh"), 62)
    en_action = _clip_en_words(_strip_field_noise(_pick_action(daily_en, "en") or "Track policy funding, processing ramp-up, and order conversion before sizing positions.", lang="en"), 20)

    zh_risk = _clip_cn(_strip_field_noise("若海外供给兑现快于预期，短期溢价与估值或回撤。", lang="zh"), 58)
    en_risk = _clip_en_words(_strip_field_noise("If non-China supply ramps faster than expected, short-term premiums and valuations may retrace.", lang="en"), 18)

    zh_ev = _pick_sentence_with_number(zh_val, "zh") or _pick_sentence_with_number(zh_head, "zh")
    zh_ev = _clip_cn(_strip_field_noise(zh_ev or _first_sentence(zh_val, "zh"), lang="zh"), 72)
    if (not _is_fact_sentence(zh_ev)) or (not _has_numeric_anchor(zh_ev)) or any(k in zh_ev for k in ["减持", "增配", "规避", "布局", "配置", "优先跟踪"]) or _looks_like_numbered_scenario(zh_ev, lang="zh") or _sameish(zh_ev, zh_impact) or _sameish(zh_ev, zh_core):
        zh_ev = _clip_cn(_strip_field_noise(zh_val, lang="zh"), 72)

    en_ev = _pick_sentence_with_number(en_val, "en") or _pick_sentence_with_number(en_head, "en")
    en_ev = _clip_en_words(_strip_field_noise(en_ev or _first_sentence(en_val, "en"), lang="en"), 24)
    if (not _is_fact_sentence(en_ev)) or (not _has_numeric_anchor(en_ev)) or any(k in en_ev.lower() for k in ACTION_HINTS) or _looks_like_numbered_scenario(en_ev, lang="en") or _sameish(en_ev, en_impact) or _sameish(en_ev, en_core):
        en_ev = _clip_en_words(_strip_field_noise(en_val, lang="en"), 24)

    cn_topic = "\n".join([
        f"核心事实：{zh_core}",
        f"影响路径：{zh_impact}",
        f"投资动作：{zh_action}",
        f"反方风险：{zh_risk}",
        f"证据锚点：{zh_ev}",
        "⚠️ 风险提示：不构成投资建议，请按风险承受能力决策。",
    ]).strip()

    en_topic = "\n".join([
        f"Core fact: {en_core}",
        f"Impact path: {en_impact}",
        f"Investor action: {en_action}",
        f"Counter-risk: {en_risk}",
        f"Evidence anchor: {en_ev}",
        "⚠️ Risk note: Not investment advice. Manage triggers and position size.",
    ]).strip()

    cn_topic = _ensure_structured_newlines(_sanitize_stale_claims(_normalize_punct(cn_topic)), lang="zh")
    en_topic = _ensure_structured_newlines(_sanitize_stale_claims(_normalize_punct(en_topic)), lang="en")

    cn_social = _soft_limit_cn_social(cn_topic, 220)
    en_social = _soft_limit_en_social(en_topic, 120)

    return {
        "generated_at": _iso_now(),
        "generator_version": GENERATOR_VERSION,
        "source_fingerprint": fingerprint,
        "cn_social": cn_social,
        "en_social": en_social,
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


def _validate_payload(payload):
    if not isinstance(payload, dict):
        return False, "not_dict"

    required = ["cn_social", "en_social", "cn_topic", "en_topic"]
    for k in required:
        if not str(payload.get(k, "")).strip():
            return False, f"missing_{k}"

    cn_social = _sanitize_stale_claims(_normalize_punct(_clean_text(payload.get("cn_social"))))
    en_social = _sanitize_stale_claims(_normalize_punct(_clean_text(payload.get("en_social"))))
    cn_topic = _ensure_structured_newlines(_sanitize_stale_claims(_normalize_punct(_clean_text(payload.get("cn_topic")))), lang="zh")
    en_topic = _ensure_structured_newlines(_sanitize_stale_claims(_normalize_punct(_clean_text(payload.get("en_topic")))), lang="en")

    if _char_count_no_space(cn_social) > 220:
        cn_social = _soft_limit_cn_social(cn_social, 220)
    if _word_count(en_social) > 120:
        en_social = _soft_limit_en_social(en_social, 120)

    combined = "\n".join([cn_social, en_social, cn_topic, en_topic])
    if _contains_stale_absolute_claim(combined):
        return False, "stale_claim"
    if _has_forbidden(combined):
        return False, "forbidden_word"
    if _contains_assembly_noise(combined):
        return False, "assembly_noise"

    if not all(lbl in cn_topic for lbl in REQUIRED_CN_LABELS):
        return False, "missing_cn_labels"
    if not all(lbl in en_topic for lbl in REQUIRED_EN_LABELS):
        return False, "missing_en_labels"

    cn_ev = _extract_line(cn_topic + "\n" + cn_social, ["证据锚点：", "证据锚点:"])
    en_ev = _extract_line(en_topic + "\n" + en_social, ["Evidence anchor:", "Evidence anchors:"])

    if _looks_like_numbered_scenario(cn_ev, lang="zh"):
        return False, "evidence_zh_numbered"
    if _looks_like_numbered_scenario(en_ev, lang="en"):
        return False, "evidence_en_numbered"

    if (not _is_fact_sentence(cn_ev)) or (not _has_numeric_anchor(cn_ev)) or any(k in cn_ev for k in ["减持", "增配", "规避", "布局", "配置", "优先跟踪"]):
        return False, "evidence_zh"
    if (not _is_fact_sentence(en_ev)) or (not _has_numeric_anchor(en_ev)) or any(k in en_ev.lower() for k in ACTION_HINTS):
        return False, "evidence_en"

    cn_impact = _extract_line(cn_topic, ["影响路径：", "影响路径:"])
    en_impact = _extract_line(en_topic, ["Impact path:"])
    cn_core = _extract_line(cn_topic, ["核心事实：", "核心事实:"])
    en_core = _extract_line(en_topic, ["Core fact:"])

    def _norm(x):
        return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(x or "").lower())

    if _norm(cn_impact) and (_norm(cn_impact) == _norm(cn_ev) or _norm(cn_impact) == _norm(cn_core)):
        return False, "dup_cn"
    if _norm(en_impact) and (_norm(en_impact) == _norm(en_ev) or _norm(en_impact) == _norm(en_core)):
        return False, "dup_en"

    payload["cn_social"] = cn_social
    payload["en_social"] = en_social
    payload["cn_topic"] = cn_topic
    payload["en_topic"] = en_topic
    return True, "ok"


def generate_social_copy_bundle(config, top3_data, daily_zh, daily_en, report_ids, timeout_seconds=8):
    """
    Returns dict with cn_social/en_social/cn_topic/en_topic and metadata,
    never None: validated model output, cache fallback, or rule fallback.
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
        ok, reason = _validate_payload(data if isinstance(data, dict) else {})
        if not ok:
            if _cache_usable(cached, fingerprint, strict=False):
                return cached
            return _save_and_return_fallback(f"validator_{reason}")

        output = {
            "generated_at": _iso_now(),
            "generator_version": GENERATOR_VERSION,
            "source_fingerprint": fingerprint,
            "cn_social": data.get("cn_social") or data.get("cn_topic", ""),
            "en_social": data.get("en_social") or data.get("en_topic", ""),
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
