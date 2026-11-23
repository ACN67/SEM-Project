"""Implement the Generation protocol without owning job or version state."""
from __future__ import annotations

import re
from collections.abc import Iterator

from pydantic import ValidationError

from ..contracts import models as m
from ..contracts.validation import check_chapter, indexed, refs
from .client import ChapterWriter, ChatCompletionsClient, generation_error
from .outline import plan_outline
from .prompts import chapter_instructions

_LINK = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)


def _content_facts(chapter: m.Chapter) -> Iterator[m.Fact]:
    for block in chapter.blocks:
        if isinstance(block, m.Paragraph):
            yield block
        elif isinstance(block, m.Table):
            for row in block.rows:
                yield from row


def summarize_chapter(chapter: m.Chapter) -> m.Summary:
    """Build a predecessor only after the chapter has passed quality checks."""
    content = list(_content_facts(chapter))
    return m.Summary(
        chapter_id=chapter.chapter_id,
        text=chapter.summary,
        evidence_ids=list(dict.fromkeys(item for fact in content for item in fact.evidence_ids)),
        metric_ids=list(dict.fromkeys(item for fact in content for item in fact.metric_ids)),
    )


def _check_context(context: m.ChapterInput) -> None:
    if context.spec.template_id != context.template_ref.template_id:
        raise generation_error("INVALID_CHAPTER_INPUT", "冻结模板与报告需求不匹配。")
    try:
        previous = indexed(context.predecessors, "chapter_id")
        evidences = indexed(context.evidences, "evidence_id")
        metrics = indexed(context.metrics, "metric_id")
        refs(previous, context.task.depends_on, "unexpected predecessor")
        refs(
            (item.document_version_id for item in context.evidences),
            context.source_version_ids, "document version",
        )
        for fact in [*context.predecessors, *context.facts]:
            refs(fact.evidence_ids, evidences, "inherited evidence")
            refs(fact.metric_ids, metrics, "inherited metric")
        for metric in context.metrics:
            refs(metric.source_evidence_ids, evidences, "metric evidence")
            refs(metric.input_metric_ids, metrics, "formula input")
    except ValueError:
        raise generation_error(
            "INVALID_CHAPTER_INPUT", "章节证据包存在重复标识或未解析的引用。",
        ) from None
    waiting = sorted(set(context.task.depends_on).difference(previous))
    if waiting:
        raise generation_error(
            "DEPENDENCY_NOT_READY", "前置章节未完成。", chapter_ids=waiting,
        )


def _known_gaps(context: m.ChapterInput) -> list[m.Gap]:
    task = context.task
    gaps = list(context.gaps)
    if not context.evidences:
        gaps.append(m.Gap(
            question=task.title, code="COVERAGE_INSUFFICIENT",
            reason="冻结资料范围内没有可引用的章节证据。",
            required=task.required,
        ))
    available = {item.indicator for item in context.metrics if item.value is not None}
    for indicator in dict.fromkeys(task.required_metrics):
        if indicator not in available:
            gaps.append(m.Gap(
                question=indicator, code="MISSING_REQUIRED_METRIC",
                reason=f"缺少可用指标「{indicator}」。", required=task.required,
            ))
    return gaps


def _validate_output(raw: str, context: m.ChapterInput) -> m.Chapter:
    try:
        chapter = m.Chapter.model_validate_json(raw, strict=True)
    except (ValidationError, UnicodeError):
        raise generation_error(
            "INVALID_MODEL_OUTPUT", "模型输出不符合章节结构。", retryable=True,
        ) from None

    try:
        evidence_ids = {item.evidence_id for item in context.evidences}
        metrics = {item.metric_id: item for item in context.metrics}
        check_chapter(chapter, evidence_ids, metrics)
    except ValueError as exc:
        code = "INVALID_EVIDENCE_REF" if str(exc) == "unresolved evidence" else "INVALID_MODEL_OUTPUT"
        raise generation_error(
            code, "章节引用或内容块结构无效。", retryable=True,
        ) from None

    try:
        if chapter.chapter_id != context.task.chapter_id or chapter.title != context.task.title:
            raise ValueError
        content = list(_content_facts(chapter))
        if not chapter.summary or not content:
            raise ValueError
        facts = [*content, *chapter.fact_candidates]
        texts = [chapter.summary, *(fact.text for fact in facts)]
        for block in chapter.blocks:
            if isinstance(block, m.Table):
                if not block.columns or not block.rows:
                    raise ValueError
                texts.extend(block.columns)
        texts.extend(request.title for request in chapter.chart_requests)
        texts.extend(gap.reason for gap in chapter.missing_evidence)
        texts.extend(gap.question for gap in chapter.missing_evidence)
        if any(_LINK.search(text) for text in texts):
            raise ValueError
        if any(not fact.evidence_ids and not fact.metric_ids for fact in chapter.fact_candidates):
            raise ValueError
        _bind_charts(chapter, context.task)
        for fact in facts:
            sources = (
                source for metric_id in fact.metric_ids
                for source in metrics[metric_id].source_evidence_ids
            )
            fact.evidence_ids = list(dict.fromkeys([*fact.evidence_ids, *sources]))
        return chapter
    except ValueError:
        raise generation_error(
            "INVALID_MODEL_OUTPUT", "章节身份、正文、事实来源或图表位置不符合要求。",
            retryable=True,
        ) from None


def _bind_charts(chapter: m.Chapter, task: m.Task) -> None:
    requests = indexed(chapter.chart_requests, "chart_id")
    positions = {block.block_id: block for block in chapter.blocks if isinstance(block, m.ChartRef)}
    if len(positions) != len(requests):
        raise ValueError
    for request in requests.values():
        block = positions.get(request.block_id)
        if (
            block is None or block.chart_id != request.chart_id
            or not request.source_metric_ids
            or len(set(request.source_metric_ids)) != len(request.source_metric_ids)
            or (task.desired_chart is not None and request.type != task.desired_chart)
        ):
            raise ValueError
        # A length prefix keeps the chapter/block pair unambiguous for opaque IDs.
        chart_id = f"{len(task.chapter_id)}:{task.chapter_id}:{block.block_id}"
        block.chart_id = request.chart_id = chart_id


class GenerationService:
    def __init__(self, writer: ChapterWriter):
        self._writer = writer

    @classmethod
    def from_env(cls) -> GenerationService:
        return cls(ChatCompletionsClient.from_env())

    def plan_outline(self, spec: m.Spec, template: m.Template) -> m.Outline:
        return plan_outline(spec, template)

    def generate_chapter(self, input: m.ChapterInput) -> m.Chapter:
        return self._generate(input, synthesis=bool(input.task.depends_on))

    def generate_synthesis(self, input: m.ChapterInput) -> m.Chapter:
        return self._generate(input, synthesis=True)

    def _generate(self, context: m.ChapterInput, *, synthesis: bool) -> m.Chapter:
        _check_context(context)
        gaps = _known_gaps(context)
        task = context.task
        if not context.evidences:
            summary = "现有资料不足，暂无法形成有来源支持的章节结论。"
            return m.Chapter(
                chapter_id=task.chapter_id, title=task.title, summary=summary,
                blocks=[m.Paragraph(
                    type="paragraph", block_id="b1", text=summary, evidence_ids=[], metric_ids=[],
                )],
                fact_candidates=[], chart_requests=[], missing_evidence=gaps,
            )

        payload = context.model_copy(update={"gaps": gaps}).model_dump_json(
            by_alias=True,
            exclude={"evidences": {"__all__": {"source_url", "score"}}},
        )
        raw = self._writer.complete(chapter_instructions(context.spec, synthesis=synthesis), payload)
        chapter = _validate_output(raw, context)
        seen = {(gap.question, gap.code, gap.reason, gap.required) for gap in gaps}
        for gap in chapter.missing_evidence:
            key = (gap.question, gap.code, gap.reason, gap.required)
            if key not in seen:
                gaps.append(gap)
                seen.add(key)
        if not any(fact.evidence_ids or fact.metric_ids for fact in _content_facts(chapter)):
            gaps.append(m.Gap(
                question=task.title, code="COVERAGE_INSUFFICIENT",
                reason="章节正文未使用可核验的证据或指标。", required=task.required,
            ))
        return chapter.model_copy(update={"missing_evidence": gaps})
