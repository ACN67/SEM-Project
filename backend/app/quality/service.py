"""Concrete implementation of the public Quality service boundary."""
from __future__ import annotations

from ..contracts import models as m
from ..contracts.services import Calculation, ChartResult
from .charts import build_charts
from .metrics import calculate_metrics
from .validation import validate_chapter, validate_report


class QualityService:
    def calculate_metrics(
        self,
        raw_metrics: list[m.Metric],
        requirements: m.Task,
    ) -> Calculation:
        return calculate_metrics(raw_metrics, requirements)

    def build_charts(
        self,
        chapter_id: str,
        metrics: list[m.Metric],
        requests: list[m.ChartRequest],
    ) -> ChartResult:
        return build_charts(chapter_id, metrics, requests)

    def validate_chapter(
        self,
        chapter: m.Chapter,
        evidences: list[m.Evidence],
        metrics: list[m.Metric],
    ) -> m.QualitySummary:
        return validate_chapter(chapter, evidences, metrics)

    def validate_report(self, document: m.ReportDocument) -> m.QualitySummary:
        return validate_report(document)
