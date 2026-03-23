from __future__ import annotations

from typing import Any


DECISION_KEEP = "keep"
DECISION_DISCARD = "discard"
DECISION_NEEDS_REVIEW = "needs_review"

SCORE_DELTA_KEEP_THRESHOLD = 0.03
SCORE_DELTA_DISCARD_THRESHOLD = -0.03


def _safe_score(summary: dict[str, Any] | None) -> float:
    if not isinstance(summary, dict):
        return 0.0
    try:
        return round(float(summary.get("composite_score") or 0.0), 4)
    except (TypeError, ValueError):
        return 0.0


def _hard_fail_homepage(summary: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if float(summary.get("stale_claim_case_rate") or 0.0) > 0:
        flags.append("stale_claim_rate_nonzero")
    if float(summary.get("non_empty_rate") or 0.0) <= 0:
        flags.append("non_empty_rate_zero")
    if float(summary.get("evidence_anchor_pass_rate") or 0.0) <= 0:
        flags.append("evidence_anchor_pass_rate_zero")
    return flags


def _hard_fail_social(summary: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if float(summary.get("publishable_rate") or 0.0) <= 0:
        flags.append("publishable_rate_zero")
    if float(summary.get("evidence_anchor_rate") or 0.0) <= 0:
        flags.append("evidence_anchor_rate_zero")
    if float(summary.get("assembly_noise_fail_rate") or 0.0) > 0.5:
        flags.append("assembly_noise_fail_rate_high")
    return flags


def _hard_fail_variants(summary: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if float(summary.get("validator_pass_rate") or 0.0) <= 0:
        flags.append("validator_pass_rate_zero")
    if float(summary.get("deep_dive_pass_rate") or 0.0) <= 0:
        flags.append("deep_dive_pass_rate_zero")
    if float(summary.get("fallback_rate") or 0.0) >= 1.0:
        flags.append("fallback_rate_full")
    return flags


def detect_hard_fail(family: str, candidate_summary: dict[str, Any] | None) -> dict[str, Any]:
    summary = candidate_summary or {}
    if family == "homepage_topic_policy":
        flags = _hard_fail_homepage(summary)
    elif family == "social_copy_policy":
        flags = _hard_fail_social(summary)
    elif family == "content_variants_policy":
        flags = _hard_fail_variants(summary)
    else:
        flags = [f"unsupported_family:{family}"]
    return {"hard_fail": bool(flags), "flags": flags}


def _review_flags_homepage(baseline: dict[str, Any], candidate: dict[str, Any], delta: float) -> list[str]:
    flags: list[str] = []
    if delta < SCORE_DELTA_KEEP_THRESHOLD and delta > SCORE_DELTA_DISCARD_THRESHOLD:
        flags.append("score_delta_small")
    if float(candidate.get("fallback_rate") or 0.0) > float(baseline.get("fallback_rate") or 0.0):
        flags.append("fallback_rate_not_improved")
    if float(candidate.get("duplicate_case_rate") or 0.0) > float(baseline.get("duplicate_case_rate") or 0.0):
        flags.append("duplicate_case_rate_not_improved")
    if float(candidate.get("required_labels_rate") or 0.0) < float(baseline.get("required_labels_rate") or 0.0):
        flags.append("required_labels_rate_regressed")
    return flags


def _review_flags_social(baseline: dict[str, Any], candidate: dict[str, Any], delta: float) -> list[str]:
    flags: list[str] = []
    if delta < SCORE_DELTA_KEEP_THRESHOLD and delta > SCORE_DELTA_DISCARD_THRESHOLD:
        flags.append("score_delta_small")
    if float(candidate.get("fallback_rate") or 0.0) >= float(baseline.get("fallback_rate") or 0.0):
        flags.append("fallback_rate_not_improved")
    if float(candidate.get("subject_clear_rate") or 0.0) < float(baseline.get("subject_clear_rate") or 0.0):
        flags.append("subject_clear_not_improved")
    if float(candidate.get("external_dm_split_rate") or 0.0) < float(baseline.get("external_dm_split_rate") or 0.0):
        flags.append("external_dm_split_not_improved")
    base_reasons = baseline.get("top_validator_reasons") or []
    cand_reasons = candidate.get("top_validator_reasons") or []
    base_top = base_reasons[0]["reason"] if base_reasons else ""
    cand_top = cand_reasons[0]["reason"] if cand_reasons else ""
    if base_top != cand_top and cand_top:
        flags.append("top_validator_reason_changed")
    return flags


def _review_flags_variants(baseline: dict[str, Any], candidate: dict[str, Any], delta: float) -> list[str]:
    flags: list[str] = []
    if delta < SCORE_DELTA_KEEP_THRESHOLD and delta > SCORE_DELTA_DISCARD_THRESHOLD:
        flags.append("score_delta_small")
    if float(candidate.get("today_hit_rate") or 0.0) < float(baseline.get("today_hit_rate") or 0.0):
        flags.append("today_hit_rate_regressed")
    if float(candidate.get("fallback_rate") or 0.0) >= float(baseline.get("fallback_rate") or 0.0):
        flags.append("fallback_rate_not_improved")
    if float(candidate.get("avg_similarity_penalty") or 0.0) > float(baseline.get("avg_similarity_penalty") or 0.0):
        flags.append("avg_similarity_penalty_not_improved")
    base_fields = baseline.get("top_failed_fields") or []
    cand_fields = candidate.get("top_failed_fields") or []
    base_top = base_fields[0]["field"] if base_fields else ""
    cand_top = cand_fields[0]["field"] if cand_fields else ""
    if base_top != cand_top and cand_top:
        flags.append("top_failed_field_shifted")
    return flags


def detect_review_flags(family: str, baseline_summary: dict[str, Any] | None, candidate_summary: dict[str, Any] | None) -> list[str]:
    baseline = baseline_summary or {}
    candidate = candidate_summary or {}
    delta = round(_safe_score(candidate) - _safe_score(baseline), 4)
    if family == "homepage_topic_policy":
        return _review_flags_homepage(baseline, candidate, delta)
    if family == "social_copy_policy":
        return _review_flags_social(baseline, candidate, delta)
    if family == "content_variants_policy":
        return _review_flags_variants(baseline, candidate, delta)
    return [f"unsupported_family:{family}"]


def compare_batch_results(family: str, baseline_summary: dict[str, Any] | None, candidate_summary: dict[str, Any] | None) -> dict[str, Any]:
    baseline_score = _safe_score(baseline_summary)
    candidate_score = _safe_score(candidate_summary)
    score_delta = round(candidate_score - baseline_score, 4)
    hard_fail = detect_hard_fail(family, candidate_summary)
    review_flags = detect_review_flags(family, baseline_summary, candidate_summary)

    if hard_fail.get("hard_fail"):
        reason = f"hard_fail: {hard_fail['flags'][0]}"
        return {
            "decision": DECISION_DISCARD,
            "reason": reason,
            "baseline_score": baseline_score,
            "candidate_score": candidate_score,
            "score_delta": score_delta,
            "hard_fail": True,
            "review_flags": review_flags,
        }

    if score_delta <= SCORE_DELTA_DISCARD_THRESHOLD:
        return {
            "decision": DECISION_DISCARD,
            "reason": "candidate_score_lower",
            "baseline_score": baseline_score,
            "candidate_score": candidate_score,
            "score_delta": score_delta,
            "hard_fail": False,
            "review_flags": review_flags,
        }

    if score_delta >= SCORE_DELTA_KEEP_THRESHOLD and not review_flags:
        return {
            "decision": DECISION_KEEP,
            "reason": "candidate_score_higher_without_hard_fail",
            "baseline_score": baseline_score,
            "candidate_score": candidate_score,
            "score_delta": score_delta,
            "hard_fail": False,
            "review_flags": [],
        }

    reason = review_flags[0] if review_flags else "score_delta_small"
    return {
        "decision": DECISION_NEEDS_REVIEW,
        "reason": reason,
        "baseline_score": baseline_score,
        "candidate_score": candidate_score,
        "score_delta": score_delta,
        "hard_fail": False,
        "review_flags": review_flags,
    }
