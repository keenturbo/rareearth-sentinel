#!/usr/bin/env python3
import html
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.storage import Storage
from src.utils import now_cst, isoformat


def _safe_list(value, limit=6):
    if value is None:
        return []
    if isinstance(value, list):
        items = [str(v).strip() for v in value if str(v).strip()]
    elif isinstance(value, str):
        items = [v.strip("-• ").strip() for v in value.splitlines() if v.strip()]
    else:
        items = []
    return items[:limit]


def _clip(text, limit=120):
    s = str(text or "").strip()
    if len(s) <= limit:
        return s
    return s[:limit].rstrip("，。；;,. ") + "…"


def _render_list(items, empty_text="-"):
    rows = _safe_list(items, limit=8)
    if not rows:
        return f'<div class="meta">{html.escape(empty_text)}</div>'
    lis = "".join(f"<li>{html.escape(_clip(x, 220))}</li>" for x in rows)
    return f"<ul>{lis}</ul>"


def _read_variants(storage):
    rows = list(storage.iter_content_variants() or [])
    rows.sort(key=lambda x: str(x.get("generated_at") or ""), reverse=True)
    return rows


def _status_badge(validator):
    validator = validator or {}
    passed = bool(validator.get("passed"))
    fallback = bool(validator.get("fallback_used"))
    reason = str(validator.get("reason") or "").strip()
    issues = validator.get("issues") or []
    if passed and not fallback:
        return '<span class="badge ok">PASS</span>'
    issue_text = html.escape(reason or (issues[0] if issues else "fallback"))
    return f'<span class="badge warn">FALLBACK</span><span class="meta inline-reason">{issue_text}</span>'


def _variant_block(prefix, label, content):
    title = str((content or {}).get("title") or "").strip()
    body = str((content or {}).get("content") or "").strip()
    q = content.get("quality_score")
    quality_html = f'<span class="meta">质量分 {html.escape(str(q))}</span>' if q not in (None, "") else ""
    textarea_id = f"variant-{prefix}"
    return (
        '<div class="variant-box">'
        f'<div class="variant-head"><div class="variant-title">{html.escape(label)}</div>{quality_html}</div>'
        f'<div class="meta variant-subtitle">{html.escape(title)}</div>'
        f'<textarea id="{html.escape(textarea_id)}" readonly>{html.escape(body)}</textarea>'
        '<div class="actions">'
        f'<button class="btn primary" data-copy="{html.escape(textarea_id)}" data-status="status-{html.escape(textarea_id)}">复制</button>'
        f'<span class="copy-status" id="status-{html.escape(textarea_id)}"></span>'
        '</div>'
        '</div>'
    )


def _render_card(entry, idx):
    source_pack = entry.get("source_pack") or {}
    variants = entry.get("variants") or {}
    grounding = source_pack.get("grounding") or {}
    premium = source_pack.get("premium") or {}
    validator = entry.get("validator") or {}

    title = str(source_pack.get("title") or entry.get("record_id") or f"事件 {idx + 1}").strip()
    url = str(source_pack.get("url") or "").strip()
    generated_at = str(entry.get("generated_at") or "").strip()
    risk_score = source_pack.get("risk_score")
    importance_score = source_pack.get("importance_score")
    model = str(entry.get("model") or "").strip()
    record_id = str(entry.get("record_id") or "").strip()

    hard_html = _render_list(grounding.get("hard_facts"), empty_text="暂无硬事实")
    soft_html = _render_list(grounding.get("soft_inferences"), empty_text="暂无合理推断")
    unknown_html = _render_list(grounding.get("unknowns"), empty_text="暂无待验证点")
    evidence_html = _render_list(source_pack.get("key_evidence"), empty_text="暂无关键证据")
    assumptions_html = _render_list(source_pack.get("assumptions"), empty_text="暂无假设")
    tracking_html = _render_list(premium.get("tracking_nodes"), empty_text="暂无追踪节点")

    title_link = html.escape(title)
    if url:
        title_link = f'<a href="{html.escape(url)}" target="_blank" rel="noreferrer">{html.escape(title)}</a>'

    card_prefix = f"{idx}"

    return (
        '<section class="card">'
        '<div class="card-top">'
        '<div class="card-main">'
        f'<div class="card-title">{title_link}</div>'
        f'<div class="card-meta">{html.escape(generated_at)} · record_id={html.escape(record_id)} · risk={html.escape(str(risk_score))} · importance={html.escape(str(importance_score))}</div>'
        f'<div class="card-meta">model={html.escape(model)}</div>'
        '</div>'
        f'<div class="card-status">{_status_badge(validator)}</div>'
        '</div>'
        '<div class="source-pack">'
        '<div class="source-grid">'
        '<div class="source-col">'
        '<div class="block-title">摘要</div>'
        f'<div class="block-text">{html.escape(str(source_pack.get("summary_zh") or "-"))}</div>'
        '<div class="block-title">专家结论</div>'
        f'<div class="block-text">{html.escape(str(source_pack.get("expert_summary") or premium.get("conclusion") or "-"))}</div>'
        '<div class="block-title">关键证据</div>'
        f'{evidence_html}'
        '<div class="block-title">待验证假设</div>'
        f'{assumptions_html}'
        '</div>'
        '<div class="source-col">'
        '<div class="block-title">原文可证</div>'
        f'{hard_html}'
        '<div class="block-title">合理推断</div>'
        f'{soft_html}'
        '<div class="block-title">待验证点</div>'
        f'{unknown_html}'
        '<div class="block-title">追踪节点</div>'
        f'{tracking_html}'
        '</div>'
        '</div>'
        '</div>'
        '<div class="variants-grid">'
        f'{_variant_block(card_prefix + "-xq", "雪球长帖版", variants.get("xueqiu_long") or {})}'
        f'{_variant_block(card_prefix + "-pro", "哨兵研判版", variants.get("sentinel_pro") or {})}'
        f'{_variant_block(card_prefix + "-deep", "深水区版本", variants.get("deep_dive") or {})}'
        '</div>'
        '</section>'
    )


def _render_entries(entries):
    if not entries:
        return '<div class="empty">暂无三版本草稿</div>'
    return "".join(_render_card(entry, idx) for idx, entry in enumerate(entries))


def _build_page(template_path, output_path, entries):
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    html_out = (
        template.replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{TOTAL_COUNT}}", str(len(entries)))
        .replace("{{VARIANTS_LIST}}", _render_entries(entries))
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)


def main():
    config = Config.from_env()
    storage = Storage(config)
    entries = _read_variants(storage)

    template_path = os.path.join(config.templates_dir, "admin_content_variants.html")
    output_path = os.path.join(config.static_dir, "cn", "admin", "content-variants", "index.html")
    _build_page(template_path, output_path, entries)
    print(f"admin content variants page built: {len(entries)} items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
