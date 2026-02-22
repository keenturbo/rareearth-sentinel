#!/usr/bin/env python3
import html
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config

START_MARK = "<!-- WON_REVIEW_CARDS_START -->"
END_MARK = "<!-- WON_REVIEW_CARDS_END -->"


def _read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def _parse_dt(value):
    s = str(value or "").strip()
    if not s:
        return None
    s = s.replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fmt_cn_text(card):
    lead_id = str(card.get("lead_id") or "-").strip()
    company = str(card.get("company") or card.get("name") or "客户").strip()
    won_at = str(card.get("won_at") or "-").strip()
    reason = str(card.get("close_reason_text") or card.get("close_reason_code") or "原因未知").strip()
    tmpl = str(card.get("proposal_template_version") or card.get("proposal_template") or "unknown").strip()
    why = str(card.get("why_won") or "成交原因未记录").strip()
    nxt = str(card.get("next_7d_action") or "D+7 复购触达并记录反馈").strip()
    return (
        f"【成交复盘卡】\n"
        f"线索ID：{lead_id}\n"
        f"客户：{company}\n"
        f"成交时间：{won_at}\n"
        f"成交归因：{reason}\n"
        f"模板版本：{tmpl}\n"
        f"为何成交：{why}\n"
        f"7天动作：{nxt}"
    )


def _fmt_en_text(card):
    lead_id = str(card.get("lead_id") or "-").strip()
    company = str(card.get("company") or card.get("name") or "client").strip()
    won_at = str(card.get("won_at") or "-").strip()
    reason = str(card.get("close_reason_text") or card.get("close_reason_code") or "unknown").strip()
    tmpl = str(card.get("proposal_template_version") or card.get("proposal_template") or "unknown").strip()
    why = str(card.get("why_won") or "No win reason recorded").strip()
    nxt = str(card.get("next_7d_action") or "D+7 upsell touchpoint with feedback log").strip()
    return (
        f"[Won Review Card]\n"
        f"Lead ID: {lead_id}\n"
        f"Account: {company}\n"
        f"Won at: {won_at}\n"
        f"Reason: {reason}\n"
        f"Template version: {tmpl}\n"
        f"Why won: {why}\n"
        f"Next 7d action: {nxt}"
    )


def _render_rows(cards):
    if not cards:
        return '<tr><td colspan="8" class="muted">暂无成交复盘卡（won review cards）</td></tr>'

    out = []
    for c in cards[:12]:
        lead_id = html.escape(str(c.get("lead_id") or ""))
        won_at = html.escape(str(c.get("won_at") or ""))
        company = html.escape(str(c.get("company") or c.get("name") or "客户"))
        owner = html.escape(str(c.get("owner") or ""))
        channel = html.escape(str(c.get("channel") or ""))
        reason = html.escape(str(c.get("close_reason_text") or c.get("close_reason_code") or "原因未知"))
        tmpl = html.escape(str(c.get("proposal_template_version") or c.get("proposal_template") or "unknown"))
        why = html.escape(str(c.get("why_won") or "成交原因未记录"))
        nxt = html.escape(str(c.get("next_7d_action") or "D+7 复购触达并记录反馈"))

        cn_txt = html.escape(_fmt_cn_text(c))
        en_txt = html.escape(_fmt_en_text(c))

        out.append(
            "<tr>"
            f"<td>{won_at}</td>"
            f"<td>{lead_id}</td>"
            f"<td>{company}</td>"
            f"<td><div>{owner or '-'}</div><div class='muted'>{channel or '-'}</div></td>"
            f"<td><div>{reason}</div><div class='muted'>{tmpl}</div></td>"
            f"<td>{why}</td>"
            f"<td>{nxt}</td>"
            "<td>"
            f"<textarea class='won-review-cn' style='position:fixed;left:-9999px;top:-9999px'>{cn_txt}</textarea>"
            f"<textarea class='won-review-en' style='position:fixed;left:-9999px;top:-9999px'>{en_txt}</textarea>"
            "<div class='action-row'>"
            "<button class='copy-quote-cn-btn won-review-copy-cn' onclick='var ta=this.closest(\"td\").querySelector(\".won-review-cn\");if(!ta)return false;ta.select();try{document.execCommand(\"copy\");}catch(e){}return false;'>复制中文复盘</button>"
            "<button class='copy-quote-en-btn won-review-copy-en' onclick='var ta=this.closest(\"td\").querySelector(\".won-review-en\");if(!ta)return false;ta.select();try{document.execCommand(\"copy\");}catch(e){}return false;'>Copy EN Review</button>"
            "</div>"
            "</td>"
            "</tr>"
        )
    return "".join(out)


def _build_section(cards, cards_path):
    rows_html = _render_rows(cards)
    top_reason = "暂无"
    top_tmpl = "暂无"
    if cards:
        reason_count = {}
        tmpl_count = {}
        for c in cards:
            r = str(c.get("close_reason_text") or c.get("close_reason_code") or "原因未知").strip() or "原因未知"
            t = str(c.get("proposal_template_version") or c.get("proposal_template") or "unknown").strip() or "unknown"
            reason_count[r] = reason_count.get(r, 0) + 1
            tmpl_count[t] = tmpl_count.get(t, 0) + 1
        top_reason = sorted(reason_count.items(), key=lambda kv: kv[1], reverse=True)[0][0]
        top_tmpl = sorted(tmpl_count.items(), key=lambda kv: kv[1], reverse=True)[0][0]

    return (
        f"{START_MARK}\n"
        "  <section class=\"section\">\n"
        "    <div class=\"path\">成交复盘卡（Won Review Card） · 仅展示近12条 · " + html.escape(cards_path) + "</div>\n"
        "    <div class=\"stats\" style=\"grid-template-columns:repeat(3,minmax(140px,1fr));margin-bottom:10px\">\n"
        f"      <div class=\"stat\"><div class=\"k\">复盘卡总量</div><div class=\"v\">{len(cards)}</div></div>\n"
        f"      <div class=\"stat\"><div class=\"k\">高频成交归因</div><div class=\"v\" style=\"font-size:14px\">{html.escape(top_reason)}</div></div>\n"
        f"      <div class=\"stat\"><div class=\"k\">高频模板版本</div><div class=\"v\" style=\"font-size:14px\">{html.escape(top_tmpl)}</div></div>\n"
        "    </div>\n"
        "    <div class=\"table-wrap\">\n"
        "      <table>\n"
        "        <thead>\n"
        "          <tr><th>成交时间</th><th>线索ID</th><th>客户</th><th>负责人/渠道</th><th>归因/模板</th><th>为何成交</th><th>7天动作</th><th>复制</th></tr>\n"
        "        </thead>\n"
        "        <tbody>\n"
        f"{rows_html}\n"
        "        </tbody>\n"
        "      </table>\n"
        "    </div>\n"
        "  </section>\n"
        f"{END_MARK}\n"
    )


def patch_leads_page(config: Config):
    leads_html_path = os.path.join(config.static_dir, "cn", "admin", "leads", "team-brief", "index.html")
    cards_path = os.path.join(config.data_dir, "status", "won_review_cards.jsonl")

    if not os.path.exists(leads_html_path):
        return {"ok": False, "error": "leads_page_not_found", "path": leads_html_path}

    cards = _read_jsonl(cards_path)
    cards.sort(key=lambda x: str(x.get("won_at") or x.get("generated_at") or ""), reverse=True)

    with open(leads_html_path, "r", encoding="utf-8") as f:
        html_doc = f.read()

    section = _build_section(cards, cards_path)

    if START_MARK in html_doc and END_MARK in html_doc:
        a = html_doc.find(START_MARK)
        b = html_doc.find(END_MARK)
        if a >= 0 and b >= 0 and b > a:
            b = b + len(END_MARK)
            html_doc = html_doc[:a] + section + html_doc[b:]
    else:
        marker = '  <section class="section">'
        if marker in html_doc:
            html_doc = html_doc.replace(marker, section + "\n" + marker, 1)
        else:
            html_doc = html_doc + "\n" + section

    with open(leads_html_path, "w", encoding="utf-8") as f:
        f.write(html_doc)

    return {
        "ok": True,
        "path": leads_html_path,
        "cards": len(cards),
    }


def main():
    cfg = Config.from_env()
    result = patch_leads_page(cfg)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
