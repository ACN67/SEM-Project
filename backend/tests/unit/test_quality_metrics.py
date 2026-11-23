"""Unit tests for deterministic metric calculation."""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import unittest

from backend.app.contracts import models as m
from backend.app.quality import FORMULA_YOY_PCT, calculate_metrics, format_percent
from quality_helpers import metric, task

ROOT = Path(__file__).resolve().parents[3]


class MetricCalculationTests(unittest.TestCase):
    def test_growth_preserves_inputs_and_tracks_sources(self):
        base = metric("base", period="2023", value="100", source_evidence_ids=["base_ev"])
        current = metric("current", value="120", source_evidence_ids=["current_ev"])
        result = calculate_metrics([base, current], task())

        self.assertEqual([item.metric_id for item in result.metrics], ["base", "current", "yoy:current"])
        derived = result.metrics[-1]
        self.assertEqual(derived.formula_id, FORMULA_YOY_PCT)
        self.assertEqual(derived.input_metric_ids, ["base", "current"])
        self.assertEqual(derived.source_evidence_ids, ["base_ev", "current_ev"])
        self.assertEqual(derived.value, "20")
        self.assertEqual(format_percent(Decimal(derived.value)), "20.00%")
        self.assertEqual(result.gaps, [])

    def test_decline_and_actual_zero_are_calculable(self):
        result = calculate_metrics(
            [metric("base", period="2023", value="100"), metric("current", value="80")],
            task(),
        )
        self.assertEqual(result.metrics[-1].value, "-20")

        result = calculate_metrics(
            [metric("base", period="2023", value="100"), metric("current", value="0")],
            task(),
        )
        self.assertEqual(result.metrics[-1].value, "-100")

    def test_zero_base_is_a_gap(self):
        result = calculate_metrics(
            [metric("base", period="2023", value="0"), metric("current", value="20")],
            task(),
        )
        self.assertEqual([gap.code for gap in result.gaps], ["ZERO_BASE"])
        self.assertTrue(result.gaps[0].required)

    def test_missing_base_period_is_reported_for_latest_period(self):
        result = calculate_metrics([metric("current", value="20")], task())
        self.assertIn("MISSING_BASE_PERIOD", {gap.code for gap in result.gaps})

    def test_unit_and_scope_mismatch_are_distinct_gaps(self):
        unit_case = calculate_metrics(
            [
                metric("base", period="2023", value="100", unit="万辆"),
                metric("current", value="120", unit="辆"),
            ],
            task(),
        )
        self.assertIn("UNIT_MISMATCH", {gap.code for gap in unit_case.gaps})

        scope_case = calculate_metrics(
            [
                metric("base", period="2023", value="100", scope="全国"),
                metric("current", value="120", scope="重点企业"),
            ],
            task(),
        )
        self.assertIn("SCOPE_MISMATCH", {gap.code for gap in scope_case.gaps})

    def test_currency_mismatch_and_missing_current_value(self):
        currency_case = calculate_metrics(
            [
                metric(
                    "base",
                    period="2023",
                    value="100",
                    unit="亿元",
                    currency="CNY",
                ),
                metric(
                    "current",
                    value="120",
                    unit="亿元",
                    currency="EUR",
                ),
            ],
            task(),
        )
        self.assertIn("CURRENCY_MISMATCH", {gap.code for gap in currency_case.gaps})

        missing_value_case = calculate_metrics(
            [
                metric("base", period="2023", value="100"),
                metric("current", value=None, raw_value=None),
            ],
            task(),
        )
        self.assertIn(
            "MISSING_CURRENT_VALUE",
            {gap.code for gap in missing_value_case.gaps},
        )

    def test_invalid_period_and_missing_required_metric(self):
        result = calculate_metrics(
            [metric("current", period="2024-Q5", granularity="quarter")],
            task(required_metrics=["sales", "r_and_d"]),
        )
        codes = {gap.code for gap in result.gaps}
        self.assertIn("INVALID_PERIOD", codes)
        self.assertIn("MISSING_REQUIRED_METRIC", codes)

    def test_ambiguous_base_cannot_be_selected(self):
        result = calculate_metrics(
            [
                metric("base1", period="2023", value="100"),
                metric("base2", period="2023", value="110"),
                metric("current", value="120"),
            ],
            task(),
        )
        self.assertIn("AMBIGUOUS_COMPARABLE_METRIC", {gap.code for gap in result.gaps})

    def test_duplicate_current_period_is_a_gap(self):
        result = calculate_metrics(
            [
                metric("base", period="2023", value="100"),
                metric("current1", value="120"),
                metric("current2", value="121"),
            ],
            task(),
        )
        self.assertIn("AMBIGUOUS_CURRENT_PERIOD", {gap.code for gap in result.gaps})

    def test_percent_display_uses_half_up(self):
        self.assertEqual(format_percent(Decimal("1.005")), "1.01%")
        self.assertEqual(format_percent(Decimal("-1.005")), "-1.01%")

    def test_monthly_and_quarterly_previous_periods(self):
        quarter = calculate_metrics(
            [
                metric(
                    "q_base",
                    period="2023-Q2",
                    granularity="quarter",
                    value="200",
                ),
                metric(
                    "q_current",
                    period="2024-Q2",
                    granularity="quarter",
                    value="250",
                ),
            ],
            task(),
        )
        quarter_yoy = next(item for item in quarter.metrics if item.metric_id == "yoy:q_current")
        self.assertEqual(quarter_yoy.value, "25")

        month = calculate_metrics(
            [
                metric("m_base", period="2023-05", granularity="month", value="50"),
                metric("m_current", period="2024-05", granularity="month", value="60"),
            ],
            task(),
        )
        month_yoy = next(item for item in month.metrics if item.metric_id == "yoy:m_current")
        self.assertEqual(month_yoy.value, "20")

    def test_real_r03_quarters_match_manual_calculation(self):
        dataset = json.loads(
            (ROOT / "data/samples/acceptance_metrics.json").read_text(encoding="utf-8")
        )
        records = [
            item
            for item in dataset["metrics"]
            if item.get("case_id") == "R03" and item.get("indicator") == "nev_sales"
        ]
        metrics = [
            m.Metric(
                metric_id=item["record_id"],
                indicator=item["indicator"],
                name=item["name"],
                entity_id=item.get("entity_id"),
                period=item["period"],
                granularity=item["granularity"],
                region=item["region"],
                scope=item["scope"],
                raw_value=item["value"],
                raw_unit=item["unit"],
                multiplier="1",
                value=item["value"],
                unit=item["unit"],
                currency=None,
                source_evidence_ids=sorted(
                    [item["source_id"]]
                    if "source_id" in item
                    else item["source_ids"]
                ),
                formula_id=None,
                input_metric_ids=[],
                missing_reason=None,
            )
            for item in records
        ]
        result = calculate_metrics(metrics, task(required_metrics=["nev_sales"]))
        expected = {
            "2024-Q1": "31.78%",
            "2024-Q2": "32.07%",
            "2024-Q3": "33.39%",
            "2024-Q4": "41.31%",
        }
        actual = {
            item.period: format_percent(Decimal(item.value))
            for item in result.metrics
            if item.formula_id == FORMULA_YOY_PCT
        }
        self.assertEqual(actual, expected)
        self.assertEqual(result.gaps, [])
        q2 = next(
            item
            for item in result.metrics
            if item.metric_id == "yoy:r03-cn-nev-sales-2024-q2"
        )
        self.assertEqual(
            q2.input_metric_ids,
            ["r03-cn-nev-sales-2023-q2", "r03-cn-nev-sales-2024-q2"],
        )
        self.assertEqual(
            q2.source_evidence_ids,
            ["miit-2023-h1-mirror", "miit-2023-q1", "miit-2024-h1", "miit-2024-q1"],
        )


if __name__ == "__main__":
    unittest.main()
