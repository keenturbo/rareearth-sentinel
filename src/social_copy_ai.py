import concurrent.futures
import hashlib
import json
import os
import re
from datetime import datetime, timezone

import google.generativeai as genai

from .utils import extract_json

FORBIDDEN_PROMISE_WORDS = [
    "稳赚",
    "必涨",
    "保本",
    "稳赚不赔",
    "guaranteed",
    "sure win",
    "risk-free",
]

SUBJECT_HINTS_ZH = ["投资者", "采购方", "政策", "政府", "企业", "供应链团队"]
SUBJECT_HINTS_EN = ["investor", "procurement", "policy", "government", "company", "supply-chain"]
ADVICE_HINTS = ["超配", "减持", "规避", "buy", "sell", "overweight", "underweight"]


def _iso_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(text):
    text = str(text or "")
    text = text.replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = text.replace("…", "")
    text = text.replace("...", "")
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


def _is_fact_sentence(text):
    t = str(text or "")
    if not t:
        return False
    if any(k in t.lower() for k in ADVICE_HINTS):
        return False
    fact_tokens = [
        r"\d", r"%", r"亿美元", r"亿元", r"million", r"billion", r"project", r"tariff", r"duty", r"confirmed", r"announced", r"终裁", r"宣布", r"确认", r"启动", r"注资", r"投资",
    ]
    return any(re.search(tok, t, re.I) for tok in fact_tokens)


def _word_count(text):
    return len(re.findall(r"\S+", str(text or "")))


def _char_count_no_space(text):
    return len(re.sub(r"\s+", "", str(text or "")))


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

    if _has_forbidden(cn_social + "\n" + en_social + "\n" + cn_topic + "\n" + en_topic):
        return False, "forbidden_word"

    if not _has_subject(cn_social + "\n" + cn_topic, lang="zh"):
        return False, "subject_zh"
    if not _has_subject(en_social + "\n" + en_topic, lang="en"):
        return False, "subject_en"

    cn_ev = _extract_line(cn_social + "\n" + cn_topic, ["证据锚点：", "证据锚点:"])
    en_ev = _extract_line(en_social + "\n" + en_topic, ["Evidence anchor:", "Evidence anchors:"])
    if not _is_fact_sentence(cn_ev):
        return False, "evidence_zh"
    if not _is_fact_sentence(en_ev):
        return False, "evidence_en"

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

Length:
- cn_social <= 220 Chinese chars (excluding line breaks)
- en_social <= 120 words
- cn_topic should be concise but richer than social copy
- en_topic should be concise but richer than social copy

Required field structure in each text:
CN social/topic lines should include:
- 核心事实：...
- 影响路径：...
- 投资动作：...
- 反方风险：...
- 证据锚点：...
- ⚠️ 风险提示：...

EN social/topic lines should include:
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
    or None if generation fails (caller should fallback).
    """
    fingerprint = _compute_fingerprint(top3_data, daily_zh, daily_en, report_ids)
    cache_file = _cache_path(config)
    cached = _load_cache(cache_file)
    if cached.get("source_fingerprint") == fingerprint:
        if all(cached.get(k) for k in ("cn_social", "en_social", "cn_topic", "en_topic")):
            return cached

    api_key = str(getattr(config, "gemini_api_key", "") or "").strip()
    model_name = str(getattr(config, "gemini_model", "") or "").strip()
    if not api_key or not model_name:
        return None

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name, generation_config={"temperature": 0.2})
        prompt = _build_prompt(top3_data, daily_zh, daily_en)
        resp = _generate_with_timeout(model, prompt, timeout_seconds=max(3, int(timeout_seconds or 8)))
        raw = _response_text(resp)
        data = extract_json(raw)
        ok, reason = _validate_payload(data)
        if not ok:
            return None

        output = {
            "generated_at": _iso_now(),
            "source_fingerprint": fingerprint,
            "cn_social": data.get("cn_social", ""),
            "en_social": data.get("en_social", ""),
            "cn_topic": data.get("cn_topic", ""),
            "en_topic": data.get("en_topic", ""),
            "quality_flags": {
                "subject_ok": True,
                "evidence_fact_ok": True,
                "no_profit_claim_ok": True,
            },
            "fallback_used": False,
            "validator_reason": reason,
        }
        _save_cache(cache_file, output)
        return output
    except Exception:
        return None
