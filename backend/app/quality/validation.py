"""Deterministic chapter and report validation with stable WARN/BLOCK results."""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re

from ..contracts import models as m
from .common import format_percent, make_issue, metric_decimal, sort_issues

CHAPTER_RULES = ("structure", "sources", "metrics", "charts")
REPORT_RULES = (*CHAPTER_RULES, "dependencies", "freshness")

RULE_CODES = {
    "structure": {
        "DUPLICATE_BLOCK_ID",
        "DUPLICATE_CHAPTER_ID",
        "DUPLICATE_CHAPTER_ORDER",
        "DUPLICATE_CHART_BLOCK",
        "DUPLICATE_CHART_ID",
        "DUPLICATE_EVIDENCE_ID",
        "DUPLICATE_METRIC_ID",
        "MISSING_CHART_REQUEST",
        "MISSING_REQUIRED_EVIDENCE",
        "TABLE_WIDTH_MISMATCH",
        "UNEXPECTED_CHAPTER",
    },
    "sources": {
        "INVALID_EVIDENCE_REF",
        "SOURCE_VERSION_MISMATCH",
    },
    "metrics": {
        "FORMULA_SOURCE_MISSING",
        "INVALID_CHART_UNIT",
        "INVALID_METRIC_REF",
        "MISSING_REQUIRED_METRIC",
        "METRIC_PERIOD_MISMATCH",
        "METRIC_UNIT_MISMATCH",
        "METRIC_VALUE_MISMATCH",
        "UNVERIFIED_NUMBER",
    },
    "charts": {
        "CHART_DATA_INSUFFICIENT",
        "CHART_UNIT_MISMATCH",
        "INVALID_CHART_REF",
        "INVALID_CHART_TARGET",
    },
    "dependencies": {
        "CYCLIC_DEPENDENCY",
        "INVALID_DEPENDENCY",
    },
    "freshness": {"STALE_CHAPTER"},
}


def _index(
    items: list,
    field: str,
) -> tuple[dict[str, object], set[str]]:
    counts = Counter(getattr(item, field) for item in items)
    duplicates = {item_id for item_id, count in counts.items() if count > 1}
    indexed = {
        getattr(item, field): item
        for item in items
        if getattr(item, field) not in duplicates
    }
    return indexed, duplicates


def _duplicate_issues(
    duplicates: set[str],
    code: str,
    description: str,
    chapter_id: str | None,
) -> list[m.Issue]:
    return [
        make_issue(
            "BLOCK",
            code,
            f"{description} {item_id} 重复",
            chapter_id=chapter_id,
            related_ids=[item_id],
        )
        for item_id in sorted(duplicates)
    ]


def _facts(chapter: m.Chapter) -> list[m.Fact]:
    facts: list[m.Fact] = list(chapter.fact_candidates)
    for block in chapter.blocks:
        if isinstance(block, m.Paragraph):
            facts.append(block)
        elif isinstance(block, m.Table):
            for row in block.rows:
                facts.extend(row)
    return facts


def _numeric_tokens(text: str) -> list[str]:
    return re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", ""))


def _same_rounded(left: Decimal, right: Decimal) -> bool:
    quantum = Decimal("0.01")
    return left.quantize(quantum, rounding=ROUND_HALF_UP) == right.quantize(
        quantum, rounding=ROUND_HALF_UP
    )


def _check_paragraph_numbers(
    chapter: m.Chapter,
    paragraph: m.Paragraph,
    metric_map: dict[str, m.Metric],
) -> list[m.Issue]:
    issues: list[m.Issue] = []
    linked = [metric_map[item] for item in paragraph.metric_ids if item in metric_map]
    percent_metrics = [
        metric
        for metric in linked
        if metric.formula_id == "yoy_pct" or metric.unit == "%"
    ]
    percent_tokens = re.findall(r"-?\d+(?:\.\d+)?%", paragraph.text.replace(",", ""))
    if percent_tokens and percent_metrics:
        expected = {
            format_percent(metric).removesuffix("%")
            for metric in percent_metrics
            if metric_decimal(metric) is not None
        }
        mismatched = [
            token
            for token in percent_tokens
            if token.removesuffix("%") not in expected
        ]
        if mismatched:
            issues.append(
                make_issue(
                    "BLOCK",
                    "METRIC_VALUE_MISMATCH",
                    f"正文百分比 {', '.join(mismatched)} 与关联指标不一致",
                    chapter_id=chapter.chapter_id,
                    block_id=paragraph.block_id,
                    related_ids=paragraph.metric_ids,
                )
            )

    if len(linked) == 1:
        metric = linked[0]
        expected = metric_decimal(metric)
        if "%" not in metric.unit:
            unit_tokens = re.findall(
                rf"(-?\d+(?:\.\d+)?)\s*{re.escape(metric.unit)}",
                paragraph.text.replace(",", ""),
            )
            if expected is not None:
                for token in unit_tokens:
                    try:
                        if Decimal(token) != expected:
                            issues.append(
                                make_issue(
                                    "BLOCK",
                                    "METRIC_VALUE_MISMATCH",
                                    f"正文数值 {token} {metric.unit} 与关联指标 {expected} 不一致",
                                    chapter_id=chapter.chapter_id,
                                    block_id=paragraph.block_id,
                                    related_ids=[metric.metric_id],
                                )
                            )
                            break
                    except InvalidOperation:
                        continue
        years = re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", paragraph.text)
        metric_years = set(re.findall(r"(?:19|20)\d{2}", metric.period))
        if years and not metric_years.intersection(years):
            issues.append(
                make_issue(
                    "BLOCK",
                    "METRIC_PERIOD_MISMATCH",
                    f"正文年份 {'、'.join(sorted(set(years)))} 与关联指标期间 {metric.period} 不一致",
                    chapter_id=chapter.chapter_id,
                    block_id=paragraph.block_id,
                    related_ids=[metric.metric_id],
                )
            )

    if not paragraph.metric_ids and re.search(
        r"\d+(?:\.\d+)?\s*(?:%|万辆|亿元|万元|辆|元|GWh|万套|套|家)",
        paragraph.text.replace(",", ""),
    ):
        issues.append(
            make_issue(
                "WARN",
                "UNVERIFIED_NUMBER",
                "正文含有未关联指标的数值，无法完成程序核验",
                chapter_id=chapter.chapter_id,
                block_id=paragraph.block_id,
            )
        )
    return issues


def _check_table_numbers(
    chapter: m.Chapter,
    table: m.Table,
    metric_map: dict[str, m.Metric],
) -> list[m.Issue]:
    issues: list[m.Issue] = []
    for row_index, row in enumerate(table.rows):
        for column_index, cell in enumerate(row):
            if len(cell.metric_ids) != 1:
                continue
            metric = metric_map.get(cell.metric_ids[0])
            expected = metric_decimal(metric) if metric else None
            if metric is None or expected is None:
                continue
            tokens = re.findall(
                r"(-?\d+(?:\.\d+)?)\s*(%|万辆|亿元|万元|辆|元|GWh|万套|套|家)",
                cell.text.replace(",", ""),
            )
            for raw_number, found_unit in tokens:
                if found_unit != metric.unit:
                    issues.append(
                        make_issue(
                            "BLOCK",
                            "METRIC_UNIT_MISMATCH",
                            f"表格单位 {found_unit} 与关联指标单位 {metric.unit} 不一致",
                            chapter_id=chapter.chapter_id,
                            block_id=table.block_id,
                            related_ids=[metric.metric_id],
                        )
                    )
                    continue
                try:
                    actual = Decimal(raw_number)
                except InvalidOperation:
                    continue
                matches = (
                    _same_rounded(actual, expected)
                    if metric.unit == "%"
                    else actual == expected
                )
                if not matches:
                    issues.append(
                        make_issue(
                            "BLOCK",
                            "METRIC_VALUE_MISMATCH",
                            f"表格单元格数值与关联指标不一致（第 {row_index + 1} 行第 {column_index + 1} 列）",
                            chapter_id=chapter.chapter_id,
                            block_id=table.block_id,
                            related_ids=[metric.metric_id],
                        )
                    )
    return issues


def _chapter_issues(
    chapter: m.Chapter,
    evidences: list[m.Evidence],
    metrics: list[m.Metric],
) -> list[m.Issue]:
    issues: list[m.Issue] = []
    evidence_map, duplicate_evidence = _index(evidences, "evidence_id")
    metric_map, duplicate_metrics = _index(metrics, "metric_id")
    issues.extend(
        _duplicate_issues(
            duplicate_evidence,
            "DUPLICATE_EVIDENCE_ID",
            "证据 ID",
            chapter.chapter_id,
        )
    )
    issues.extend(
        _duplicate_issues(
            duplicate_metrics,
            "DUPLICATE_METRIC_ID",
            "指标 ID",
            chapter.chapter_id,
        )
    )

    block_map, duplicate_blocks = _index(chapter.blocks, "block_id")
    issues.extend(
        _duplicate_issues(
            duplicate_blocks,
            "DUPLICATE_BLOCK_ID",
            "内容块 ID",
            chapter.chapter_id,
        )
    )
    for block in chapter.blocks:
        if isinstance(block, m.Table):
            for row_index, row in enumerate(block.rows):
                if len(row) != len(block.columns):
                    issues.append(
                        make_issue(
                            "BLOCK",
                            "TABLE_WIDTH_MISMATCH",
                            f"表格第 {row_index + 1} 行有 {len(row)} 个单元格，表头有 {len(block.columns)} 列",
                            chapter_id=chapter.chapter_id,
                            block_id=block.block_id,
                        )
                    )

    for fact in _facts(chapter):
        missing_evidence = sorted(set(fact.evidence_ids).difference(evidence_map))
        missing_metrics = sorted(set(fact.metric_ids).difference(metric_map))
        if missing_evidence:
            issues.append(
                make_issue(
                    "BLOCK",
                    "INVALID_EVIDENCE_REF",
                    "正文引用了本次资料范围内不存在的证据",
                    chapter_id=chapter.chapter_id,
                    block_id=getattr(fact, "block_id", None),
                    related_ids=missing_evidence,
                )
            )
        if missing_metrics:
            issues.append(
                make_issue(
                    "BLOCK",
                    "INVALID_METRIC_REF",
                    "正文引用了本章指标集中不存在的指标",
                    chapter_id=chapter.chapter_id,
                    block_id=getattr(fact, "block_id", None),
                    related_ids=missing_metrics,
                )
            )

    for gap in chapter.missing_evidence:
        if gap.required:
            issues.append(
                make_issue(
                    "BLOCK",
                    "MISSING_REQUIRED_EVIDENCE",
                    f"缺少必需资料：{gap.reason}",
                    chapter_id=chapter.chapter_id,
                    related_ids=[gap.question],
                )
            )

    chart_requests: dict[str, m.ChartRequest] = {}
    chart_id_counts = Counter(request.chart_id for request in chapter.chart_requests)
    target_counts = Counter(request.block_id for request in chapter.chart_requests)
    for request in chapter.chart_requests:
        if chart_id_counts[request.chart_id] > 1:
            issues.append(
                make_issue(
                    "BLOCK",
                    "DUPLICATE_CHART_ID",
                    f"图表 ID {request.chart_id} 重复",
                    chapter_id=chapter.chapter_id,
                    block_id=request.block_id,
                    related_ids=[request.chart_id],
                )
            )
        if target_counts[request.block_id] > 1:
            issues.append(
                make_issue(
                    "BLOCK",
                    "DUPLICATE_CHART_BLOCK",
                    f"图表目标块 {request.block_id} 重复",
                    chapter_id=chapter.chapter_id,
                    block_id=request.block_id,
                )
            )
        chart_requests[request.chart_id] = request
        missing_metrics = sorted(
            set(request.source_metric_ids).difference(metric_map)
        )
        if missing_metrics:
            issues.append(
                make_issue(
                    "BLOCK",
                    "INVALID_METRIC_REF",
                    "图表请求引用了本章指标集中不存在的指标",
                    chapter_id=chapter.chapter_id,
                    block_id=request.block_id,
                    related_ids=missing_metrics,
                )
            )
        target = block_map.get(request.block_id)
        if target is None:
            issues.append(
                make_issue(
                    "BLOCK",
                    "INVALID_CHART_TARGET",
                    "图表请求目标块不存在",
                    chapter_id=chapter.chapter_id,
                    block_id=request.block_id,
                    related_ids=[request.chart_id],
                )
            )
        elif not isinstance(target, m.ChartRef) or target.chart_id != request.chart_id:
            issues.append(
                make_issue(
                    "BLOCK",
                    "INVALID_CHART_TARGET",
                    "图表请求与目标 chart_ref 不匹配",
                    chapter_id=chapter.chapter_id,
                    block_id=request.block_id,
                    related_ids=[request.chart_id],
                )
            )

    for block in chapter.blocks:
        if isinstance(block, m.ChartRef):
            request = chart_requests.get(block.chart_id)
            if request is None or request.block_id != block.block_id:
                issues.append(
                    make_issue(
                        "BLOCK",
                        "MISSING_CHART_REQUEST",
                        f"chart_ref {block.chart_id} 缺少匹配的图表请求",
                        chapter_id=chapter.chapter_id,
                        block_id=block.block_id,
                        related_ids=[block.chart_id],
                    )
                )
        if isinstance(block, m.Paragraph):
            issues.extend(_check_paragraph_numbers(chapter, block, metric_map))
        elif isinstance(block, m.Table):
            issues.extend(_check_table_numbers(chapter, block, metric_map))

    return sort_issues(issues)


def _summary_from_issues(
    issues: list[m.Issue],
    rule_ids: tuple[str, ...],
) -> m.QualitySummary:
    unique = {issue.issue_id: issue for issue in issues}
    issues = sort_issues(unique.values())
    rules: list[m.Rule] = []
    for rule_id in rule_ids:
        related = [
            issue for issue in issues if issue.code in RULE_CODES.get(rule_id, set())
        ]
        blocks = sum(issue.severity == "BLOCK" for issue in related)
        warnings = sum(issue.severity == "WARN" for issue in related)
        if related:
            reason = f"发现 {blocks} 个阻断问题和 {warnings} 个警告"
            result = "FAIL"
        else:
            reason = "检查通过"
            result = "PASS"
        rules.append(
            m.Rule(
                rule_id=rule_id,
                result=result,
                reason=reason,
                required=True,
            )
        )
    return m.QualitySummary(rules=rules, issues=issues)


def validate_chapter(
    chapter: m.Chapter,
    evidences: list[m.Evidence],
    metrics: list[m.Metric],
) -> m.QualitySummary:
    return _summary_from_issues(
        _chapter_issues(chapter, evidences, metrics),
        CHAPTER_RULES,
    )


def _dependency_issues(tasks: list[m.Task]) -> list[m.Issue]:
    issues: list[m.Issue] = []
    task_ids = {task.chapter_id for task in tasks}
    task_by_id = {task.chapter_id: task for task in tasks}
    for task in tasks:
        missing = sorted(set(task.depends_on).difference(task_ids))
        if missing:
            issues.append(
                make_issue(
                    "BLOCK",
                    "INVALID_DEPENDENCY",
                    "章节依赖了冻结大纲中不存在的章节",
                    chapter_id=task.chapter_id,
                    related_ids=missing,
                )
            )

    indegree = {
        task.chapter_id: len(set(task.depends_on).intersection(task_ids))
        for task in tasks
    }
    outgoing: dict[str, list[str]] = defaultdict(list)
    for task in tasks:
        for dependency in set(task.depends_on).intersection(task_ids):
            outgoing[dependency].append(task.chapter_id)
    ready = sorted(chapter_id for chapter_id, degree in indegree.items() if degree == 0)
    visited: list[str] = []
    while ready:
        current = ready.pop(0)
        visited.append(current)
        for dependent in sorted(outgoing[current]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
                ready.sort()
    cyclic = sorted(set(task_by_id).difference(visited))
    if cyclic:
        issues.append(
            make_issue(
                "BLOCK",
                "CYCLIC_DEPENDENCY",
                "章节依赖关系存在环",
                chapter_id=cyclic[0],
                related_ids=cyclic,
            )
        )
    return issues


def _report_issues(document: m.ReportDocument) -> list[m.Issue]:
    issues: list[m.Issue] = []
    tasks, duplicate_tasks = _index(document.outline.chapters, "chapter_id")
    chapters, duplicate_chapters = _index(document.chapters, "chapter_id")
    evidences, duplicate_evidence = _index(document.evidences, "evidence_id")
    metrics, duplicate_metrics = _index(document.metrics, "metric_id")
    charts, duplicate_charts = _index(document.charts, "chart_id")
    issues.extend(
        _duplicate_issues(
            duplicate_tasks, "DUPLICATE_CHAPTER_ID", "大纲章节 ID", None
        )
    )
    issues.extend(
        _duplicate_issues(
            duplicate_chapters, "DUPLICATE_CHAPTER_ID", "正文章节 ID", None
        )
    )
    issues.extend(
        _duplicate_issues(
            duplicate_evidence, "DUPLICATE_EVIDENCE_ID", "证据 ID", None
        )
    )
    issues.extend(
        _duplicate_issues(
            duplicate_metrics, "DUPLICATE_METRIC_ID", "指标 ID", None
        )
    )
    issues.extend(
        _duplicate_issues(
            duplicate_charts, "DUPLICATE_CHART_ID", "图表 ID", None
        )
    )

    order_counts = Counter(task.order for task in document.outline.chapters)
    for order, count in sorted(order_counts.items()):
        if count > 1:
            issues.append(
                make_issue(
                    "BLOCK",
                    "DUPLICATE_CHAPTER_ORDER",
                    f"章节顺序 {order} 被重复使用",
                    related_ids=[task.chapter_id for task in document.outline.chapters if task.order == order],
                )
            )

    for task in document.outline.chapters:
        if task.required and task.chapter_id not in chapters:
            issues.append(
                make_issue(
                    "BLOCK",
                    "MISSING_REQUIRED_CHAPTER",
                    f"缺少必需章节：{task.title}",
                    chapter_id=task.chapter_id,
                )
            )
        for indicator in task.required_metrics:
            if not any(metric.indicator == indicator for metric in document.metrics):
                issues.append(
                    make_issue(
                        "BLOCK",
                        "MISSING_REQUIRED_METRIC",
                        f"章节要求指标 {indicator}，但报告中没有该指标",
                        chapter_id=task.chapter_id,
                        related_ids=[indicator],
                    )
                )
    for chapter_id in sorted(set(chapters).difference(tasks)):
        issues.append(
            make_issue(
                "BLOCK",
                "UNEXPECTED_CHAPTER",
                "报告包含冻结大纲中不存在的章节",
                chapter_id=chapter_id,
            )
        )

    issues.extend(_dependency_issues(document.outline.chapters))

    source_versions = set(document.source_version_ids)
    for evidence in document.evidences:
        if evidence.document_version_id not in source_versions:
            issues.append(
                make_issue(
                    "BLOCK",
                    "SOURCE_VERSION_MISMATCH",
                    "证据来自冻结资料版本清单之外",
                    related_ids=[evidence.evidence_id, evidence.document_version_id],
                )
            )

    for metric in document.metrics:
        missing_evidence = sorted(
            set(metric.source_evidence_ids).difference(evidences)
        )
        missing_inputs = sorted(set(metric.input_metric_ids).difference(metrics))
        if missing_evidence:
            issues.append(
                make_issue(
                    "BLOCK",
                    "INVALID_EVIDENCE_REF",
                    "指标引用了报告中不存在的证据",
                    related_ids=[metric.metric_id, *missing_evidence],
                )
            )
        if missing_inputs:
            issues.append(
                make_issue(
                    "BLOCK",
                    "INVALID_METRIC_REF",
                    "派生指标引用了不存在的输入指标",
                    related_ids=[metric.metric_id, *missing_inputs],
                )
            )
        if metric.formula_id and not metric.input_metric_ids:
            issues.append(
                make_issue(
                    "BLOCK",
                    "FORMULA_SOURCE_MISSING",
                    "派生指标没有记录输入指标",
                    related_ids=[metric.metric_id],
                )
            )

    for chart in document.charts:
        if chart.chapter_id not in chapters:
            issues.append(
                make_issue(
                    "BLOCK",
                    "INVALID_CHART_REF",
                    "图表关联了不存在的章节",
                    chapter_id=chart.chapter_id,
                    block_id=chart.block_id,
                    related_ids=[chart.chart_id],
                )
            )
        missing_sources = sorted(set(chart.source_metric_ids).difference(metrics))
        invalid_points = sorted(
            {
                point.metric_id
                for point in chart.data
                if point.metric_id not in chart.source_metric_ids
            }
        )
        if missing_sources or invalid_points:
            issues.append(
                make_issue(
                    "BLOCK",
                    "INVALID_CHART_REF",
                    "图表源指标或数据点引用无法解析",
                    chapter_id=chart.chapter_id,
                    block_id=chart.block_id,
                    related_ids=[chart.chart_id, *missing_sources, *invalid_points],
                )
            )
        missing_points = sorted(
            set(chart.source_metric_ids).difference(
                point.metric_id for point in chart.data
            )
        )
        if missing_points:
            issues.append(
                make_issue(
                    "WARN",
                    "CHART_DATA_INSUFFICIENT",
                    "图表源指标缺少对应数据点",
                    chapter_id=chart.chapter_id,
                    block_id=chart.block_id,
                    related_ids=[chart.chart_id, *missing_points],
                )
            )
        source_metrics = [
            metric for metric in document.metrics if metric.metric_id in chart.source_metric_ids
        ]
        if source_metrics and any(metric.unit != chart.unit for metric in source_metrics):
            issues.append(
                make_issue(
                    "BLOCK",
                    "INVALID_CHART_UNIT",
                    "图表单位与源指标单位不一致",
                    chapter_id=chart.chapter_id,
                    block_id=chart.block_id,
                    related_ids=[chart.chart_id],
                )
            )

    for chapter in document.chapters:
        issues.extend(_chapter_issues(chapter, document.evidences, document.metrics))

    for chapter_id in document.stale_chapter_ids:
        issues.append(
            make_issue(
                "BLOCK",
                "STALE_CHAPTER",
                "章节依赖已变化，尚未重新生成",
                chapter_id=chapter_id,
            )
        )
    return sort_issues(issues)


def validate_report(document: m.ReportDocument) -> m.QualitySummary:
    return _summary_from_issues(_report_issues(document), REPORT_RULES)
