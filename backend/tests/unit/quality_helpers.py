"""Builders shared by the quality unit tests."""
from __future__ import annotations

from backend.app.contracts import models as m


def metric(
    metric_id: str = "m1",
    *,
    indicator: str = "sales",
    name: str = "模拟销量",
    entity_id: str | None = None,
    period: str = "2024",
    granularity: str = "year",
    region: str = "中国",
    scope: str = "同一统计口径",
    value: str | None = "120",
    raw_value: str | None = None,
    unit: str = "辆",
    currency: str | None = None,
    source_evidence_ids: list[str] | None = None,
    formula_id: str | None = None,
    input_metric_ids: list[str] | None = None,
    missing_reason: str | None = None,
) -> m.Metric:
    if raw_value is None:
        raw_value = value
    return m.Metric(
        metric_id=metric_id,
        indicator=indicator,
        name=name,
        entity_id=entity_id,
        period=period,
        granularity=granularity,
        region=region,
        scope=scope,
        raw_value=raw_value,
        raw_unit=unit,
        multiplier="1",
        value=value,
        unit=unit,
        currency=currency,
        source_evidence_ids=source_evidence_ids or ["ev1"],
        formula_id=formula_id,
        input_metric_ids=input_metric_ids or [],
        missing_reason=missing_reason,
    )


def task(
    *,
    chapter_id: str = "ch_market",
    required_metrics: list[str] | None = None,
    dependencies: list[str] | None = None,
) -> m.Task:
    return m.Task(
        chapter_id=chapter_id,
        section_key="market",
        title="市场规模",
        order=0,
        required=True,
        depends_on=dependencies or [],
        research_questions=["销量是多少？"],
        evidence_requirements=["销量原表"],
        required_metrics=required_metrics or ["sales"],
        desired_chart="bar",
    )


def evidence(evidence_id: str = "ev1", document_version_id: str = "dv1") -> m.Evidence:
    return m.Evidence(
        title="模拟销量表",
        publisher="测试发布方",
        published_at="2024-12-31",
        source_url=None,
        evidence_id=evidence_id,
        document_id="doc1",
        document_version_id=document_version_id,
        chunk_id=f"chunk_{evidence_id}",
        locator=m.Sheet(kind="xlsx", sheet="销量", cells="B2"),
        excerpt="模拟销量 120 辆",
        score=0.9,
    )


def paragraph(
    block_id: str = "b1",
    text: str = "模拟销量为 120 辆。",
    *,
    evidence_ids: list[str] | None = None,
    metric_ids: list[str] | None = None,
) -> m.Paragraph:
    return m.Paragraph(
        type="paragraph",
        block_id=block_id,
        text=text,
        evidence_ids=evidence_ids if evidence_ids is not None else ["ev1"],
        metric_ids=metric_ids if metric_ids is not None else ["m1"],
    )


def chapter(
    *,
    chapter_id: str = "ch_market",
    blocks: list[m.Block] | None = None,
    chart_requests: list[m.ChartRequest] | None = None,
) -> m.Chapter:
    return m.Chapter(
        chapter_id=chapter_id,
        title="市场规模",
        blocks=blocks or [paragraph()],
        summary="模拟销量为 120 辆。",
        fact_candidates=[],
        chart_requests=chart_requests or [],
        missing_evidence=[],
    )
