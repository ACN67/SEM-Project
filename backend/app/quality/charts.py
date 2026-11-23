"""Build chart-ready data without asking a model or interpolating values."""
from __future__ import annotations

from collections import Counter

from ..contracts import models as m
from ..contracts.services import ChartResult
from .common import decimal_text, make_issue, metric_decimal, period_sort_key, sort_issues, stable_digest


def chart_asset_id(
    chapter_id: str,
    request: m.ChartRequest,
    data: list[m.Point],
    unit: str,
) -> str:
    payload = {
        "chapter_id": chapter_id,
        "chart_id": request.chart_id,
        "type": request.type,
        "title": request.title,
        "source_metric_ids": request.source_metric_ids,
        "unit": unit,
        "data": [point.model_dump(mode="json") for point in data],
    }
    return f"chart_{stable_digest(payload)[:20]}"


def build_charts(
    chapter_id: str,
    metrics: list[m.Metric],
    requests: list[m.ChartRequest],
) -> ChartResult:
    issues: list[m.Issue] = []
    metric_id_counts = Counter(metric.metric_id for metric in metrics)
    metric_map = {
        metric.metric_id: metric
        for metric in metrics
        if metric_id_counts[metric.metric_id] == 1
    }
    chart_id_counts = Counter(request.chart_id for request in requests)
    block_id_counts = Counter(request.block_id for request in requests)
    charts: list[m.Chart] = []

    name_counts = Counter(metric.name for metric in metric_map.values())
    for request in requests:
        if chart_id_counts[request.chart_id] > 1:
            issues.append(
                make_issue(
                    "BLOCK",
                    "DUPLICATE_CHART_ID",
                    f"图表 ID {request.chart_id} 重复",
                    chapter_id=chapter_id,
                    block_id=request.block_id,
                    related_ids=[request.chart_id],
                )
            )
            continue
        if block_id_counts[request.block_id] > 1:
            issues.append(
                make_issue(
                    "BLOCK",
                    "DUPLICATE_CHART_BLOCK",
                    f"图表块 {request.block_id} 被多个图表重复使用",
                    chapter_id=chapter_id,
                    block_id=request.block_id,
                )
            )
            continue

        missing = [metric_id for metric_id in request.source_metric_ids if metric_id not in metric_map]
        duplicated_metrics = [
            metric_id
            for metric_id in request.source_metric_ids
            if metric_id_counts[metric_id] > 1
        ]
        if missing or duplicated_metrics:
            unresolved = sorted(set(missing + duplicated_metrics))
            issues.append(
                make_issue(
                    "WARN",
                    "CHART_DATA_INSUFFICIENT",
                    "图表引用的指标不唯一或不可解析，未生成图表",
                    chapter_id=chapter_id,
                    block_id=request.block_id,
                    related_ids=unresolved,
                )
            )
            continue

        source_metrics = [metric_map[metric_id] for metric_id in request.source_metric_ids]
        numeric_metrics = [
            metric for metric in source_metrics if metric_decimal(metric) is not None
        ]
        if not numeric_metrics:
            issues.append(
                make_issue(
                    "WARN",
                    "CHART_DATA_INSUFFICIENT",
                    "图表没有可绘制的数值，未生成图表",
                    chapter_id=chapter_id,
                    block_id=request.block_id,
                    related_ids=request.source_metric_ids,
                )
            )
            continue

        units = sorted({metric.unit for metric in source_metrics})
        if len(units) != 1:
            issues.append(
                make_issue(
                    "WARN",
                    "CHART_UNIT_MISMATCH",
                    f"图表源指标单位不一致：{'、'.join(units)}",
                    chapter_id=chapter_id,
                    block_id=request.block_id,
                    related_ids=request.source_metric_ids,
                )
            )
            continue

        points: list[m.Point] = []
        for source_index, metric in enumerate(source_metrics):
            series = metric.name
            if name_counts[metric.name] > 1:
                series = f"{metric.name} ({metric.entity_id or metric.metric_id})"
            number = metric_decimal(metric)
            points.append(
                m.Point(
                    label=metric.period,
                    series=series,
                    value=decimal_text(number) if number is not None else None,
                    metric_id=metric.metric_id,
                )
            )
        points.sort(
            key=lambda point: (
                point.series,
                period_sort_key(
                    point.label,
                    metric_map[point.metric_id].granularity,
                ),
                request.source_metric_ids.index(point.metric_id),
            )
        )
        unit = units[0]
        charts.append(
            m.Chart(
                chapter_id=chapter_id,
                chart_id=request.chart_id,
                block_id=request.block_id,
                title=request.title,
                type=request.type,
                source_metric_ids=request.source_metric_ids,
                data=points,
                unit=unit,
                asset_id=chart_asset_id(chapter_id, request, points, unit),
            )
        )

    return ChartResult(charts=charts, issues=sort_issues(issues))
