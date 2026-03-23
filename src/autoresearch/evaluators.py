from __future__ import annotations

import re
from collections import Counter
from typing import Any


HOMEPAGE_REQUIRED_LABELS = {
    "zh": ["核心事实", "影响路径", "证据锚点"],
    "en": ["Core fact", "Impact path", "Evidence anchor"],
}

SOCIAL_REQUIRED_TOPIC_LABELS = {
    "zh": ["核心事实", "影响路径", "投资动作", "反方风险", "证据锚点"],
    "en": ["Core fact", "Impact path", "Investor action", "Counter-risk", "Evidence anchor"],
}

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

ACTION_HINTS = [
    "超配",
    "减持",
    "规避",
    "布局",
    "配置",
    "优先跟踪",
    "建议",
    "加仓",
    "减仓",
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
    "track",
    "watch",
]

FACT_HINT_TOKENS = [
    r"\d",
    r"%",
    r"亿美元",
    r"亿元",
    r"million",
    r"billion",
    r"202\d",
    r"Q[1-4]",
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
    r"审批",
    r"出口",
    r"产能",
    r"矿",
]

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

CLEAR_SUBJECT_TOKENS_ZH = [
    "中国",
    "美国",
    "欧盟",
    "政府",
    "矿企",
    "项目",
    "供应链",
    "企业",
    "稀土",
    "关键矿产",
    "工厂",
    "矿山",
]

CLEAR_SUBJECT_TOKENS_EN = [
    "china",
    "us",
    "u.s.",
    "europe",
    "government",
    "miner",
    "project",
    "supply chain",
    "producer",
    "rare earth",
    "critical minerals",
    "plant",
    "mine",
]

CTA_LINES_ZH = ["私信回复【稀土】获取完整版", "👉 解锁深度报告："]
CTA_LINES_EN = ["DM for full report", "Unlock full report:"]


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)


def _clean_text(text: Any) -> str:
    s = str(text or "")
    s = s.replace("\r", "\n")
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def _normalize_compare(text: Any) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(text or "").lower())


def _is_similar(a: Any, b: Any) -> bool:
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
    return (len(sa & sb) / len(union)) >= 0.72


def _split_lines(text: Any) -> list[str]:
    rows = []
    for raw in str(text or "").splitlines():
        line = _clean_text(raw)
        if not line:
            continue
        rows.append(line)
    return rows


def _extract_line(text: Any, prefixes: list[str]) -> str:
    for line in _split_lines(text):
        lowered = line.lower()
        for prefix in prefixes:
            p = prefix.lower()
            if lowered.startswith(p):
                body = line[len(prefix):].strip(" ：:")
                return _clean_text(body)
    return ""


def _has_required_labels(text: Any, labels: list[str]) -> bool:
    raw = str(text or "")
    return all(label in raw for label in labels)


def _count_duplicate_lines(text: Any) -> int:
    lines = _split_lines(text)
    duplicates = 0
    seen: list[str] = []
    for line in lines:
        if any(_is_similar(line, old) for old in seen):
            duplicates += 1
        else:
            seen.append(line)
    return duplicates


def _contains_stale_claim(text: Any) -> bool:
    raw = str(text or "")
    low = raw.lower()
    if any(re.search(pattern, raw, re.I) for pattern in STALE_ABSOLUTE_PATTERNS_ZH):
        return True
    if any(re.search(pattern, low, re.I) for pattern in STALE_ABSOLUTE_PATTERNS_EN):
        return True
    return False


def _contains_assembly_noise(text: Any) -> bool:
    raw = str(text or "")
    return any(re.search(pattern, raw, re.I) for pattern in ASSEMBLY_NOISE_PATTERNS)


def _is_fact_like_sentence(text: Any) -> bool:
    raw = _clean_text(text)
    if not raw:
        return False
    lowered = raw.lower()
    if any(token.lower() in lowered for token in ACTION_HINTS):
        return False
    return any(re.search(pattern, raw, re.I) for pattern in FACT_HINT_TOKENS)


def _subject_clear(text: Any, lang: str = "zh") -> bool:
    raw = _clean_text(text)
    if not raw:
        return False
    low = raw.lower()
    tokens = CLEAR_SUBJECT_TOKENS_EN if lang == "en" else CLEAR_SUBJECT_TOKENS_ZH
    for token in tokens:
        target = token.lower() if lang == "en" else token
        if target in (low if lang == "en" else raw):
            return True
    return False


def _remove_cta_and_links(text: Any, lang: str = "zh") -> str:
    lines = []
    cta_lines = CTA_LINES_EN if lang == "en" else CTA_LINES_ZH
    for line in _split_lines(text):
        if re.search(r"https?://", line):
            continue
        if any(line.startswith(prefix) for prefix in cta_lines):
            continue
        if line.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _external_dm_split_pass(external_text: Any, dm_text: Any, lang: str = "zh") -> bool:
    external = _remove_cta_and_links(external_text, lang=lang)
    dm = _remove_cta_and_links(dm_text, lang=lang)
    if not external or not dm:
        return False
    return not _is_similar(external, dm)


def _word_count(text: Any) -> int:
    return len(re.findall(r"\S+", str(text or "")))


def _text_pair_similarity(a: Any, b: Any) -> float:
    na = _normalize_compare(a)
    nb = _normalize_compare(b)
    if not na or not nb:
        return 0.0
    sa, sb = set(na), set(nb)
    union = sa | sb
    if not union:
        return 0.0
    return round(len(sa & sb) / len(union), 4)


def _cross_version_similarity_penalty(bundle: dict[str, Any]) -> float:
    variants = (bundle or {}).get("variants") or {}
    x = ((variants.get("xueqiu_long") or {}).get("content") or "")
    s = ((variants.get("sentinel_pro") or {}).get("content") or "")
    d = ((variants.get("deep_dive") or {}).get("content") or "")
    scores = [
        _text_pair_similarity(x, s),
        _text_pair_similarity(x, d),
        _text_pair_similarity(s, d),
    ]
    max_score = max(scores) if scores else 0.0
    if max_score >= 0.75:
        return 0.2
    if max_score >= 0.6:
        return 0.1
    return 0.0


def _top_reason_rows(values: list[str], top_n: int = 5) -> list[dict[str, Any]]:
    counter = Counter(str(v).strip() for v in values if str(v).strip())
    return [{"reason": reason, "count": count} for reason, count in counter.most_common(top_n)]


def _top_failed_field_rows(values: list[str], top_n: int = 5) -> list[dict[str, Any]]:
    counter = Counter(str(v).strip() for v in values if str(v).strip())
    return [{"field": field, "count": count} for field, count in counter.most_common(top_n)]


def evaluate_homepage_case(case_payload: dict[str, Any], candidate_output: dict[str, Any]) -> dict[str, Any]:
    lang = str((case_payload or {}).get("lang") or (candidate_output or {}).get("lang") or "zh").strip().lower()
    lang = "en" if lang == "en" else "zh"
    topic_text = _clean_text((candidate_output or {}).get("topic_text"))
    meta = (candidate_output or {}).get("meta") or {}

    non_empty_pass = bool(topic_text)
    required_labels_pass = _has_required_labels(topic_text, HOMEPAGE_REQUIRED_LABELS[lang])
    duplicate_line_count = _count_duplicate_lines(topic_text)
    stale_claim_flag_count = 1 if _contains_stale_claim(topic_text) else 0

    evidence_prefixes = ["Evidence anchor:"] if lang == "en" else ["证据锚点：", "证据锚点:"]
    evidence_anchor = _extract_line(topic_text, evidence_prefixes)
    evidence_anchor_present = bool(evidence_anchor)
    evidence_anchor_fact_like = bool(evidence_anchor_present and _is_fact_like_sentence(evidence_anchor))
    fallback_used = bool(meta.get("fallback_used"))
    line_count = len(_split_lines(topic_text))

    score = 1.0
    reasons: list[str] = []
    if not non_empty_pass:
        score -= 0.30
        reasons.append("empty_topic")
    if not required_labels_pass:
        score -= 0.20
        reasons.append("missing_required_labels")
    if duplicate_line_count:
        score -= 0.15 * duplicate_line_count
        reasons.append("duplicate_lines")
    if stale_claim_flag_count:
        score -= 0.25 * stale_claim_flag_count
        reasons.append("stale_claim")
    if not evidence_anchor_present:
        score -= 0.15
        reasons.append("missing_evidence_anchor")
    elif not evidence_anchor_fact_like:
        score -= 0.15
        reasons.append("evidence_anchor_not_fact_like")
    if fallback_used:
        score -= 0.10
        reasons.append("fallback_used")

    return {
        "case_id": str((case_payload or {}).get("case_id") or "").strip(),
        "lang": lang,
        "non_empty_pass": non_empty_pass,
        "required_labels_pass": required_labels_pass,
        "duplicate_line_count": duplicate_line_count,
        "stale_claim_flag_count": stale_claim_flag_count,
        "evidence_anchor_present": evidence_anchor_present,
        "evidence_anchor_fact_like": evidence_anchor_fact_like,
        "fallback_used": fallback_used,
        "line_count": line_count,
        "reasons": reasons,
        "case_score": _clamp_score(score),
    }


def evaluate_social_case(case_payload: dict[str, Any], candidate_output: dict[str, Any]) -> dict[str, Any]:
    meta = (candidate_output or {}).get("meta") or {}
    cn_topic = _clean_text((candidate_output or {}).get("cn_topic"))
    en_topic = _clean_text((candidate_output or {}).get("en_topic"))
    cn_external = _clean_text((candidate_output or {}).get("cn_social_external"))
    cn_dm = _clean_text((candidate_output or {}).get("cn_social_dm"))
    en_external = _clean_text((candidate_output or {}).get("en_social_external"))
    en_dm = _clean_text((candidate_output or {}).get("en_social_dm"))

    required_topic_labels_pass = _has_required_labels(cn_topic, SOCIAL_REQUIRED_TOPIC_LABELS["zh"]) and _has_required_labels(en_topic, SOCIAL_REQUIRED_TOPIC_LABELS["en"])
    subject_clear_pass = _subject_clear(cn_topic or cn_external or cn_dm, "zh") and _subject_clear(en_topic or en_external or en_dm, "en")
    cn_evidence = _extract_line(cn_topic, ["证据锚点：", "证据锚点:"])
    en_evidence = _extract_line(en_topic, ["Evidence anchor:"])
    evidence_anchor_pass = bool(cn_evidence and en_evidence and _is_fact_like_sentence(cn_evidence) and _is_fact_like_sentence(en_evidence))
    assembly_noise_pass = not _contains_assembly_noise("\n".join([cn_topic, en_topic, cn_external, cn_dm, en_external, en_dm]))
    external_dm_split_pass = _external_dm_split_pass(cn_external, cn_dm, "zh") and _external_dm_split_pass(en_external, en_dm, "en")
    fallback_used = bool(meta.get("fallback_used"))
    validator_reason = str(meta.get("validator_reason") or "").strip() or "ok"
    publishable_pass = all([
        required_topic_labels_pass,
        subject_clear_pass,
        evidence_anchor_pass,
        assembly_noise_pass,
        external_dm_split_pass,
        not fallback_used,
        validator_reason == "ok",
    ])

    score = 1.0
    reasons: list[str] = []
    if fallback_used:
        score -= 0.25
        reasons.append("fallback_used")
    if not subject_clear_pass:
        score -= 0.20
        reasons.append("subject_not_clear")
    if not evidence_anchor_pass:
        score -= 0.20
        reasons.append("evidence_anchor_not_pass")
    if not assembly_noise_pass:
        score -= 0.15
        reasons.append("assembly_noise")
    if not external_dm_split_pass:
        score -= 0.10
        reasons.append("external_dm_not_split")
    if not required_topic_labels_pass:
        score -= 0.10
        reasons.append("missing_topic_labels")
    if validator_reason != "ok":
        reasons.append(f"validator_reason:{validator_reason}")

    return {
        "case_id": str((case_payload or {}).get("case_id") or "").strip(),
        "publishable_pass": publishable_pass,
        "fallback_used": fallback_used,
        "validator_reason": validator_reason,
        "subject_clear_pass": subject_clear_pass,
        "evidence_anchor_pass": evidence_anchor_pass,
        "assembly_noise_pass": assembly_noise_pass,
        "external_dm_split_pass": external_dm_split_pass,
        "required_topic_labels_pass": required_topic_labels_pass,
        "reasons": reasons,
        "case_score": _clamp_score(score),
    }


def evaluate_variants_case(case_payload: dict[str, Any], candidate_output: dict[str, Any]) -> dict[str, Any]:
    bundle = (candidate_output or {}).get("bundle") or {}
    validator = bundle.get("validator") or {}
    validator_pass = bool(validator.get("passed"))
    fallback_used = bool(validator.get("fallback_used"))
    validator_reason = str(validator.get("reason") or "").strip() or ("ok" if validator_pass else "unknown")
    failed_field = str(validator.get("failed_field") or "").strip()

    expected = (case_payload or {}).get("expected_properties") or {}
    today_hit_expected = bool(expected.get("should_be_today_hit"))
    selected_for_today = (candidate_output or {}).get("selected_for_today")
    selected_for_today = bool(selected_for_today) if selected_for_today is not None else False
    today_hit_pass = None
    if today_hit_expected:
        today_hit_pass = bool(selected_for_today)

    issues = validator.get("issues") or []
    deep_dive_pass = validator_pass and not any(str(issue).startswith("deep_dive") for issue in issues) and failed_field != "deep_dive"
    similarity_penalty = _cross_version_similarity_penalty(bundle)

    score = 1.0
    reasons: list[str] = []
    if not validator_pass:
        score -= 0.30
        reasons.append("validator_failed")
    if fallback_used:
        score -= 0.20
        reasons.append("fallback_used")
    if not deep_dive_pass:
        score -= 0.15
        reasons.append("deep_dive_not_pass")
    score -= similarity_penalty
    if today_hit_expected and today_hit_pass is False:
        score -= 0.10
        reasons.append("today_hit_missed")
    if validator_reason and validator_reason != "ok":
        reasons.append(f"validator_reason:{validator_reason}")

    return {
        "case_id": str((case_payload or {}).get("case_id") or "").strip(),
        "validator_pass": validator_pass,
        "fallback_used": fallback_used,
        "validator_reason": validator_reason,
        "failed_field": failed_field,
        "selected_for_today": selected_for_today,
        "today_hit_expected": today_hit_expected,
        "today_hit_pass": today_hit_pass,
        "cross_version_similarity_penalty": similarity_penalty,
        "deep_dive_pass": deep_dive_pass,
        "reasons": reasons,
        "case_score": _clamp_score(score),
    }


def summarize_homepage_results(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(case_results)
    duplicate_cases = sum(1 for row in case_results if int(row.get("duplicate_line_count") or 0) > 0)
    stale_cases = sum(1 for row in case_results if int(row.get("stale_claim_flag_count") or 0) > 0)
    evidence_pass = sum(1 for row in case_results if row.get("evidence_anchor_present") and row.get("evidence_anchor_fact_like"))
    non_empty = sum(1 for row in case_results if row.get("non_empty_pass"))
    required = sum(1 for row in case_results if row.get("required_labels_pass"))
    fallback_cases = sum(1 for row in case_results if row.get("fallback_used"))
    scores = [float(row.get("case_score") or 0.0) for row in case_results]
    reasons: list[str] = []
    for row in case_results:
        reasons.extend(row.get("reasons") or [])

    summary = {
        "case_count": total,
        "non_empty_rate": _safe_ratio(non_empty, total),
        "required_labels_rate": _safe_ratio(required, total),
        "duplicate_case_rate": _safe_ratio(duplicate_cases, total),
        "stale_claim_case_rate": _safe_ratio(stale_cases, total),
        "evidence_anchor_pass_rate": _safe_ratio(evidence_pass, total),
        "fallback_rate": _safe_ratio(fallback_cases, total),
        "avg_case_score": round(sum(scores) / total, 4) if total else 0.0,
        "top_issue_reasons": _top_reason_rows(reasons),
    }
    summary["composite_score"] = compute_composite_score("homepage_topic_policy", summary)
    return summary


def summarize_social_results(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(case_results)
    publishable = sum(1 for row in case_results if row.get("publishable_pass"))
    fallback_cases = sum(1 for row in case_results if row.get("fallback_used"))
    subject_clear = sum(1 for row in case_results if row.get("subject_clear_pass"))
    evidence_anchor = sum(1 for row in case_results if row.get("evidence_anchor_pass"))
    assembly_fail = sum(1 for row in case_results if not row.get("assembly_noise_pass"))
    external_dm = sum(1 for row in case_results if row.get("external_dm_split_pass"))
    topic_labels = sum(1 for row in case_results if row.get("required_topic_labels_pass"))
    scores = [float(row.get("case_score") or 0.0) for row in case_results]
    validator_reasons = [row.get("validator_reason") for row in case_results if row.get("validator_reason") and row.get("validator_reason") != "ok"]

    summary = {
        "case_count": total,
        "publishable_rate": _safe_ratio(publishable, total),
        "fallback_rate": _safe_ratio(fallback_cases, total),
        "subject_clear_rate": _safe_ratio(subject_clear, total),
        "evidence_anchor_rate": _safe_ratio(evidence_anchor, total),
        "assembly_noise_fail_rate": _safe_ratio(assembly_fail, total),
        "external_dm_split_rate": _safe_ratio(external_dm, total),
        "required_topic_labels_rate": _safe_ratio(topic_labels, total),
        "avg_case_score": round(sum(scores) / total, 4) if total else 0.0,
        "top_validator_reasons": _top_reason_rows(validator_reasons),
    }
    summary["composite_score"] = compute_composite_score("social_copy_policy", summary)
    return summary


def summarize_variants_results(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(case_results)
    validator_pass = sum(1 for row in case_results if row.get("validator_pass"))
    fallback_cases = sum(1 for row in case_results if row.get("fallback_used"))
    deep_dive_pass = sum(1 for row in case_results if row.get("deep_dive_pass"))
    expected_today = [row for row in case_results if row.get("today_hit_expected")]
    today_hits = sum(1 for row in expected_today if row.get("today_hit_pass") is True)
    similarity_penalties = [float(row.get("cross_version_similarity_penalty") or 0.0) for row in case_results]
    scores = [float(row.get("case_score") or 0.0) for row in case_results]
    validator_reasons = [row.get("validator_reason") for row in case_results if row.get("validator_reason") and row.get("validator_reason") != "ok"]
    failed_fields = [row.get("failed_field") for row in case_results if row.get("failed_field")]

    summary = {
        "case_count": total,
        "validator_pass_rate": _safe_ratio(validator_pass, total),
        "fallback_rate": _safe_ratio(fallback_cases, total),
        "today_hit_rate": _safe_ratio(today_hits, len(expected_today)),
        "today_hit_denominator": len(expected_today),
        "deep_dive_pass_rate": _safe_ratio(deep_dive_pass, total),
        "avg_similarity_penalty": round(sum(similarity_penalties) / total, 4) if total else 0.0,
        "avg_case_score": round(sum(scores) / total, 4) if total else 0.0,
        "top_validator_reasons": _top_reason_rows(validator_reasons),
        "top_failed_fields": _top_failed_field_rows(failed_fields),
    }
    summary["composite_score"] = compute_composite_score("content_variants_policy", summary)
    return summary


def compute_composite_score(family: str, summary: dict[str, Any]) -> float:
    if family == "homepage_topic_policy":
        score = (
            0.20 * float(summary.get("non_empty_rate") or 0.0)
            + 0.25 * float(summary.get("required_labels_rate") or 0.0)
            + 0.25 * float(summary.get("evidence_anchor_pass_rate") or 0.0)
            + 0.15 * (1.0 - float(summary.get("stale_claim_case_rate") or 0.0))
            + 0.10 * (1.0 - float(summary.get("fallback_rate") or 0.0))
            + 0.05 * (1.0 - float(summary.get("duplicate_case_rate") or 0.0))
        )
        return _clamp_score(score)

    if family == "social_copy_policy":
        score = (
            0.30 * float(summary.get("publishable_rate") or 0.0)
            + 0.20 * (1.0 - float(summary.get("fallback_rate") or 0.0))
            + 0.20 * float(summary.get("subject_clear_rate") or 0.0)
            + 0.15 * float(summary.get("evidence_anchor_rate") or 0.0)
            + 0.10 * float(summary.get("external_dm_split_rate") or 0.0)
            + 0.05 * (1.0 - float(summary.get("assembly_noise_fail_rate") or 0.0))
        )
        return _clamp_score(score)

    if family == "content_variants_policy":
        score = (
            0.30 * float(summary.get("validator_pass_rate") or 0.0)
            + 0.25 * (1.0 - float(summary.get("fallback_rate") or 0.0))
            + 0.20 * float(summary.get("today_hit_rate") or 0.0)
            + 0.15 * float(summary.get("deep_dive_pass_rate") or 0.0)
            + 0.10 * (1.0 - float(summary.get("avg_similarity_penalty") or 0.0))
        )
        return _clamp_score(score)

    return 0.0


def evaluate_family_batch(family: str, dataset_payload: dict[str, Any], candidate_outputs: dict[str, Any]) -> dict[str, Any]:
    items = (dataset_payload or {}).get("items") or []
    if not isinstance(items, list):
        raise ValueError("dataset_items_not_list")
    if not isinstance(candidate_outputs, dict):
        raise ValueError("candidate_outputs_not_dict")

    case_results = []
    for case_payload in items:
        case_id = str((case_payload or {}).get("case_id") or "").strip()
        output = candidate_outputs.get(case_id) or {}
        if family == "homepage_topic_policy":
            case_results.append(evaluate_homepage_case(case_payload, output))
        elif family == "social_copy_policy":
            case_results.append(evaluate_social_case(case_payload, output))
        elif family == "content_variants_policy":
            case_results.append(evaluate_variants_case(case_payload, output))
        else:
            raise ValueError(f"unsupported_family: {family}")

    if family == "homepage_topic_policy":
        summary = summarize_homepage_results(case_results)
    elif family == "social_copy_policy":
        summary = summarize_social_results(case_results)
    elif family == "content_variants_policy":
        summary = summarize_variants_results(case_results)
    else:
        raise ValueError(f"unsupported_family: {family}")

    top_reasons = summary.get("top_validator_reasons") or summary.get("top_issue_reasons") or []

    return {
        "family": family,
        "case_count": len(case_results),
        "summary": summary,
        "case_results": case_results,
        "composite_score": float(summary.get("composite_score") or 0.0),
        "top_reasons": top_reasons,
    }
