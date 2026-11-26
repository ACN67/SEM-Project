"""Render chart data to real PNG bytes."""
from __future__ import annotations

import hashlib
from io import BytesIO
import unittest

from PIL import Image

from backend.app.contracts import models as m
from backend.app.quality import ChartRenderError, render_chart_png
from backend.app.quality.render import _series_and_labels


def chart(chart_type: str, *, data: list[m.Point] | None = None) -> m.Chart:
    return m.Chart(
        chapter_id="ch_market",
        chart_id="chart1",
        block_id="b_chart",
        title="中国新能源汽车销量趋势",
        type=chart_type,
        source_metric_ids=["m1", "m2", "m3"],
        data=data
        or [
            m.Point(label="2022", series="全国销量", value="688.7", metric_id="m1"),
            m.Point(label="2023", series="全国销量", value="949.5", metric_id="m2"),
            m.Point(label="2024", series="全国销量", value="1286.6", metric_id="m3"),
        ],
        unit="万辆",
        asset_id="chart_fixture",
    )


class ChartRenderTests(unittest.TestCase):
    def test_line_bar_and_table_produce_png_with_expected_size(self):
        for chart_type in ("line", "bar", "table"):
            with self.subTest(chart_type=chart_type):
                content = render_chart_png(chart(chart_type))
                self.assertTrue(content.startswith(b"\x89PNG\r\n\x1a\n"))
                with Image.open(BytesIO(content)) as image:
                    self.assertEqual(image.format, "PNG")
                    self.assertEqual(image.size, (1200, 720))
                    self.assertEqual(image.mode, "RGB")

    def test_rendering_is_deterministic_and_preserves_missing_gap(self):
        first = render_chart_png(
            chart(
                "line",
                data=[
                    m.Point(label="2022", series="全国销量", value="688.7", metric_id="m1"),
                    m.Point(label="2023", series="全国销量", value=None, metric_id="m2"),
                    m.Point(label="2024", series="全国销量", value="1286.6", metric_id="m3"),
                ],
            )
        )
        second = render_chart_png(
            chart(
                "line",
                data=[
                    m.Point(label="2022", series="全国销量", value="688.7", metric_id="m1"),
                    m.Point(label="2023", series="全国销量", value=None, metric_id="m2"),
                    m.Point(label="2024", series="全国销量", value="1286.6", metric_id="m3"),
                ],
            )
        )
        self.assertEqual(hashlib.sha256(first).digest(), hashlib.sha256(second).digest())

    def test_global_period_order_and_duplicate_coordinates(self):
        points = [
            m.Point(label="2024", series="A", value="3", metric_id="a"),
            m.Point(label="2022", series="B", value="2", metric_id="b"),
            m.Point(label="2024", series="B", value="1", metric_id="c"),
        ]
        _, labels, values = _series_and_labels(points)
        self.assertEqual(labels, ["2022", "2024"])
        self.assertNotIn(("A", "2022"), values)
        points.append(m.Point(label="2024", series="B", value="9", metric_id="d"))
        with self.assertRaises(ChartRenderError) as error:
            render_chart_png(chart("line", data=points))
        self.assertEqual(error.exception.code, "CHART_DATA_CONFLICT")
        self.assertIn("c, d", str(error.exception))

    def test_signed_bars_share_zero_baseline(self):
        points = [
            m.Point(label=str(2022+i), series="A", value=str(value), metric_id=str(i))
            for i, value in enumerate((-5, 0, 5))
        ]
        image = Image.open(BytesIO(render_chart_png(chart("bar", data=points))))
        blue = (47, 107, 255)
        self.assertEqual(image.getpixel((272, 500)), blue)
        self.assertEqual(image.getpixel((958, 250)), blue)
        self.assertNotEqual(image.getpixel((615, 385)), blue)

    def test_mixed_calendar_periods_sort_chronologically(self):
        periods = ["2024-12", "2024-Q1", "2023-Q4", "2024-01", "2024-Q2", "2024-05"]
        points = [m.Point(label=label, series="A", value="1", metric_id=str(index))
                  for index, label in enumerate(periods)]
        self.assertEqual(_series_and_labels(points)[1],
                         ["2023-Q4", "2024-01", "2024-Q1", "2024-05", "2024-Q2", "2024-12"])
        for kind in ("line", "bar"):
            self.assertTrue(render_chart_png(chart(kind, data=points)).startswith(b"\x89PNG"))

    def test_dense_monthly_bars(self):
        points = [m.Point(label=f"{2000+i//12}-{i%12+1:02}", series="S", value="1", metric_id=str(i))
                  for i in range(120)]
        self.assertTrue(render_chart_png(chart("bar", data=points)).startswith(b"\x89PNG"))

    def test_invalid_size_and_missing_data_are_explicit_errors(self):
        with self.assertRaises(ChartRenderError) as error:
            render_chart_png(chart("line"), width=320, height=240)
        self.assertEqual(error.exception.code, "CHART_SIZE_INVALID")

        empty = chart("table").model_copy(update={"data": []})
        with self.assertRaises(ChartRenderError) as error:
            render_chart_png(empty)
        self.assertEqual(error.exception.code, "CHART_DATA_INSUFFICIENT")


if __name__ == "__main__":
    unittest.main()
