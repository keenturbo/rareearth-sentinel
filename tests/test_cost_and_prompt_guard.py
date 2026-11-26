import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import main as main_mod
from scripts import run_tasks as run_tasks_mod
from src.config import Config
from src.extractor import GeminiExtractor, _normalize_en_result
from src.input_guard import prefilter_domain_relevance
from src.premium.briefs import PremiumBriefGenerator
from src.reporter import Reporter
from src.storage import Storage
from src.utils import hash_url, isoformat, now_cst


FORBIDDEN_STANCE_TERMS = [
    "de-risking",
    "reshoring",
    "western governments",
    "western decision makers",
    "中国战略安全",
]

# Must stay longer than input_guard_min_text_len (default 240) so the input
# guard passes and the test reaches the early-exit / token-cap logic.
SAMPLE_ARTICLE = (
    "Lynas Rare Earths announced a $120 million separation upgrade at its Malaysian plant in 2026. "
    "NdPr oxide output reached 1,850 tons in Q3, up 14 percent year-on-year according to the quarterly report. "
    "The company signed a three-year offtake agreement covering 600 tons annually with a Japanese magnet maker. "
    "Site works are scheduled to begin in the second quarter of 2027, subject to final permits."
)


def _make_test_config(tmpdir, **overrides):
    base = Config.from_env()
    defaults = dict(
        gemini_api_key="test-key",
        gemini_model="gemini-3.5-flash-lite",
        data_dir=os.path.join(tmpdir, "data"),
        static_dir=os.path.join(tmpdir, "static"),
        context_mode="token",
        fetch_delay_seconds=0.0,
        analyze_delay_seconds=0.0,
        premium_enabled=False,
        en_analysis_mode="high_score",
        en_min_importance=4,
        en_min_risk_score=70,
        analyze_max_output_tokens=900,
        analyze_en_max_output_tokens=500,
        input_guard_max_prompt_chars=4000,
        prefilter_domain_enabled=True,
        rejected_url_ttl_days=14,
    )
    defaults.update(overrides)
    return replace(base, **defaults)


class TestCostAndPromptGuard(unittest.TestCase):
    def test_domain_prefilter_blocks_irrelevant_news(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _make_test_config(tmpdir)
            cases = [
                (
                    "Toyota unveils new family SUV for 2027",
                    "The automaker announced updated cabin space, hybrid trim options, and dealer pricing.",
                ),
                (
                    "Congress debates IDEA special education funding bill",
                    "School districts across the country reviewed grant allocations under the federal IDEA program.",
                ),
                (
                    "New flagship smartphone review: lithium battery lasts 18 hours",
                    "The handset features a brighter OLED display, faster charging, and improved camera sensors.",
                ),
            ]
            for title, body in cases:
                res = prefilter_domain_relevance(title, body, cfg)
                self.assertFalse(res["pass"], f"Expected block for: {title}")
                self.assertEqual(res["reason"], "off_topic_prefilter")

    def test_domain_prefilter_passes_core_rare_earth_news(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _make_test_config(tmpdir)
            cases = [
                ("Lynas Q3 output report", "NdPr production rose 12% quarter-on-quarter at the separation plant."),
                ("工信部下达稀土开采指标", "北方稀土与中国稀土集团合计获得新一轮冶炼分离配额。"),
                ("Gallium export licensing update", "Refined gallium shipments tightened after new customs rules."),
                ("Lithium mine expansion approved", "The spodumene mining project reached feasibility with 40,000 tpa capacity."),
            ]
            for title, body in cases:
                res = prefilter_domain_relevance(title, body, cfg)
                self.assertTrue(res["pass"], f"Expected pass for: {title}")
                self.assertTrue(res["matched"])

    def test_rejected_url_cache_ttl_and_transient_filter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _make_test_config(tmpdir, rejected_url_ttl_days=14)
            storage = Storage(cfg)

            url_ok = "https://example.com/off-topic-1"
            url_old = "https://example.com/expired-1"
            url_transient = "https://example.com/timeout-1"

            self.assertTrue(storage.append_rejected_url(url_ok, "off_topic_prefilter"))
            for transient in ("timeout", "gemini_exception", "empty_response", "invalid_json", "fetch_fail"):
                self.assertFalse(storage.append_rejected_url(url_transient, transient))

            old_ts = isoformat(now_cst() - timedelta(days=20))
            with open(storage.rejected_urls_path, "a", encoding="utf-8") as f:
                f.write("{corrupted json line\n")
                f.write(
                    json.dumps(
                        {
                            "id": hash_url(url_old),
                            "url": url_old,
                            "reason": "off_topic",
                            "rejected_at": old_ts,
                        }
                    )
                    + "\n"
                )

            rejected_ids = storage.load_rejected_ids()
            self.assertIn(hash_url(url_ok), rejected_ids)
            self.assertNotIn(hash_url(url_old), rejected_ids)
            self.assertNotIn(hash_url(url_transient), rejected_ids)

    def test_en_analysis_gate_high_score_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_high = _make_test_config(tmpdir, en_analysis_mode="high_score", en_min_importance=4, en_min_risk_score=70)
            self.assertFalse(main_mod._should_run_en_analysis(cfg_high, {"importance_score": 3, "risk_score": 65}))
            self.assertTrue(main_mod._should_run_en_analysis(cfg_high, {"importance_score": 4, "risk_score": 50}))
            self.assertTrue(main_mod._should_run_en_analysis(cfg_high, {"importance_score": 2, "risk_score": 70}))

            cfg_off = _make_test_config(tmpdir, en_analysis_mode="off")
            self.assertFalse(main_mod._should_run_en_analysis(cfg_off, {"importance_score": 5, "risk_score": 95}))

            cfg_all = _make_test_config(tmpdir, en_analysis_mode="all")
            self.assertTrue(main_mod._should_run_en_analysis(cfg_all, {"importance_score": 1, "risk_score": 10}))

    def test_prompt_compliance_no_adversarial_terms(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _make_test_config(tmpdir)
            prompts = []

            with patch("src.extractor.genai") as mock_genai:
                mock_model = MagicMock()
                mock_model.generate_content.side_effect = lambda p: (
                    prompts.append(p) or SimpleNamespace(text='{"headline":"ok","body":"ok","bullets":["a"]}')
                )
                mock_genai.GenerativeModel.return_value = mock_model

                for pv in ("v1", "v2"):
                    with patch.dict(os.environ, {"PROMPT_VERSION": pv}):
                        extractor = GeminiExtractor(cfg)
                        prompts.append(extractor._build_zh_prompt("sample content", ""))
                        prompts.append(extractor._build_en_prompt("sample content", ""))
                        extractor.analyze_daily_report("- event A", lang="zh")
                        extractor.analyze_daily_report("- event A", lang="en")

            with patch("src.premium.briefs.genai") as mock_brief_genai:
                mock_brief_model = MagicMock()
                mock_brief_model.generate_content.side_effect = lambda p: (
                    prompts.append(p) or SimpleNamespace(text='{"conclusion":"ok"}')
                )
                mock_brief_genai.GenerativeModel.return_value = mock_brief_model

                sample_rec = {
                    "title": "Sample rare earth update",
                    "content": "Rare earth separation plant announced 2,000 tons NdPr capacity expansion in 2026.",
                    "summary_zh": "稀土分离厂宣布扩产。",
                    "summary_en": "Rare earth separation plant announced expansion.",
                    "minerals": ["NdPr"],
                }
                for pv in ("v1", "v2"):
                    with patch.dict(os.environ, {"PROMPT_VERSION": pv}):
                        gen = PremiumBriefGenerator(cfg)
                        gen.generate(sample_rec, lang="zh", view="policy")
                        gen.generate(sample_rec, lang="en", view="policy")

            for pv in ("v1", "v2"):
                with patch.dict(os.environ, {"PROMPT_VERSION": pv}):
                    entry = {"task_suggestion": "track quota", "report_title": "Q2 Quota", "report_value": "supply"}
                    prompts.append(run_tasks_mod._build_prompt(entry, [], lang="cn"))
                    prompts.append(run_tasks_mod._build_prompt(entry, [], lang="en"))

            self.assertGreaterEqual(len(prompts), 16)
            for idx, prompt in enumerate(prompts):
                low = prompt.lower()
                for forbidden in FORBIDDEN_STANCE_TERMS:
                    self.assertNotIn(
                        forbidden.lower(),
                        low,
                        f"Forbidden term '{forbidden}' found in prompt #{idx}",
                    )

    def test_slim_en_result_inherits_zh_scores_and_forecast_compat(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _make_test_config(tmpdir)
            storage = Storage(cfg)

            zh_analysis = {
                "summary_zh": "某稀土分离项目宣布新增 2000 吨氧化镨钕产能。",
                "country_primary": "Australia",
                "event_location": "Western Australia",
                "minerals": ["NdPr"],
                "entities": ["Lynas Rare Earths"],
                "action_type": "Investment",
                "risk_level": 4,
                "importance_score": 4,
                "china_involved": False,
                "china_dependency": "Medium",
                "china_role": "Mentioned",
                "confidence": 82,
                "china_impact": "中性",
                "china_risk_trend": "持平",
                "expert_summary": "新增产能预计在 12 个月后逐步释放。",
                "recommendations_strategy": ["跟踪项目爬坡进度"],
                "recommendations_tactical": ["关注氧化镨钕季度价格"],
                "key_evidence": ["新增 2000 吨产能", "预计 2027 年投产"],
                "assumptions": ["环保审批按期完成"],
                "audience_fit": "investor",
                "supply_risk_score": 78,
                "policy_risk_score": 66,
                "price_pressure_score": 72,
                "risk_score": 84,
                "forecast_6m": {"price_pressure": "Up", "confidence": 75},
                "forecast_12m": {"price_pressure": "Neutral", "confidence": 65},
                "forecast_24m": {},
                "forecast_48m": {},
            }

            slim_en_raw = {
                "valid_input": True,
                "reject_reason": "",
                "summary_en": "A rare earth separation project announced 2,000 tpa additional NdPr capacity.",
                "impact_west": "Positive",
                "risk_trend_west": "Easing",
                "expert_summary_en": "Additional separated NdPr volume may ease tightness after commissioning.",
                "recommendations_strategy_en": ["Monitor commissioning schedule over next 12 months."],
                "recommendations_tactical_en": ["Track quarterly NdPr oxide contract pricing."],
                "key_evidence": ["2,000 tpa NdPr capacity announced", "Target commissioning in 2027"],
                "assumptions": ["Permitting remains on schedule"],
                "audience_fit": "investor",
            }
            en_analysis = _normalize_en_result(slim_en_raw)
            self.assertNotIn("risk_score_en", en_analysis)
            self.assertNotIn("supply_risk_score_en", en_analysis)

            item = {"title": "Lynas expansion", "url": "https://example.com/lynas-2026", "source_type": "rss"}
            record = storage.build_record(item, zh_analysis, "Article body", analysis_en=en_analysis)
            self.assertEqual(record["risk_score_en"], 84)
            self.assertEqual(record["supply_risk_score_en"], 78)
            self.assertEqual(record["forecast_6m_en"], {"price_pressure": "Up", "confidence": 75})
            self.assertEqual(record["forecast_24m"], {})
            self.assertEqual(record["forecast_24m_en"], {})

            reporter = Reporter(cfg)
            self.assertEqual(reporter._format_forecast("24m", record["forecast_24m"]), "")
            self.assertIn("6m", reporter._format_forecast("6m", record["forecast_6m"]))

    def test_extractor_early_exit_null_and_max_output_tokens(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _make_test_config(tmpdir, analyze_max_output_tokens=900, analyze_en_max_output_tokens=500)

            with patch("src.extractor.genai") as mock_genai:
                zh_model = MagicMock()
                en_model = MagicMock()
                mock_genai.GenerativeModel.side_effect = [zh_model, en_model]

                extractor = GeminiExtractor(cfg)
                calls = mock_genai.GenerativeModel.call_args_list
                self.assertEqual(calls[0].kwargs["generation_config"]["max_output_tokens"], 900)
                self.assertEqual(calls[1].kwargs["generation_config"]["max_output_tokens"], 500)

                zh_model.generate_content.return_value = SimpleNamespace(
                    text='{"valid_input":false,"reject_reason":"off_topic"}'
                )
                self.assertIsNone(extractor.analyze(SAMPLE_ARTICLE))
                self.assertEqual(extractor.last_failure_reason, "off_topic")

                zh_model.generate_content.return_value = SimpleNamespace(text="null")
                self.assertIsNone(extractor.analyze(SAMPLE_ARTICLE))
                self.assertEqual(extractor.last_failure_reason, "off_topic")

                zh_model.generate_content.return_value = SimpleNamespace(text="{truncated json")
                self.assertIsNone(extractor.analyze(SAMPLE_ARTICLE))
                self.assertEqual(extractor.last_failure_reason, "invalid_json")

                # Transient failures must stay distinguishable from deterministic rejects.
                zh_model.generate_content.side_effect = Exception("network down")
                self.assertIsNone(extractor.analyze(SAMPLE_ARTICLE))
                self.assertEqual(extractor.last_failure_reason, "gemini_exception")

    def test_e2e_pipeline_prefilter_reject_cache_and_en_gate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _make_test_config(tmpdir, min_content_chars=100, input_guard_min_text_len=100)

            items = [
                {
                    "source_type": "rss",
                    "title": "Toyota launches 2027 family SUV",
                    "url": "https://example.com/car-suv",
                    "summary": "Auto launch update",
                    "published_at": "2026-11-25T08:00:00+08:00",
                    "language": "en-US",
                },
                {
                    "source_type": "rss",
                    "title": "Minor rare earth drilling update",
                    "url": "https://example.com/ree-low",
                    "summary": "Early drilling assay",
                    "published_at": "2026-11-25T08:10:00+08:00",
                    "language": "en-US",
                },
                {
                    "source_type": "rss",
                    "title": "Major NdPr separation plant expansion approved",
                    "url": "https://example.com/ree-high",
                    "summary": "Large expansion funding approved",
                    "published_at": "2026-11-25T08:20:00+08:00",
                    "language": "en-US",
                },
            ]

            bodies = {
                "https://example.com/car-suv": (
                    "Toyota unveiled its 2027 family SUV with a starting price of $36,000. "
                    "Deliveries begin in Q2 2027 across 450 dealerships with updated infotainment and safety sensors."
                ),
                "https://example.com/ree-low": (
                    "Explorer reported 2026 rare earth drilling assays from 12 shallow holes averaging 0.8% TREO. "
                    "The company plans $2 million follow-up sampling in 2027 before preparing a resource estimate."
                ),
                "https://example.com/ree-high": (
                    "Lynas Rare Earths approved a $320 million NdPr separation expansion targeting 3,000 tons annual output. "
                    "Construction starts in Q1 2027 with 60% output committed under multi-year magnet offtake contracts."
                ),
            }

            class FakeSource:
                def fetch_items(self):
                    return list(items)

            fetch_calls = []

            class FakeFetcher:
                def __init__(self, _cfg):
                    pass

                def fetch(self, url):
                    fetch_calls.append(url)
                    return {"content": bodies[url], "status": "ok", "resolved_url": url}

            zh_responses = [
                json.dumps(
                    {
                        "valid_input": True,
                        "reject_reason": "",
                        "summary_zh": "某矿企公布 12 个浅孔稀土钻探结果，平均品位 0.8% TREO。",
                        "country_primary": "Australia",
                        "event_location": "Queensland",
                        "minerals": ["Rare Earths"],
                        "entities": ["Explorer Ltd"],
                        "action_type": "Discovery",
                        "risk_level": 2,
                        "importance_score": 2,
                        "china_involved": False,
                        "china_dependency": "None",
                        "china_role": "Mentioned",
                        "confidence": 72,
                        "china_impact": "中性",
                        "china_risk_trend": "持平",
                        "expert_summary": "尚处于早期勘探阶段，短期不影响全球供需。",
                        "recommendations_strategy": [],
                        "recommendations_tactical": [],
                        "supply_risk_score": 30,
                        "policy_risk_score": 20,
                        "price_pressure_score": 25,
                        "risk_score": 35,
                        "forecast_6m": {"price_pressure": "Neutral", "confidence": 60},
                        "forecast_12m": {"price_pressure": "Neutral", "confidence": 55},
                        "key_evidence": ["完成 12 个浅孔钻探", "平均品位 0.8% TREO"],
                        "assumptions": ["尚未形成资源量估算"],
                        "audience_fit": "industry",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "valid_input": True,
                        "reject_reason": "",
                        "summary_zh": "Lynas 批准 3.2 亿美元氧化镨钕分离扩产项目，规划年产 3000 吨。",
                        "country_primary": "Australia",
                        "event_location": "Western Australia",
                        "minerals": ["NdPr"],
                        "entities": ["Lynas Rare Earths"],
                        "action_type": "Investment",
                        "risk_level": 4,
                        "importance_score": 4,
                        "china_involved": False,
                        "china_dependency": "Medium",
                        "china_role": "Mentioned",
                        "confidence": 85,
                        "china_impact": "中性",
                        "china_risk_trend": "持平",
                        "expert_summary": "新增 3000 吨氧化镨钕产能将提升中长期海外供给弹性。",
                        "recommendations_strategy": ["跟踪 2027 年一季度开工进展"],
                        "recommendations_tactical": ["观察长协定价对现货溢价影响"],
                        "supply_risk_score": 76,
                        "policy_risk_score": 60,
                        "price_pressure_score": 70,
                        "risk_score": 82,
                        "forecast_6m": {"price_pressure": "Up", "confidence": 75},
                        "forecast_12m": {"price_pressure": "Neutral", "confidence": 65},
                        "key_evidence": ["项目投资 3.2 亿美元", "规划年产 3000 吨氧化镨钕，60% 已签长协"],
                        "assumptions": ["建设周期与资本开支按计划推进"],
                        "audience_fit": "investor",
                    },
                    ensure_ascii=False,
                ),
            ]
            en_response = json.dumps(
                {
                    "valid_input": True,
                    "reject_reason": "",
                    "summary_en": "Lynas approved a $320M NdPr separation expansion targeting 3,000 tpa with 60% contracted.",
                    "impact_west": "Positive",
                    "risk_trend_west": "Easing",
                    "expert_summary_en": "Contracted NdPr separation capacity improves medium-term supply visibility.",
                    "recommendations_strategy_en": ["Monitor Q1 2027 construction start milestones."],
                    "recommendations_tactical_en": ["Track NdPr contract spreads versus spot prices."],
                    "key_evidence": ["$320M capex approved", "3,000 tpa capacity with 60% offtake committed"],
                    "assumptions": ["Construction timeline stays on schedule"],
                    "audience_fit": "investor",
                }
            )

            zh_model = MagicMock()
            zh_model.generate_content.side_effect = [SimpleNamespace(text=t) for t in zh_responses]
            en_model = MagicMock()
            en_model.generate_content.side_effect = [SimpleNamespace(text=en_response)]

            with (
                patch("src.extractor.genai") as mock_genai,
                patch.object(main_mod, "get_sources", return_value=[FakeSource()]),
                patch.object(main_mod, "Fetcher", FakeFetcher),
                patch.object(main_mod.Reporter, "rebuild", return_value=None),
                patch.object(main_mod, "_postprocess_home_top_tracks", return_value=None),
                patch.object(main_mod, "post_telegram_records", return_value=(0, 0)),
                patch.object(main_mod, "post_telegram_top3_due", return_value=(0, 0)),
            ):
                mock_genai.GenerativeModel.side_effect = [zh_model, en_model]
                stats1 = main_mod.run_pipeline(cfg)

                self.assertEqual(stats1["prefilter_skip"], 1)
                self.assertEqual(stats1["en_skipped"], 1)
                self.assertEqual(stats1["saved"], 2)
                self.assertEqual(zh_model.generate_content.call_count, 2)
                self.assertEqual(en_model.generate_content.call_count, 1)

                records = list(Storage(cfg).iter_records())
                self.assertEqual(len(records), 2)
                by_url = {r["url"]: r for r in records}
                self.assertEqual(by_url["https://example.com/ree-low"]["summary_en"], "")
                self.assertIn("Lynas approved", by_url["https://example.com/ree-high"]["summary_en"])
                self.assertEqual(by_url["https://example.com/ree-high"]["risk_score_en"], 82)
                self.assertEqual(by_url["https://example.com/ree-high"]["forecast_24m"], {})

                # Second run: off-topic URL must hit cached_reject without fetching or calling Gemini
                fetch_calls.clear()
                zh_model2 = MagicMock()
                en_model2 = MagicMock()
                mock_genai.GenerativeModel.side_effect = [zh_model2, en_model2]

                stats2 = main_mod.run_pipeline(cfg)
                self.assertEqual(stats2["cached_reject"], 1)
                self.assertEqual(stats2["seen"], 2)
                self.assertEqual(stats2["saved"], 0)
                self.assertEqual(len(fetch_calls), 0)
                self.assertEqual(zh_model2.generate_content.call_count, 0)
                self.assertEqual(en_model2.generate_content.call_count, 0)


if __name__ == "__main__":
    unittest.main()
