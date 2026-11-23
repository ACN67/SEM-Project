"""Offline generation, dependency, evidence and model-transport checks."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import unittest
from unittest.mock import Mock

from pydantic import TypeAdapter

from backend.app.contracts import models as m
from backend.app.contracts.services import Generation, ServiceError
from backend.app.contracts.validation import check_generation
from backend.app.generation import ChatCompletionsClient, GenerationService, summarize_chapter
from backend.app.generation.analysis_context import quarterly_analysis
from backend.app.quality import QualityService, format_percent

FIXTURES = Path(__file__).resolve().parents[3] / "shared/contracts/fixtures"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.context = m.ChapterInput.model_validate(fixture("chapter_input"))
        self.output = fixture("chapter")
        self.writer = Mock()
        self.writer.complete.side_effect = lambda *_: json.dumps(self.output)
        self.service: Generation = GenerationService(self.writer)

    def prosperity_context(self, observations, *, end_year="2024"):
        spec = m.Prosperity.model_validate(fixture("industry_prosperity") | {
            "indicator": "nev_sales",
            "period": {"from": "2024", "to": end_year},
        })
        task = self.context.task.model_copy(update={"required_metrics": [spec.indicator]})
        raw = [
            self.context.metrics[0].model_copy(update={
                "metric_id": period, "indicator": spec.indicator, "granularity": "quarter",
                "period": period, "raw_value": value, "value": value, "unit": "万辆", "raw_unit": "万辆",
            })
            for period, value in observations
        ]
        return self.context.model_copy(update={
            "spec": spec, "template_ref": m.TemplateRef(template_id=spec.template_id, version=1),
            "task": task, "metrics": QualityService().calculate_metrics(raw, task).metrics,
        })

    def template(self, spec):
        summary = self.context.task.model_copy(update={
            "chapter_id": "ch_summary",
            "section_key": "summary",
            "title": "摘要",
            "depends_on": ["market"],
        })
        return m.Template(
            template_id=spec.template_id, version=1, report_type=spec.report_type,
            title="测试模板", sections=[summary, self.context.task], layout_version="1",
            input_schema={},
        )

    def test_outline_for_five_forms(self):
        for kind in (
            "industry_analysis", "enterprise_comparison", "industry_prosperity",
            "investment_attraction", "overseas_assessment",
        ):
            with self.subTest(kind=kind):
                spec = TypeAdapter(m.Spec).validate_python(fixture(kind))
                template = self.template(spec)
                summary, market = self.service.plan_outline(spec, template).chapters
                self.assertEqual([summary.order, market.order], [0, 1])
                self.assertEqual(summary.depends_on, [market.chapter_id])
                self.assertEqual(summary.evidence_requirements, self.context.task.evidence_requirements)
                self.assertTrue(market.required)
                summary.research_questions.append("另一问题")
                self.assertNotIn("另一问题", template.sections[0].research_questions)
                self.assertEqual(template.sections[0].depends_on, ["market"])
        self.writer.complete.assert_not_called()

    def test_invalid_template(self):
        spec = self.context.spec
        template = self.template(spec)
        inputs = (
            {"sections": []},
            {"template_id": "other"},
            {"input_schema": {"required": ["as_of"]}},
            {"sections": [template.sections[0]]},
            {"sections": [template.sections[0], template.sections[0]]},
            {"sections": [
                template.sections[0],
                template.sections[1].model_copy(update={"depends_on": ["summary"]}),
            ]},
        )
        for values in inputs:
            with self.subTest(values=values), self.assertRaises(ServiceError) as error:
                self.service.plan_outline(spec, template.model_copy(update=values))
            self.assertEqual(error.exception.error.code, "INVALID_OUTLINE")

    def test_each_report_type_gets_its_writing_rules_and_specialized_inputs(self):
        cases = (
            ("industry_analysis", "缺年", "industry"),
            ("enterprise_comparison", "研发费用与研发投入", "companies"),
            ("industry_prosperity", "未舍入", "indicator"),
            ("investment_attraction", "地区需求和该企业能力", "target_segment"),
            ("overseas_assessment", "区分发布日期与生效时间", "as_of"),
        )
        for kind, rule, field in cases:
            with self.subTest(kind=kind):
                spec = TypeAdapter(m.Spec).validate_python(fixture(kind))
                context = self.context.model_copy(update={
                    "spec": spec,
                    "template_ref": m.TemplateRef(template_id=spec.template_id, version=1),
                })
                self.service.generate_chapter(context)
                instructions, request = self.writer.complete.call_args.args
                self.assertIn(rule, instructions)
                self.assertEqual(json.loads(request)["spec"][field], fixture(kind)[field])
                self.assertIn("直接回答 task.research_questions", instructions)
                self.assertIn("因果判断须有相应证据", instructions)

    def test_prosperity_prompt_contains_linked_actual_quarter_pairs(self):
        path = FIXTURES.parents[2] / "data/samples/acceptance_metrics.json"
        records = json.loads(path.read_text(encoding="utf-8"))["metrics"]
        context = self.prosperity_context([
            (item["period"], item["value"])
            for item in records if item.get("case_id") == "R03" and item["indicator"] == "nev_sales"
        ])
        rate_ids = [f"yoy:2024-Q{quarter}" for quarter in range(1, 5)]
        text = "四季度销量同比增长 41.31%，高于三季度的 33.39%，增速加快。"
        self.output["summary"] = text
        self.output["blocks"] = [self.output["blocks"][0] | {"text": text, "metric_ids": rate_ids[-2:]}]
        self.output["chart_requests"] = []

        chapter = self.service.generate_chapter(context)
        payload = json.loads(self.writer.complete.call_args.args[1])
        self.writer.complete.assert_called_once()
        analysis = payload["quarterly_analysis"]
        self.assertEqual(analysis["year_over_year"][0], {
            "period": "2024-Q1", "baseline_period": "2023-Q1",
            "current_metric_id": "2024-Q1", "baseline_metric_id": "2023-Q1",
            "yoy_metric_id": "yoy:2024-Q1",
        })
        self.assertEqual([item["yoy_metric_id"] for item in analysis["year_over_year"]], rate_ids)
        self.assertEqual(
            [item["direction"] for item in analysis["adjacent_yoy_changes"]], ["加快"] * 3,
        )
        self.assertEqual(chapter.blocks[0].metric_ids, rate_ids[-2:])
        self.assertEqual(QualityService().validate_chapter(chapter, context.evidences, context.metrics).issues, [])

    def test_prosperity_analysis_uses_unrounded_rates_without_bridging_missing_quarters(self):
        context = self.prosperity_context([
            ("2022-Q4", "90"), ("2023-Q4", "100"),
            ("2023-Q1", "100"), ("2023-Q2", "100"), ("2023-Q3", "100"),
            ("2024-Q1", "110.004"), ("2024-Q2", "110.003"), ("2024-Q3", "110.003"),
            ("2025-Q1", "150"),
        ], end_year="2025")
        analysis = quarterly_analysis(context.spec, context.metrics)
        by_id = {metric.metric_id: metric for metric in context.metrics}
        self.assertEqual(format_percent(by_id["yoy:2024-Q1"]), "10.00%")
        self.assertEqual(format_percent(by_id["yoy:2024-Q2"]), "10.00%")
        self.assertEqual(
            [item["period"] for item in analysis["year_over_year"]],
            ["2024-Q1", "2024-Q2", "2024-Q3", "2025-Q1"],
        )
        self.assertEqual(
            [(item["later_period"], item["direction"]) for item in analysis["adjacent_yoy_changes"]],
            [("2024-Q2", "放缓"), ("2024-Q3", "持平")],
        )

        # Conflicting observations for the same period remove that comparison.
        context.metrics.append(by_id["2024-Q2"].model_copy(update={"metric_id": "duplicate-q2"}))
        analysis = quarterly_analysis(context.spec, context.metrics)
        self.assertNotIn("2024-Q2", [item["period"] for item in analysis["year_over_year"]])
        self.assertEqual(analysis["adjacent_yoy_changes"], [])

    def test_chapter_to_quality_and_synthesis(self):
        chapter = self.service.generate_chapter(self.context)
        check_generation(self.context, chapter)
        quality = QualityService()
        self.assertEqual(quality.validate_chapter(
            chapter, self.context.evidences, self.context.metrics,
        ).issues, [])
        charts = quality.build_charts(chapter.chapter_id, self.context.metrics, chapter.chart_requests)
        self.assertEqual(len(charts.charts), 1)
        self.assertEqual(chapter.blocks[1].chart_id, charts.charts[0].chart_id)

        summary = summarize_chapter(chapter)
        self.assertEqual(summary.evidence_ids, ["ev1"])
        self.assertEqual(summary.metric_ids, ["m1"])
        task = self.context.task.model_copy(update={
            "chapter_id": "summary", "title": "摘要", "depends_on": [chapter.chapter_id],
        })
        self.output.update(chapter_id=task.chapter_id, title=task.title)
        synthesis_context = self.context.model_copy(update={
            "task": task, "predecessors": [summary],
        })
        synthesis = self.service.generate_synthesis(synthesis_context)
        self.assertNotEqual(chapter.chart_requests[0].chart_id, synthesis.chart_requests[0].chart_id)
        instructions, request = self.writer.complete.call_args.args
        self.assertIn("综合依赖章摘要", instructions)
        sent = json.loads(request)
        self.assertEqual(sent["predecessors"][0]["evidence_ids"], ["ev1"])
        self.assertNotIn("source_url", sent["evidences"][0])
        self.assertEqual(sent["spec"]["period"]["from"], "2023")

        # A rewrite reruns only the target task against the supplied frozen context.
        self.output["summary"] = "重写后的摘要。"
        rewrite = self.service.generate_chapter(synthesis_context)
        self.assertEqual(rewrite.summary, "重写后的摘要。")
        self.assertEqual(rewrite.chart_requests[0].chart_id, synthesis.chart_requests[0].chart_id)
        self.assertEqual(chapter.summary, "模拟销量为 120 辆。")

    def test_missing_predecessor_or_out_of_scope_source_stops_model_call(self):
        task = self.context.task.model_copy(update={"depends_on": ["base"]})
        for updates, code in (
            ({"task": task}, "DEPENDENCY_NOT_READY"),
            ({"source_version_ids": []}, "INVALID_CHAPTER_INPUT"),
        ):
            with self.subTest(code=code), self.assertRaises(ServiceError) as error:
                self.service.generate_synthesis(self.context.model_copy(update=updates))
            self.assertEqual(error.exception.error.code, code)
            self.assertFalse(error.exception.error.retryable)
        self.writer.complete.assert_not_called()

    def test_no_evidence_returns_draft_and_quality_blocks_required_chapter(self):
        self.context.evidences.clear()
        self.context.metrics.clear()
        chapter = self.service.generate_chapter(self.context)
        self.writer.complete.assert_not_called()
        self.assertFalse(chapter.blocks[0].evidence_ids)
        self.assertTrue(all(gap.required for gap in chapter.missing_evidence))
        quality = QualityService().validate_chapter(chapter, [], [])
        self.assertTrue(any(issue.severity == "BLOCK" for issue in quality.issues))

        self.context.task.required = False
        optional = self.service.generate_chapter(self.context)
        self.assertTrue(all(not gap.required for gap in optional.missing_evidence))

    def test_preserves_authoritative_gaps(self):
        gap = m.Gap(question="范围", code="MISSING", reason="缺少口径说明", required=True)
        self.context.gaps.append(gap)
        self.output["missing_evidence"] = [gap.model_dump()]
        self.output["blocks"].pop()
        self.output["chart_requests"].clear()
        self.context.metrics[0].value = None
        chapter = self.service.generate_chapter(self.context)
        self.assertEqual(chapter.missing_evidence[0], gap)
        self.assertEqual([item.code for item in chapter.missing_evidence], ["MISSING", "MISSING_REQUIRED_METRIC"])
        self.assertEqual(self.context.gaps, [gap])

    def test_metric_references_carry_their_source_evidence(self):
        self.output["blocks"][0]["evidence_ids"] = []
        result = self.service.generate_chapter(self.context)
        self.assertEqual(result.blocks[0].evidence_ids, ["ev1"])
        self.assertEqual(summarize_chapter(result).evidence_ids, ["ev1"])

    def test_invalid_outputs_are_never_returned_as_chapters(self):
        outputs = [
            ("invalid JSON", "INVALID_MODEL_OUTPUT"),
            (json.dumps(self.output | {"chapter_id": "wrong"}), "INVALID_MODEL_OUTPUT"),
            (json.dumps(self.output | {"summary": "https://example.invalid"}), "INVALID_MODEL_OUTPUT"),
            (json.dumps(self.output | {"chart_requests": []}), "INVALID_MODEL_OUTPUT"),
            (json.dumps(self.output | {"blocks": [
                self.output["blocks"][0] | {"evidence_ids": ["fake"]},
            ]}), "INVALID_EVIDENCE_REF"),
            (json.dumps(self.output | {"fact_candidates": [
                {"text": "无引用事实", "evidence_ids": [], "metric_ids": []},
            ]}), "INVALID_MODEL_OUTPUT"),
        ]
        self.writer.complete.side_effect = None
        for raw, code in outputs:
            with self.subTest(output=raw), self.assertRaises(ServiceError) as error:
                self.writer.complete.return_value = raw
                self.service.generate_chapter(self.context)
            self.assertEqual(error.exception.error.code, code)
            self.assertTrue(error.exception.error.retryable)
        self.assertEqual(self.writer.complete.call_count, len(outputs))


class ChatCompletionsTests(unittest.TestCase):
    def test_single_request_wire_format_and_rate_limit(self):
        calls = []
        response_content = json.dumps(fixture("chapter"), ensure_ascii=False)

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                size = int(self.headers["Content-Length"])
                payload = json.loads(self.rfile.read(size))
                calls.append((self.path, self.headers["Authorization"], payload))
                if len(calls) > 1:
                    self.send_error(429)
                    return
                data = json.dumps({
                    "choices": [{"message": {"content": response_content}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 123, "completion_tokens": 45},
                }).encode("utf-8")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}/v1"
                client = ChatCompletionsClient(base_url, "mock-model", "mock-key")
                context = m.ChapterInput.model_validate(fixture("chapter_input"))
                service = GenerationService(client)
                with self.assertLogs("backend.app.generation.client", "INFO") as log:
                    chapter = service.generate_chapter(context)
                self.assertEqual(chapter.summary, fixture("chapter")["summary"])
                self.assertIn("completion_tokens=45", log.output[0])
                with self.assertRaises(ServiceError) as error:
                    service.generate_chapter(context)
                self.assertEqual(error.exception.error.code, "MODEL_RATE_LIMIT")
                self.assertTrue(error.exception.error.retryable)
            finally:
                server.shutdown()
                thread.join()

        self.assertEqual(len(calls), 2)
        path, authorization, payload = calls[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(authorization, "Bearer mock-key")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual([message["role"] for message in payload["messages"]], ["system", "user"])
