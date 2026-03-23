from __future__ import annotations

import re
from typing import Any


def _clean_text(text: Any) -> str:
    s = str(text or "")
    s = s.replace("\r", "\n")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _clip_text(text: Any, limit: int = 120) -> str:
    s = _clean_text(text)
    if not limit or len(s) <= limit:
        return s
    return s[:limit].rstrip("，。；;,. ") + "…"


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if isinstance(value, list):
            for item in value:
                s = _clean_text(item)
                if s:
                    return s
        elif isinstance(value, dict):
            for item in value.values():
                s = _clean_text(item)
                if s:
                    return s
        else:
            s = _clean_text(value)
            if s:
                return s
    return ""


def _title_for_lang(top_item: dict[str, Any], lang: str) -> str:
    if lang == "en":
        return _first_non_empty(top_item.get("title_en"), top_item.get("title"), top_item.get("title_zh"), "Today's focus")
    return _first_non_empty(top_item.get("title_zh"), top_item.get("title"), top_item.get("title_en"), "今日重点专题")


def _impact_for_lang(case_payload: dict[str, Any], top_item: dict[str, Any], lang: str) -> str:
    daily = (case_payload or {}).get("daily_report") or {}
    if lang == "en":
        return _clip_text(
            _first_non_empty(
                top_item.get("value_en"),
                top_item.get("report_contains_en"),
                daily.get("summary"),
                daily.get("headline"),
                daily.get("body"),
                _title_for_lang(top_item, lang),
            ),
            180,
        )
    return _clip_text(
        _first_non_empty(
            top_item.get("value_zh"),
            top_item.get("report_contains_zh"),
            daily.get("summary"),
            daily.get("headline"),
            daily.get("body"),
            _title_for_lang(top_item, lang),
        ),
        120,
    )


def _action_for_lang(case_payload: dict[str, Any], lang: str) -> str:
    daily = (case_payload or {}).get("daily_report") or {}
    if lang == "en":
        picked = _first_non_empty(
            daily.get("strategy"),
            daily.get("strategies"),
            daily.get("tactics"),
            daily.get("tactical"),
            daily.get("action_items"),
        )
        return _clip_text(picked or "Track policy, capacity and order conversion before adjusting the homepage emphasis.", 160)
    picked = _first_non_empty(
        daily.get("strategy"),
        daily.get("strategies"),
        daily.get("tactics"),
        daily.get("tactical"),
        daily.get("action_items"),
    )
    return _clip_text(picked or "优先跟踪政策、产能与订单兑现节奏，再决定首页专题强化方向。", 100)


def _risk_for_lang(case_payload: dict[str, Any], lang: str) -> str:
    if lang == "en":
        return "If supply, policy, or pricing signals reverse quickly, the homepage focus may need to be rotated."  # noqa: E501
    return "若供给、政策或价格信号快速反转，当前首页专题需要及时轮换。"


def _evidence_for_lang(case_payload: dict[str, Any], top_item: dict[str, Any], lang: str) -> str:
    date_value = str((case_payload or {}).get("date") or "").strip() or "today"
    record_context = (case_payload or {}).get("record_context") or []
    first_ctx = record_context[0] if isinstance(record_context, list) and record_context else {}
    title = _title_for_lang(top_item, lang)
    if lang == "en":
        ctx = _clip_text(_first_non_empty(first_ctx.get("summary_en"), first_ctx.get("summary_zh")), 120)
        if ctx:
            return f"{date_value} sample context: {ctx}"
        return f"{date_value} public sample highlighted {title} as a homepage focus case."
    ctx = _clip_text(_first_non_empty(first_ctx.get("summary_zh"), first_ctx.get("title")), 90)
    if ctx:
        return f"{date_value} 公开样本：{ctx}"
    return f"{date_value} 公开信号显示《{title}》进入首页专题候选。"


def _baseline_text(case_payload: dict[str, Any], lang: str) -> str:
    top_items = (case_payload or {}).get("top_items") or []
    top_item = top_items[0] if top_items else {}
    title = _title_for_lang(top_item, lang)
    if lang == "en":
        impact = "This topic may affect supply-chain expectations and deserves monitoring."
        return "\n".join([
            f"Core fact: {title}",
            f"Impact path: {impact}",
        ])
    impact = "该主题可能影响供应链预期，值得继续跟踪。"
    return "\n".join([
        f"核心事实：{title}",
        f"影响路径：{impact}",
    ])


def _candidate_text(case_payload: dict[str, Any], lang: str) -> str:
    top_items = (case_payload or {}).get("top_items") or []
    top_item = top_items[0] if top_items else {}
    title = _title_for_lang(top_item, lang)
    impact = _impact_for_lang(case_payload, top_item, lang)
    action = _action_for_lang(case_payload, lang)
    risk = _risk_for_lang(case_payload, lang)
    evidence = _evidence_for_lang(case_payload, top_item, lang)
    if lang == "en":
        return "\n".join([
            f"Core fact: {title}",
            f"Impact path: {impact}",
            f"Investor action: {action}",
            f"Counter-risk: {risk}",
            f"Evidence anchor: {evidence}",
            "⚠️ Risk note: Not investment advice.",
        ])
    return "\n".join([
        f"核心事实：{title}",
        f"影响路径：{impact}",
        f"投资动作：{action}",
        f"反方风险：{risk}",
        f"证据锚点：{evidence}",
        "⚠️ 风险提示：不构成投资建议。",
    ])


def build_homepage_baseline_outputs(dataset_payload: dict[str, Any]) -> dict[str, Any]:
    outputs: dict[str, Any] = {}
    for case_payload in (dataset_payload or {}).get("items") or []:
        case_id = str((case_payload or {}).get("case_id") or "").strip()
        if not case_id:
            continue
        lang = str((case_payload or {}).get("lang") or "zh").strip().lower()
        lang = "en" if lang == "en" else "zh"
        outputs[case_id] = {
            "case_id": case_id,
            "lang": lang,
            "topic_text": _baseline_text(case_payload, lang),
            "meta": {
                "fallback_used": True,
                "source_mode": "homepage_provider_baseline_v1",
            },
        }
    return outputs


def build_homepage_candidate_outputs(dataset_payload: dict[str, Any]) -> dict[str, Any]:
    outputs: dict[str, Any] = {}
    for case_payload in (dataset_payload or {}).get("items") or []:
        case_id = str((case_payload or {}).get("case_id") or "").strip()
        if not case_id:
            continue
        lang = str((case_payload or {}).get("lang") or "zh").strip().lower()
        lang = "en" if lang == "en" else "zh"
        outputs[case_id] = {
            "case_id": case_id,
            "lang": lang,
            "topic_text": _candidate_text(case_payload, lang),
            "meta": {
                "fallback_used": False,
                "source_mode": "homepage_provider_candidate_v1",
            },
        }
    return outputs


def build_homepage_provider_bundle(dataset_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "baseline": build_homepage_baseline_outputs(dataset_payload),
        "candidate": build_homepage_candidate_outputs(dataset_payload),
    }
