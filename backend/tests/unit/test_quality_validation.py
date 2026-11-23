"""Unit tests for chapter and report quality validation."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from backend.app.contracts import models as m
from backend.app.contracts.validation import check_snapshot
from backend.app.quality import QualityService
from quality_helpers import chapter, evidence, metric, paragraph

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "shared/contracts/fixtures"


def load_document() -> m.ReportDocument:
    return m.ReportDocument.model_validate(
        json.loads((FIXTURES / "report_document.json").read_text(encoding="utf-8"))
    )


class ChapterValidationTests(unittest.TestCase):
    def setUp(self):
        self.service = QualityService()

    def test_valid_chapter_passes_all_rules(self):
        summary = self.service.validate_chapter(
            chapter(),
            [evidence()],
            [metric()],
        )
        self.assertEqual(summary.issues, [])
        self.assertEqual(
            [rule.result for rule in summary.rules],
            ["PASS", "PASS", "PASS", "PASS"],
        )

    def test_dangling_evidence_and_metric_are_blocked(self):
        bad = chapter(
            blocks=[
                paragraph(
                    evidence_ids=["fake_ev"],
                    metric_ids=["fake_metric"],
                )
            ]
        )
        summary = self.service.validate_chapter(bad, [evidence()], [metric()])
        self.assertEqual(
            {issue.code for issue in summary.issues},
            {"INVALID_EVIDENCE_REF", "INVALID_METRIC_REF"},
        )
        self.assertTrue(all(issue.severity == "BLOCK" for issue in summary.issues))

    def test_duplicate_block_id_is_blocked(self):
        bad = chapter(
            blocks=[
                paragraph("b1"),
                paragraph("b1", text="第二段"),
            ]
        )
        summary = self.service.validate_chapter(bad, [evidence()], [metric()])
        self.assertIn("DUPLICATE_BLOCK_ID", {issue.code for issue in summary.issues})

    def test_table_width_is_checked(self):
        table = m.Table(
            type="table",
            block_id="table1",
            columns=["年份", "销量"],
            rows=[
                [
                    m.Cell(text="2024", evidence_ids=["ev1"], metric_ids=[]),
                ]
            ],
        )
        summary = self.service.validate_chapter(
            chapter(blocks=[table]),
            [evidence()],
            [metric()],
        )
        self.assertIn("TABLE_WIDTH_MISMATCH", {issue.code for issue in summary.issues})

    def test_chart_target_and_request_must_match(self):
        bad = chapter(
            blocks=[
                paragraph(),
                m.ChartRef(type="chart_ref", block_id="b2", chart_id="chart1"),
            ],
            chart_requests=[
                m.ChartRequest(
                    block_id="b3",
                    chart_id="chart1",
                    title="销量",
                    type="bar",
                    source_metric_ids=["m1"],
                )
            ],
        )
        summary = self.service.validate_chapter(bad, [evidence()], [metric()])
        codes = {issue.code for issue in summary.issues}
        self.assertIn("INVALID_CHART_TARGET", codes)
        self.assertIn("MISSING_CHART_REQUEST", codes)

    def test_wrong_linked_percentage_and_year_are_blocked(self):
        yoy = metric(
            "yoy:m1",
            value="31.78",
            unit="%",
            formula_id="yoy_pct",
            input_metric_ids=["base", "m1"],
        )
        bad_percentage = chapter(
            blocks=[
                paragraph(
                    text="2023 年同比增长 41.31%。",
                    metric_ids=["yoy:m1"],
                )
            ]
        )
        summary = self.service.validate_chapter(
            bad_percentage,
            [evidence()],
            [yoy],
        )
        codes = {issue.code for issue in summary.issues}
        self.assertIn("METRIC_VALUE_MISMATCH", codes)
        self.assertIn("METRIC_PERIOD_MISMATCH", codes)

    def test_direct_value_and_unit_mismatch_in_table_are_blocked(self):
        table = m.Table(
            type="table",
            block_id="table1",
            columns=["销量"],
            rows=[
                [
                    m.Cell(
                        text="999 亿元",
                        evidence_ids=["ev1"],
                        metric_ids=["m1"],
                    )
                ]
            ],
        )
        summary = self.service.validate_chapter(
            chapter(blocks=[table]),
            [evidence()],
            [metric(value="120", unit="辆")],
        )
        self.assertIn("METRIC_UNIT_MISMATCH", {issue.code for issue in summary.issues})

    def test_unlinked_number_is_warning_not_block(self):
        warning = chapter(
            blocks=[
                paragraph(),
                paragraph(
                    "b2",
                    "另有约 999 辆仍需核实。",
                    evidence_ids=[],
                    metric_ids=[],
                ),
            ]
        )
        summary = self.service.validate_chapter(warning, [evidence()], [metric()])
        unverified = [issue for issue in summary.issues if issue.code == "UNVERIFIED_NUMBER"]
        self.assertEqual(len(unverified), 1)
        self.assertEqual(unverified[0].severity, "WARN")


class ReportValidationTests(unittest.TestCase):
    def setUp(self):
        self.service = QualityService()

    def test_valid_fixture_passes_report_rules(self):
        document = load_document()
        summary = self.service.validate_report(document)
        self.assertEqual(summary.issues, [])
        self.assertEqual(
            [rule.rule_id for rule in summary.rules],
            ["structure", "sources", "metrics", "charts", "dependencies", "freshness"],
        )
        self.assertTrue(all(rule.required for rule in summary.rules))

    def test_missing_required_chapter_is_blocked(self):
        document = load_document()
        document.chapters = []
        summary = self.service.validate_report(document)
        self.assertIn(
            "MISSING_REQUIRED_CHAPTER",
            {issue.code for issue in summary.issues},
        )

    def test_dependency_cycle_is_blocked(self):
        document = load_document()
        document.outline.chapters[0].depends_on = ["ch_market"]
        summary = self.service.validate_report(document)
        self.assertIn("CYCLIC_DEPENDENCY", {issue.code for issue in summary.issues})

    def test_stale_chapter_is_blocked(self):
        document = load_document()
        document.stale_chapter_ids = ["ch_market"]
        summary = self.service.validate_report(document)
        self.assertIn("STALE_CHAPTER", {issue.code for issue in summary.issues})

    def test_dangling_metric_source_is_blocked(self):
        document = load_document()
        document.metrics[0].source_evidence_ids = ["missing_evidence"]
        summary = self.service.validate_report(document)
        self.assertIn("INVALID_EVIDENCE_REF", {issue.code for issue in summary.issues})

    def test_chart_source_without_data_point_is_a_warning(self):
        document = load_document()
        document.charts[0].data = []
        summary = self.service.validate_report(document)
        chart_warning = [
            issue for issue in summary.issues if issue.code == "CHART_DATA_INSUFFICIENT"
        ]
        self.assertEqual(len(chart_warning), 1)
        self.assertEqual(chart_warning[0].severity, "WARN")

    def test_warning_only_report_remains_exportable(self):
        document = load_document()
        document.chapters[0].blocks.append(
            m.Paragraph(
                type="paragraph",
                block_id="warning",
                text="另有约 999 辆仍需核实。",
                evidence_ids=[],
                metric_ids=[],
            )
        )
        summary = self.service.validate_report(document)
        self.assertIn("UNVERIFIED_NUMBER", {issue.code for issue in summary.issues})
        self.assertFalse(any(issue.severity == "BLOCK" for issue in summary.issues))

        document.quality = summary
        document.exportable = True
        check_snapshot(document)

    def test_blocked_report_is_not_exportable(self):
        document = load_document()
        document.outline.chapters.append(
            m.Task(
                chapter_id="ch_missing",
                section_key="missing",
                title="缺失章",
                order=1,
                required=True,
                depends_on=[],
                research_questions=[],
                evidence_requirements=[],
                required_metrics=[],
                desired_chart=None,
            )
        )
        summary = self.service.validate_report(document)
        self.assertTrue(any(issue.severity == "BLOCK" for issue in summary.issues))
        document.quality = summary
        document.exportable = not any(
            issue.severity == "BLOCK" for issue in summary.issues
        )
        self.assertFalse(document.exportable)
        check_snapshot(document)


if __name__ == "__main__":
    unittest.main()
