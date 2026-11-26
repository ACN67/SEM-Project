"""Exercise the W5 fixture Knowledge through generation and quality."""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import json
import unittest
from unittest.mock import Mock

from PIL import Image

from backend.app.contracts import models as m
from backend.app.contracts.services import ServiceError
from backend.app.contracts.validation import check_snapshot
from backend.app.generation import GenerationService
from backend.app.quality import QualityService, render_chart_png
from w5_fixture_knowledge import W5FixtureKnowledge


class W5FixtureKnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.knowledge = W5FixtureKnowledge()

    def test_retrieve_and_get_metrics_are_scoped_by_case_versions(self):
        versions = self.knowledge.source_version_ids("R01")
        filters = m.Filters(as_of=self.knowledge.cases["R01"]["request"]["as_of"])
        evidences = self.knowledge.retrieve("R01", filters, 50, versions)
        metrics = self.knowledge.get_metrics(filters, versions)

        self.assertEqual(len(evidences), 4)
        self.assertEqual(
            {item.metric_id for item in metrics},
            {
                "r01-cn-nev-sales-2022",
                "r01-cn-nev-sales-2023",
                "r01-cn-nev-sales-2024",
            },
        )
        self.assertTrue(all(item.indicator == "sales" for item in metrics))
        self.assertTrue(all(item.source_evidence_ids for item in metrics))
        self.assertTrue(
            any(isinstance(item.locator, m.Word) for item in evidences)
        )
        web = next(item for item in evidences if isinstance(item.locator, m.Word))
        self.assertIsNotNone(web.source_url)
        self.assertEqual(web.locator.section_path[0], "网页快照")

    def test_filters_unknown_versions_and_ingest_are_explicit(self):
        filters = m.Filters(indicators=["rd_investment"])
        versions = self.knowledge.source_version_ids("R02")
        metrics = self.knowledge.get_metrics(filters, versions)
        self.assertEqual(len(metrics), 4)
        self.assertTrue(all(item.indicator == "rd_investment" for item in metrics))

        self.assertEqual(self.knowledge.retrieve("R01", filters, 10, []), [])
        self.assertEqual(self.knowledge.get_metrics(filters, []), [])
        with self.assertRaises(ServiceError) as error:
            self.knowledge.ingest_document(
                __file__,
                m.Metadata(
                    title="fixture",
                    publisher="fixture",
                    published_at="2026-11-26",
                ),
            )
        self.assertEqual(error.exception.error.code, "FIXTURE_READ_ONLY")


class W5EndToEndFixtureTests(unittest.TestCase):
    def setUp(self):
        self.knowledge = W5FixtureKnowledge()
        self.context = self.knowledge.case_context("R01")
        self.quality = QualityService()

    def _model_output(self) -> str:
        raw_ids = [
            "r01-cn-nev-sales-2022",
            "r01-cn-nev-sales-2023",
            "r01-cn-nev-sales-2024",
        ]
        yoy_id = "yoy:r01-cn-nev-sales-2024"
        metric = next(item for item in self.context.metrics if item.metric_id == yoy_id)
        return json.dumps(
            {
                "chapter_id": self.context.task.chapter_id,
                "title": self.context.task.title,
                "blocks": [
                    {
                        "type": "paragraph",
                        "block_id": "b_text",
                        "text": "2024年中国新能源汽车销量同比增长35.50%。",
                        "evidence_ids": metric.source_evidence_ids,
                        "metric_ids": [yoy_id],
                    },
                    {
                        "type": "chart_ref",
                        "block_id": "b_chart",
                        "chart_id": "placeholder",
                    },
                ],
                "summary": "中国新能源汽车销量持续增长。",
                "fact_candidates": [],
                "chart_requests": [
                    {
                        "chart_id": "placeholder",
                        "block_id": "b_chart",
                        "title": "中国新能源汽车销量趋势",
                        "type": "line",
                        "source_metric_ids": raw_ids,
                    }
                ],
                "missing_evidence": [],
            },
            ensure_ascii=False,
        )

    def test_metrics_generation_quality_chart_and_report(self):
        writer = Mock()
        writer.complete.return_value = self._model_output()
        chapter = GenerationService(writer).generate_chapter(self.context)

        chapter_quality = self.quality.validate_chapter(
            chapter,
            self.context.evidences,
            self.context.metrics,
        )
        self.assertEqual(chapter_quality.issues, [])

        charts = self.quality.build_charts(
            chapter.chapter_id,
            self.context.metrics,
            chapter.chart_requests,
        )
        self.assertEqual(charts.issues, [])
        self.assertEqual(len(charts.charts), 1)
        chart = charts.charts[0]
        png = render_chart_png(chart)
        with Image.open(BytesIO(png)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (1200, 720))

        initial_quality = m.QualitySummary(rules=[], issues=[])
        document = m.ReportDocument(
            run_id=self.context.run_id,
            spec=self.context.spec,
            template_ref=self.context.template_ref,
            source_version_ids=self.context.source_version_ids,
            facts_version=self.context.facts_version,
            facts=[],
            version_id="v_w5_fixture",
            parent_version_id=None,
            created_at=datetime(2026, 11, 26, tzinfo=timezone.utc),
            stale_chapter_ids=[],
            exportable=False,
            report_id="report_w5_fixture",
            schema_version="1.0",
            layout_version="1",
            model_id="fixture",
            outline=m.Outline(chapters=[self.context.task]),
            chapters=[chapter],
            evidences=self.context.evidences,
            metrics=self.context.metrics,
            charts=charts.charts,
            quality=initial_quality,
        )
        report_quality = self.quality.validate_report(document)
        self.assertEqual(report_quality.issues, [])
        document.quality = report_quality
        document.exportable = True
        check_snapshot(document)


if __name__ == "__main__":
    unittest.main()
