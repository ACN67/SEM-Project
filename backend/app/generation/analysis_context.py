"""Organize verified quarterly metrics for the model's writing prompt."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from itertools import pairwise
import re
from typing import TypedDict

from ..contracts import models as m

_QUARTER = re.compile(r"([0-9]{4})-Q([1-4])")
# Match the formula ID emitted by the quality service.
_YOY_FORMULA = "yoy_pct"
Series = tuple[str | None, str, str, str, str | None]


class QuarterlyYoy(TypedDict):
    period: str
    baseline_period: str
    current_metric_id: str
    baseline_metric_id: str
    yoy_metric_id: str


class QuarterlyChange(TypedDict):
    earlier_period: str
    later_period: str
    earlier_yoy_metric_id: str
    later_yoy_metric_id: str
    direction: str


class QuarterlyAnalysis(TypedDict):
    year_over_year: list[QuarterlyYoy]
    adjacent_yoy_changes: list[QuarterlyChange]


def _quarter_number(metric: m.Metric) -> int | None:
    match = _QUARTER.fullmatch(metric.period)
    if match is None or metric.granularity != "quarter":
        return None
    year, quarter = map(int, match.groups())
    return year * 4 + quarter - 1


def _series(metric: m.Metric) -> Series:
    return metric.entity_id, metric.region, metric.scope, metric.unit, metric.currency


def quarterly_analysis(spec: m.Prosperity, metrics: list[m.Metric]) -> QuarterlyAnalysis:
    """Compare available rate metrics without deriving new rates or filling gaps."""
    observations: dict[tuple[Series, int], list[m.Metric]] = defaultdict(list)
    rates: dict[tuple[Series, int], list[tuple[m.Metric, m.Metric, m.Metric]]] = defaultdict(list)
    by_id = {metric.metric_id: metric for metric in metrics}

    for metric in metrics:
        quarter = _quarter_number(metric)
        if metric.indicator == spec.indicator and metric.formula_id is None and quarter is not None:
            observations[_series(metric), quarter].append(metric)

    for rate in metrics:
        quarter = _quarter_number(rate)
        if (
            rate.formula_id != _YOY_FORMULA or rate.indicator != spec.indicator
            or rate.unit != "%" or rate.value is None or quarter is None
            or not int(spec.period.from_) <= quarter // 4 <= int(spec.period.to)
            or len(rate.input_metric_ids) != 2
        ):
            continue
        baseline, current = (by_id.get(item) for item in rate.input_metric_ids)
        if baseline is None or current is None:
            continue
        source_ids = set(baseline.source_evidence_ids).union(current.source_evidence_ids)
        series = _series(current)
        if (
            observations.get((series, quarter)) == [current]
            and observations.get((series, quarter - 4)) == [baseline]
            and current.value is not None and baseline.value is not None
            and Decimal(baseline.value) != 0
            and baseline.source_evidence_ids and current.source_evidence_ids
            and source_ids.issubset(rate.source_evidence_ids)
            and (rate.entity_id, rate.region, rate.scope) == (current.entity_id, current.region, current.scope)
        ):
            rates[series, quarter].append((baseline, current, rate))

    year_over_year: list[QuarterlyYoy] = []
    changes: list[QuarterlyChange] = []
    by_series: dict[Series, list[tuple[int, m.Metric]]] = defaultdict(list)
    for (series, quarter), entries in sorted(rates.items(), key=lambda item: item[0][1]):
        if len(entries) != 1:
            continue
        baseline, current, rate = entries[0]
        year_over_year.append({
            "period": rate.period, "baseline_period": baseline.period,
            "current_metric_id": current.metric_id, "baseline_metric_id": baseline.metric_id,
            "yoy_metric_id": rate.metric_id,
        })
        by_series[series].append((quarter, rate))

    for entries in by_series.values():
        for (earlier_quarter, earlier), (later_quarter, later) in pairwise(entries):
            if later_quarter - earlier_quarter != 1:
                continue
            before, after = Decimal(earlier.value), Decimal(later.value)
            direction = "加快" if after > before else "放缓" if after < before else "持平"
            changes.append({
                "earlier_period": earlier.period, "later_period": later.period,
                "earlier_yoy_metric_id": earlier.metric_id, "later_yoy_metric_id": later.metric_id,
                "direction": direction,
            })

    return {"year_over_year": year_over_year, "adjacent_yoy_changes": changes}
