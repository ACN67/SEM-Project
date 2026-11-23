"""Standardization and deterministic year-over-year metric calculation."""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation, localcontext
import re
from typing import Hashable

from ..contracts import models as m
from ..contracts.services import Calculation
from .common import decimal_text, metric_decimal, period_sort_key

FORMULA_YOY_PCT = "yoy_pct"


def _previous_period(period: str, granularity: str) -> str | None:
    if granularity == "year":
        matched = re.fullmatch(r"(\d{4})", period)
        if matched:
            return f"{int(matched.group(1)) - 1:04d}"
        return None
    if granularity == "quarter":
        matched = re.fullmatch(r"(\d{4})-Q([1-4])", period)
        if matched:
            return f"{int(matched.group(1)) - 1:04d}-Q{matched.group(2)}"
        return None
    matched = re.fullmatch(r"(\d{4})-(\d{2})", period)
    if matched and 1 <= int(matched.group(2)) <= 12:
        return f"{int(matched.group(1)) - 1:04d}-{matched.group(2)}"
    return None


def _comparison_key(metric: m.Metric) -> tuple[Hashable, ...]:
    return (
        metric.indicator,
        metric.entity_id,
        metric.region,
        metric.scope,
        metric.granularity,
        metric.unit,
        metric.currency,
    )


def _diagnostic_key(metric: m.Metric) -> tuple[Hashable, ...]:
    return (
        metric.indicator,
        metric.entity_id,
        metric.region,
        metric.granularity,
    )


def _gap_question(metric: m.Metric) -> str:
    entity = metric.entity_id or "*"
    return f"{metric.indicator}:{entity}:{metric.period}:yoy"


def _add_gap(
    gaps: list[m.Gap],
    seen: set[tuple[str, str, str, bool]],
    metric: m.Metric,
    code: str,
    reason: str,
    required: bool,
) -> None:
    question = _gap_question(metric)
    key = question, code, reason, required
    if key in seen:
        return
    seen.add(key)
    gaps.append(m.Gap(question=question, code=code, reason=reason, required=required))


def _mismatch_reason(
    current: m.Metric,
    base: m.Metric,
) -> tuple[str, str] | None:
    if current.unit != base.unit:
        return "UNIT_MISMATCH", f"本期单位为 {current.unit}，同期单位为 {base.unit}，不能直接计算同比"
    if current.currency != base.currency:
        return (
            "CURRENCY_MISMATCH",
            f"本期币种为 {current.currency or '未注明'}，同期币种为 {base.currency or '未注明'}",
        )
    if current.scope != base.scope:
        return "SCOPE_MISMATCH", "本期与同期的统计口径不同，不能直接计算同比"
    return None


def _derived_yoy(base: m.Metric, current: m.Metric, base_value: Decimal, current_value: Decimal) -> m.Metric:
    with localcontext() as context:
        context.prec = 34
        percentage = (current_value - base_value) / base_value * Decimal("100")
    serialized = decimal_text(percentage)
    return m.Metric(
        metric_id=f"yoy:{current.metric_id}",
        indicator=current.indicator,
        name=f"{current.name}同比",
        entity_id=current.entity_id,
        period=current.period,
        granularity=current.granularity,
        region=current.region,
        scope=current.scope,
        raw_value=serialized,
        raw_unit="%",
        multiplier="1",
        value=serialized,
        unit="%",
        currency=None,
        source_evidence_ids=sorted(
            set(base.source_evidence_ids).union(current.source_evidence_ids)
        ),
        formula_id=FORMULA_YOY_PCT,
        input_metric_ids=[base.metric_id, current.metric_id],
        missing_reason=None,
    )


def calculate_metrics(raw_metrics: list[m.Metric], requirements: m.Task) -> Calculation:
    """Return input metrics plus deterministic YoY metrics and explicit gaps."""
    raw_metrics = list(raw_metrics)
    id_counts = Counter(metric.metric_id for metric in raw_metrics)
    duplicate_ids = {metric_id for metric_id, count in id_counts.items() if count > 1}
    gaps: list[m.Gap] = []
    gap_keys: set[tuple[str, str, str, bool]] = set()

    for metric_id in sorted(duplicate_ids):
        duplicate = next(metric for metric in raw_metrics if metric.metric_id == metric_id)
        _add_gap(
            gaps,
            gap_keys,
            duplicate,
            "DUPLICATE_METRIC_ID",
            f"指标 ID {metric_id} 重复，不能确定比较对象",
            True,
        )

    unique_metrics = [
        metric for metric in raw_metrics if metric.metric_id not in duplicate_ids
    ]
    required_indicators = set(requirements.required_metrics)
    available_indicators = {metric.indicator for metric in unique_metrics}
    for indicator in sorted(required_indicators.difference(available_indicators)):
        question = f"{indicator}:required"
        key = question, "MISSING_REQUIRED_METRIC", f"未提供必需指标 {indicator}", True
        if key not in gap_keys:
            gap_keys.add(key)
            gaps.append(
                m.Gap(
                    question=question,
                    code="MISSING_REQUIRED_METRIC",
                    reason=f"未提供必需指标 {indicator}",
                    required=True,
                )
            )

    indicators = (
        required_indicators
        if required_indicators
        else {metric.indicator for metric in unique_metrics}
    )
    eligible = [metric for metric in unique_metrics if metric.indicator in indicators]

    for metric in eligible:
        if _previous_period(metric.period, metric.granularity) is None:
            _add_gap(
                gaps,
                gap_keys,
                metric,
                "INVALID_PERIOD",
                f"期间 {metric.period} 与粒度 {metric.granularity} 不匹配",
                metric.indicator in required_indicators,
            )

    by_key_and_period: dict[tuple[Hashable, ...], list[m.Metric]] = defaultdict(list)
    by_diagnostic_period: dict[tuple[Hashable, ...], list[m.Metric]] = defaultdict(list)
    by_key: dict[tuple[Hashable, ...], list[m.Metric]] = defaultdict(list)
    for metric in eligible:
        if _previous_period(metric.period, metric.granularity) is None:
            continue
        key = _comparison_key(metric)
        by_key_and_period[(key, metric.period)].append(metric)
        by_diagnostic_period[(_diagnostic_key(metric), metric.period)].append(metric)
        by_key[key].append(metric)

    latest_periods: dict[tuple[Hashable, ...], str] = {}
    for key, metrics in by_key.items():
        latest_periods[key] = max(
            (metric.period for metric in metrics),
            key=lambda period: period_sort_key(period, metrics[0].granularity),
        )

    raw_ids = set(id_counts)
    derived: list[m.Metric] = []
    for key, periods_for_key in by_key.items():
        for period in sorted(
            {metric.period for metric in periods_for_key},
            key=lambda item: period_sort_key(item, periods_for_key[0].granularity),
        ):
            candidates = by_key_and_period[(key, period)]
            required = candidates[0].indicator in required_indicators
            if len(candidates) != 1:
                if period == latest_periods[key]:
                    _add_gap(
                        gaps,
                        gap_keys,
                        candidates[0],
                        "AMBIGUOUS_CURRENT_PERIOD",
                        "同一口径和期间存在多个本期指标，不能确定同比对象",
                        required,
                    )
                continue

            current = candidates[0]
            base_period = _previous_period(current.period, current.granularity)
            if base_period is None:
                continue
            base_candidates = by_key_and_period.get((key, base_period), [])
            if not base_candidates:
                if period == latest_periods[key]:
                    diagnostic_candidates = by_diagnostic_period.get(
                        (_diagnostic_key(current), base_period), []
                    )
                    mismatch = next(
                        (
                            _mismatch_reason(current, candidate)
                            for candidate in diagnostic_candidates
                            if _mismatch_reason(current, candidate) is not None
                        ),
                        None,
                    )
                    if mismatch:
                        _add_gap(
                            gaps,
                            gap_keys,
                            current,
                            mismatch[0],
                            mismatch[1],
                            required,
                        )
                    else:
                        _add_gap(
                            gaps,
                            gap_keys,
                            current,
                            "MISSING_BASE_PERIOD",
                            f"缺少 {base_period} 同期指标",
                            required,
                        )
                continue
            if len(base_candidates) != 1:
                _add_gap(
                    gaps,
                    gap_keys,
                    current,
                    "AMBIGUOUS_COMPARABLE_METRIC",
                    f"同期 {base_period} 存在多个同口径指标，不能确定基期",
                    required,
                )
                continue

            base = base_candidates[0]
            current_value = metric_decimal(current)
            base_value = metric_decimal(base)
            if current_value is None:
                _add_gap(
                    gaps,
                    gap_keys,
                    current,
                    "MISSING_CURRENT_VALUE",
                    "本期指标有记录但缺少可计算的规范值",
                    required,
                )
                continue
            if base_value is None:
                _add_gap(
                    gaps,
                    gap_keys,
                    current,
                    "MISSING_BASE_VALUE",
                    "同期指标有记录但缺少可计算的规范值",
                    required,
                )
                continue
            if base_value == 0:
                _add_gap(
                    gaps,
                    gap_keys,
                    current,
                    "ZERO_BASE",
                    "同期基数为 0，同比不可计算",
                    required,
                )
                continue

            derived_id = f"yoy:{current.metric_id}"
            if derived_id in raw_ids or any(item.metric_id == derived_id for item in derived):
                _add_gap(
                    gaps,
                    gap_keys,
                    current,
                    "DERIVED_METRIC_ID_COLLISION",
                    f"派生指标 ID {derived_id} 与现有指标冲突",
                    True,
                )
                continue
            derived.append(_derived_yoy(base, current, base_value, current_value))

    gaps.sort(key=lambda gap: (gap.question, gap.code, gap.reason))
    return Calculation(metrics=[*raw_metrics, *derived], gaps=gaps)
