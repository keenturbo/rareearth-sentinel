#!/usr/bin/env python3
from pathlib import Path
import re
import py_compile

MAIN = Path('/root/rareearth-sentinel/main.py')


def inject_import(text: str) -> str:
    marker = 'from src.telegram import post_telegram_records, post_telegram_top3_due\n'
    if 'from src.social_copy_ai import generate_social_copy_bundle' in text:
        return text
    block = (
        marker
        + "\ntry:\n"
        + "    from src.social_copy_ai import generate_social_copy_bundle\n"
        + "except Exception:\n"
        + "    generate_social_copy_bundle = None\n"
    )
    if marker not in text:
        raise RuntimeError('import marker not found')
    return text.replace(marker, block, 1)


def inject_helper(text: str) -> str:
    if 'def _build_home_ai_topic_html(' in text:
        return text

    helper = '''\n\ndef _build_home_ai_topic_html(topic_text, items, valid_report_ids, lang="zh"):
    raw = str(topic_text or "").strip()
    if not raw:
        return ""

    lines = []
    for line in raw.splitlines():
        s = _normalize_punct(line).strip()
        if not s:
            continue
        low = s.lower()
        if re.search(r"https?://", s):
            continue
        if s.startswith("#"):
            continue
        if s.startswith("私信回复") or low.startswith("dm for full report"):
            continue
        if low in {"today's focus", "todays focus", "top tracks"}:
            continue
        if s.startswith("【") and s.endswith("】"):
            continue
        s = s.replace("...", "").replace("…", "").strip()
        if s:
            lines.append(s)

    if not lines:
        return ""

    if lang == "zh":
        subdir = "cn"
        btn_label = "解锁深度报告"
        sep = "："
    else:
        subdir = "en"
        btn_label = "Unlock Deep Report"
        sep = ": "

    primary_report_id = _pick_primary_report_id(items or [], valid_report_ids)
    unlock = f"/{subdir}/pay/{primary_report_id}.html" if primary_report_id else "mailto:sentinel@dataspices.com"

    out = []
    for s in lines[:8]:
        if "：" in s:
            k, v = s.split("：", 1)
            k = k.strip()
            v = v.strip()
            if k and v and len(k) <= 16:
                out.append(
                    f'<div class="premium-reason"><span class="label-gray">{html_lib.escape(k)}</span>{sep}{html_lib.escape(v)}</div>'
                )
                continue
        if ":" in s and lang == "en":
            k, v = s.split(":", 1)
            k = k.strip()
            v = v.strip()
            if k and v and len(k) <= 20:
                out.append(
                    f'<div class="premium-reason"><span class="label-gray">{html_lib.escape(k)}</span>{sep}{html_lib.escape(v)}</div>'
                )
                continue
        out.append(f'<div class="premium-reason">{html_lib.escape(s)}</div>')

    return (
        '<div class="premium-item premium-aggregate">'
        + ''.join(out)
        + f'<a class="premium-btn" href="{html_lib.escape(unlock)}">{html_lib.escape(btn_label)}</a>'
        + '</div>'
    )
'''

    anchor = 'def _replace_top_tracks_section('
    if anchor not in text:
        raise RuntimeError('helper anchor not found')
    return text.replace(anchor, helper + '\n' + anchor, 1)


def inject_postprocess(text: str) -> str:
    old = '''    aggregate_cn = _build_home_aggregate_html(
        top3_data, valid_report_ids, daily_zh, lang="zh"
    )
    aggregate_en = _build_home_aggregate_html(
        top3_data, valid_report_ids, daily_en, lang="en"
    )
'''

    new = '''    social_bundle = None
    if callable(generate_social_copy_bundle):
        try:
            social_bundle = generate_social_copy_bundle(
                config,
                top3_data,
                daily_zh,
                daily_en,
                valid_report_ids,
                timeout_seconds=8,
            )
        except Exception:
            social_bundle = None

    top_items = top3_data.get("items") if isinstance(top3_data, dict) else []

    aggregate_cn = ""
    aggregate_en = ""
    if social_bundle:
        aggregate_cn = _build_home_ai_topic_html(
            social_bundle.get("cn_topic"), top_items, valid_report_ids, lang="zh"
        )
        aggregate_en = _build_home_ai_topic_html(
            social_bundle.get("en_topic"), top_items, valid_report_ids, lang="en"
        )

    if not aggregate_cn:
        aggregate_cn = _build_home_aggregate_html(
            top3_data, valid_report_ids, daily_zh, lang="zh"
        )
    if not aggregate_en:
        aggregate_en = _build_home_aggregate_html(
            top3_data, valid_report_ids, daily_en, lang="en"
        )
'''

    if new in text:
        return text
    if old not in text:
        raise RuntimeError('postprocess block not found')
    return text.replace(old, new, 1)


def main():
    if not MAIN.exists():
        raise SystemExit(f'main.py not found: {MAIN}')

    src = MAIN.read_text(encoding='utf-8')
    out = src
    out = inject_import(out)
    out = inject_helper(out)
    out = inject_postprocess(out)

    if out == src:
        print('no changes')
        return

    bak = MAIN.with_suffix('.py.bak.ai_topic')
    bak.write_text(src, encoding='utf-8')
    MAIN.write_text(out, encoding='utf-8')
    py_compile.compile(str(MAIN), doraise=True)
    print('patched:', MAIN)
    print('backup :', bak)


if __name__ == '__main__':
    main()
