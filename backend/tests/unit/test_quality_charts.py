"""Unit tests for chart-ready data construction."""
from __future__ import annotations

import unittest

from backend.app.contracts import models as m
from backend.app.quality import QualityService
from quality_helpers import metric


def request(
    chart_id: str = "chart1",
    *,
    block_id: str = "b2",
    chart_type: str = "line",
    metric_ids: list[str] | None = None,
) -> m.ChartRequest:
    return m.ChartRequest(
        chart_id=chart_id,
        block_id=block_id,
        title="销量趋势",
        type=chart_type,
        source_metric_ids=metric_ids or ["m1", "m2", "m3"],
    )


class ChartTests(unittest.TestCase):
    def setUp(self):
        self.service = QualityService()
        self.metrics = [
            metric("m1", period="2022", value="100"),
            metric("m2", period="2023", value="120"),
            metric("m3", period="2024", value=None, missing_reason="未披露"),
        ]

    def test_line_chart_preserves_null_gap_and_has_stable_asset_id(self):
        first = self.service.build_charts("ch1", self.metrics, [request()])
        second = self.service.build_charts("ch1", list(reversed(self.metrics)), [request()])

        self.assertEqual(first.issues, [])
        self.assertEqual(len(first.charts), 1)
        self.assertEqual(
            [(point.label, point.value) for point in first.charts[0].data],
            [("2022", "100"), ("2023", "120"), ("2024", None)],
        )
        self.assertEqual(first.charts[0].asset_id, second.charts[0].asset_id)
        self.assertTrue(first.charts[0].asset_id.startswith("chart_"))

    def test_bar_table_and_zero_are_supported(self):
        metrics = [
            metric("m1", period="2023", value="0"),
            metric("m2", period="2024", value="120"),
        ]
        for chart_type in ("bar", "table"):
            with self.subTest(chart_type=chart_type):
                result = self.service.build_charts(
                    "ch1",
                    metrics,
                    [request(chart_type=chart_type, metric_ids=["m1", "m2"])],
                )
                self.assertEqual(result.issues, [])
                self.assertEqual(result.charts[0].type, chart_type)
                self.assertEqual(result.charts[0].data[0].value, "0")

    def test_missing_metric_is_a_warning_without_chart(self):
        result = self.service.build_charts("ch1", self.metrics, [request(metric_ids=["m1", "missing"])])
        self.assertEqual(result.charts, [])
        self.assertEqual(result.issues[0].severity, "WARN")
        self.assertEqual(result.issues[0].code, "CHART_DATA_INSUFFICIENT")

    def test_mixed_units_are_not_combined(self):
        metrics = [
            metric("m1", period="2023", value="100", unit="辆"),
            metric("m2", period="2024", value="1.2", unit="万辆"),
        ]
        result = self.service.build_charts(
            "ch1",
            metrics,
            [request(metric_ids=["m1", "m2"])],
        )
        self.assertEqual(result.charts, [])
        self.assertEqual(result.issues[0].code, "CHART_UNIT_MISMATCH")

    def test_no_numeric_values_is_insufficient(self):
        metrics = [
            metric("m1", value=None),
            metric("m2", period="2023", value=None),
        ]
        result = self.service.build_charts(
            "ch1",
            metrics,
            [request(metric_ids=["m1", "m2"])],
        )
        self.assertEqual(result.charts, [])
        self.assertEqual(result.issues[0].code, "CHART_DATA_INSUFFICIENT")


if __name__ == "__main__":
    unittest.main()
