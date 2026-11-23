"""Shared deterministic helpers for the quality module."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import re
from typing import Iterable

from ..contracts import models as m


def metric_decimal(metric: m.Metric) -> Decimal | None:
    """Return the normalized metric value without relying on binary floats."""
    if metric.value is not None:
        raw = metric.value
    elif metric.raw_value is not None:
        try:
            multiplier = Decimal(metric.multiplier)
            raw = format(Decimal(metric.raw_value) * multiplier, "f")
        except InvalidOperation:
            return None
    else:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def decimal_text(value: Decimal) -> str:
    """Serialize a Decimal in the non-exponent form required by the contract."""
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def format_percent(value: Decimal | str | m.Metric) -> str:
    """Format a percentage value with two decimals using half-up rounding."""
    if isinstance(value, m.Metric):
        parsed = metric_decimal(value)
        if parsed is None:
            raise ValueError("metric has no numeric value")
        value = parsed
    else:
        parsed = value if isinstance(value, Decimal) else Decimal(value)
    rounded = parsed.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{rounded:.2f}%"


def period_sort_key(period: str, granularity: str) -> tuple[int, int, int]:
    """Return a chronological sort key; malformed time buckets sort last."""
    if granularity == "year" and re.fullmatch(r"\d{4}", period):
        return int(period), 0, 0
    quarter = re.fullmatch(r"(\d{4})-Q([1-4])", period)
    if granularity == "quarter" and quarter:
        return int(quarter.group(1)), int(quarter.group(2)) * 3, 0
    month = re.fullmatch(r"(\d{4})-(\d{2})", period)
    if granularity == "month" and month and 1 <= int(month.group(2)) <= 12:
        return int(month.group(1)), int(month.group(2)), 0
    return 9999, 99, 99


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def stable_digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def make_issue(
    severity: str,
    code: str,
    description: str,
    *,
    chapter_id: str | None = None,
    block_id: str | None = None,
    related_ids: Iterable[str] = (),
) -> m.Issue:
    related = sorted({str(item) for item in related_ids})
    location = m.Location(chapter_id=chapter_id, block_id=block_id)
    payload = {
        "severity": severity,
        "code": code,
        "description": description,
        "location": location.model_dump(mode="json"),
        "related_ids": related,
    }
    return m.Issue(
        issue_id=f"issue_{stable_digest(payload)[:16]}",
        severity=severity,
        code=code,
        location=location,
        description=description,
        related_ids=related,
    )


def sort_issues(issues: Iterable[m.Issue]) -> list[m.Issue]:
    severity_order = {"BLOCK": 0, "WARN": 1}
    return sorted(
        issues,
        key=lambda issue: (
            issue.location.chapter_id or "",
            issue.location.block_id or "",
            severity_order[issue.severity],
            issue.code,
            tuple(issue.related_ids),
            issue.issue_id,
        ),
    )
