"""Report orchestration: schedules jobs, runs the chapter DAG and exports.

Owns task state, retries, budget reservations, idempotency and immutable
version snapshots. Domain modules stay callable through the shared service
protocols; this module is the only writer of job/version/report state.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from ..contracts import models as m
from ..contracts.services import ServiceError
from ..contracts.validation import check_snapshot
from ..database.store import (
    ChapterRecord,
    ExportRecord,
    IdempotencyRequest,
    JobRecord,
    ReportRecord,
    Store,
    new_id,
    request_hash,
)
from ..generation import summarize_chapter
from ..quality import render_chart_png

logger = logging.getLogger(__name__)

_UTC = timezone.utc
JOB_URL = "/api/v1/jobs/{}"
EXPORT_URL = "/api/v1/exports/{}"
WORKER_LOST_AFTER = timedelta(seconds=90)
MAX_ATTEMPTS = 3
TRANSIENT_EXTRA = 2
FORMAT_EXTRA = 1
FORMAT_CODES = {"INVALID_MODEL_OUTPUT", "INVALID_EVIDENCE_REF"}


def _error(code: str, message: str, *, retryable: bool = False, **details) -> ServiceError:
    return ServiceError(m.ErrorInfo(
        code=code, message=message, retryable=retryable, details=details,
    ))


def _resource_not_found(what: str) -> ServiceError:
    return _error("RESOURCE_NOT_FOUND", f"未找到对应的{what}。")


def report_title(spec: m.Spec, template: m.Template) -> str:
    period = spec.period
    return f"{template.title}：{spec.industry}（{spec.region}，{period.from_}–{period.to}）"


class OrchestrationService:
    """Implements the Orchestration protocol plus the worker-side operations."""

    def __init__(
        self,
        store: Store,
        templates,
        knowledge,
        generation,
        quality,
        export,
        *,
        runtime_root: Path,
        model_id: str = "unconfigured",
        top_k: int = 10,
        budget_cny: Decimal = Decimal("100.00"),
        per_call_reserve_cny: Decimal = Decimal("0.10"),
        ingest_runner: Callable[[str], object] | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.store = store
        self.templates = templates
        self.knowledge = knowledge
        self.generation = generation
        self.quality = quality
        self.export = export
        self.runtime_root = Path(runtime_root)
        self.model_id = model_id
        self.top_k = top_k
        self.budget_cny = budget_cny
        self.per_call_reserve_cny = per_call_reserve_cny
        self.ingest_runner = ingest_runner
        self.clock = clock or (lambda: datetime.now(_UTC))
        self.sleeper = sleeper
        # Run-scoped packs: run_id -> evidence/metric objects and rendered PNGs.
        # Kept across retries of the same run so inherited evidence stays resolvable.
        self._packs: dict[str, dict] = {}
        self._assets: dict[str, dict[str, bytes]] = {}

    # ------------------------------------------------------------------
    # Scheduling (Orchestration protocol)
    # ------------------------------------------------------------------
    def schedule_report(self, payload: object, idempotency_key: str) -> m.JobAccepted:
        spec, template = self.templates.validate_request(payload)
        guard = IdempotencyRequest(
            operation="report_create", target_id="",
            key=idempotency_key,
            hash=request_hash(spec.model_dump(mode="json", by_alias=True)),
        )
        now = self.clock()
        job_id = new_id()
        accepted = {
            "job_id": job_id,
            "report_id": new_id(),
            "previous_job_id": None,
            "status_url": JOB_URL.format(job_id),
        }
        report = ReportRecord(
            report_id=accepted["report_id"], title=report_title(spec, template),
            report_type=template.report_type, template_id=template.template_id,
            template_version=template.version, latest_job_id=job_id, job_status="QUEUED",
        )
        job = JobRecord(
            job_id=job_id, report_id=report.report_id, run_id=new_id(), spec=spec,
        )
        response = self.store.begin_report(guard, report, job, accepted, now)
        return m.JobAccepted.model_validate(response)

    def assess_coverage(
        self, task: m.Task, evidences: list[m.Evidence], metrics: list[m.Metric]
    ) -> m.Coverage:
        gaps: list[m.Gap] = []
        if not evidences:
            gaps.append(m.Gap(
                question=task.title, code="COVERAGE_INSUFFICIENT",
                reason="冻结资料范围内没有检索到可用证据。", required=task.required,
            ))
        available = {item.indicator for item in metrics if item.value is not None}
        for indicator in task.required_metrics:
            if indicator not in available:
                gaps.append(m.Gap(
                    question=indicator, code="MISSING_REQUIRED_METRIC",
                    reason=f"缺少可用指标「{indicator}」。", required=task.required,
                ))
        if not gaps:
            status = "SUFFICIENT"
        elif evidences:
            status = "PARTIAL"
        else:
            status = "INSUFFICIENT"
        return m.Coverage(status=status, gaps=gaps)

    def prepare_chapter_context(
        self, task: m.Task, frozen_run: m.FrozenRun, predecessors: list[m.Summary]
    ) -> m.ChapterInput:
        spec = frozen_run.spec
        filters = m.Filters(
            entity_ids=list(getattr(spec, "companies", []) or []),
            regions=[spec.region],
            industries=[spec.industry],
            as_of=spec.as_of,
        )
        evidences = self._retrieve_union(
            [task.title, *task.research_questions, spec.industry, spec.region],
            filters, frozen_run.source_version_ids,
        )
        raw_metrics = self.knowledge.get_metrics(filters, frozen_run.source_version_ids)
        calculation = self.quality.calculate_metrics(raw_metrics, task)
        evidences, metrics = self._merge_package(
            frozen_run.run_id, evidences, calculation.metrics,
            predecessors, frozen_run.facts, frozen_run.source_version_ids,
        )
        return m.ChapterInput(
            run_id=frozen_run.run_id, spec=spec, template_ref=frozen_run.template_ref,
            source_version_ids=frozen_run.source_version_ids,
            facts_version=frozen_run.facts_version, facts=frozen_run.facts,
            task=task, predecessors=predecessors, evidences=evidences,
            metrics=metrics, gaps=calculation.gaps,
        )

    def _retrieve_union(
        self,
        terms: list[str],
        filters: m.Filters,
        source_version_ids: list[str],
    ) -> list[m.Evidence]:
        """Merge per-term retrieval results; precise terms keep priority.

        Real vector retrieval scores each query well; keyword backends and the
        fixture pack benefit from searching the question, title and industry.
        """
        merged: dict[str, m.Evidence] = {}
        for term in dict.fromkeys(terms):
            text = term.strip()
            if not text:
                continue
            for evidence in self.knowledge.retrieve(
                text, filters, self.top_k, source_version_ids,
            ):
                merged.setdefault(evidence.evidence_id, evidence)
                if len(merged) >= self.top_k:
                    return list(merged.values())
        return list(merged.values())

    def _merge_package(
        self,
        run_id: str,
        evidences: list[m.Evidence],
        metrics: list[m.Metric],
        predecessors: list[m.Summary],
        facts: list[m.Fact],
        source_version_ids: list[str],
    ) -> tuple[list[m.Evidence], list[m.Metric]]:
        """Merge retrieved items with everything referenced inside the package."""
        pack = self._packs.setdefault(run_id, {"evidences": {}, "metrics": {}})
        for evidence in evidences:
            pack["evidences"][evidence.evidence_id] = evidence
        for metric in metrics:
            pack["metrics"][metric.metric_id] = metric
        self._resolve_metric_sources(run_id, metrics, source_version_ids)
        wanted_evidence = {item.evidence_id for item in evidences}
        wanted_metric = {item.metric_id for item in metrics}
        for metric in metrics:
            # Metric sources and formula inputs must resolve inside the package.
            wanted_evidence.update(metric.source_evidence_ids)
            wanted_metric.update(metric.input_metric_ids)
        for fact in [*predecessors, *facts]:
            wanted_evidence.update(fact.evidence_ids)
            wanted_metric.update(fact.metric_ids)
        missing = wanted_evidence.difference(pack["evidences"])
        missing_metric = wanted_metric.difference(pack["metrics"])
        if missing or missing_metric:
            raise _error(
                "INVALID_CHAPTER_INPUT",
                "章节证据包存在未解析的证据或指标引用。",
                evidence_ids=sorted(missing), metric_ids=sorted(missing_metric),
            )
        merged_evidence = [pack["evidences"][key] for key in sorted(wanted_evidence)]
        merged_metric = [pack["metrics"][key] for key in sorted(wanted_metric)]
        return merged_evidence, merged_metric

    def _resolve_metric_sources(
        self, run_id: str, metrics: list[m.Metric], source_version_ids: list[str]
    ) -> None:
        """Fetch evidence objects that metrics reference but retrieval missed."""
        pack = self._packs.setdefault(run_id, {"evidences": {}, "metrics": {}})
        referenced = {
            evidence_id
            for metric in metrics
            for evidence_id in metric.source_evidence_ids
        }
        unresolved = referenced.difference(pack["evidences"])
        if not unresolved:
            return
        resolver = getattr(self.knowledge, "get_evidences", None)
        if resolver is None:
            raise _error(
                "INVALID_CHAPTER_INPUT",
                "知识库不支持按 ID 解析指标来源证据。",
                evidence_ids=sorted(unresolved),
            )
        for evidence in resolver(sorted(unresolved), source_version_ids):
            pack["evidences"][evidence.evidence_id] = evidence
        still_missing = referenced.difference(pack["evidences"])
        if still_missing:
            raise _error(
                "INVALID_CHAPTER_INPUT",
                "指标来源证据不在冻结资料范围内。",
                evidence_ids=sorted(still_missing),
            )

    def _persist_pack(self, job: JobRecord) -> None:
        pack = self._packs.get(job.run_id)
        if pack is None:
            return
        context = {
            "evidences": {
                key: item.model_dump(mode="json", by_alias=True)
                for key, item in pack["evidences"].items()
            },
            "metrics": {
                key: item.model_dump(mode="json", by_alias=True)
                for key, item in pack["metrics"].items()
            },
        }
        self.store.update_job(job.job_id, context=context, now=self.clock())

    def _load_pack(self, job: JobRecord) -> dict:
        pack = self._packs.setdefault(job.run_id, {"evidences": {}, "metrics": {}})
        if job.context and not pack["evidences"] and not pack["metrics"]:
            for key, payload in job.context.get("evidences", {}).items():
                pack["evidences"][key] = m.Evidence.model_validate(payload)
            for key, payload in job.context.get("metrics", {}).items():
                pack["metrics"][key] = m.Metric.model_validate(payload)
        return pack

    # ------------------------------------------------------------------
    # Worker pass
    # ------------------------------------------------------------------
    def run_cycle(self) -> dict:
        """One worker pass: recovery, ingest processing, one job, one export."""
        now = self.clock()
        lost = self.store.fail_stale_jobs(now - WORKER_LOST_AFTER, now)
        for job_id in lost:
            logger.warning("job %s marked WORKER_LOST", job_id)
        result = {"job": None, "export": None, "ingest": 0, "lost": lost}
        result["ingest"] = self._process_ingests()
        job = self.store.claim_next_queued(now)
        if job is not None:
            result["job"] = job.job_id
            self._execute_job(job)
        export = self.store.claim_next_export(self.clock())
        if export is not None:
            result["export"] = export.export_id
            self._execute_export(export)
        return result

    def _process_ingests(self) -> int:
        if self.ingest_runner is None:
            return 0
        processed = 0
        for version in self.store.queued_ingest_version_ids():
            try:
                if self.ingest_runner(version):
                    processed += 1
            except ServiceError as exc:
                logger.error("ingest %s failed: %s", version, exc.error.code)
        return processed

    # ------------------------------------------------------------------
    # Job lifecycle
    # ------------------------------------------------------------------
    def _execute_job(self, claimed: JobRecord) -> None:
        job_id = claimed.job_id
        self.store.heartbeat_job(job_id, self.clock())
        self._load_pack(claimed)
        if claimed.frozen is None:
            planned = self._plan_job(claimed)
            if planned is None:
                return
            frozen, outline = planned
        else:
            frozen = claimed.frozen
            outline = self._outline(frozen)
            self._reset_for_retry(claimed)
        tasks = {task.chapter_id: task for task in outline.chapters}
        failed: dict[str, m.ErrorInfo] = {}
        while True:
            job = self.store.get_job(job_id)
            assert job is not None
            if job.status != "RUNNING":
                return  # cancelled mid-flight; keep succeeded chapters
            succeeded = {
                record.chapter_id for record in job.chapters
                if record.status == "SUCCEEDED"
            }
            ready = [
                record for record in job.chapters
                if record.status in ("PENDING", "WAITING_DEPENDENCY")
                and set(tasks[record.chapter_id].depends_on) <= succeeded
            ]
            if not ready:
                break
            ready.sort(key=lambda record: tasks[record.chapter_id].order)
            for record in ready:
                if self.store.get_job(job_id).status != "RUNNING":
                    return
                frozen = self._run_chapter(job_id, frozen, tasks, record.chapter_id, failed)
        self._finalize_job(job_id, frozen, tasks, failed)

    def _outline(self, frozen: m.FrozenRun) -> m.Outline:
        template = self.templates.get_template(
            frozen.template_ref.template_id, frozen.template_ref.version,
        )
        return self.generation.plan_outline(frozen.spec, template)

    def _plan_job(self, claimed: JobRecord) -> tuple[m.FrozenRun, m.Outline] | None:
        job_id = claimed.job_id
        now = self.clock()
        report = self.store.get_report(claimed.report_id)
        assert report is not None
        template = self.templates.get_template(report.template_id, report.template_version)
        try:
            outline = self.generation.plan_outline(claimed.spec, template)
        except ServiceError as exc:
            self.store.update_job(
                job_id, status="FAILED", stage=None, error=exc.error, now=now,
            )
            self.store.set_report_job(claimed.report_id, job_id, "FAILED", now)
            return None
        frozen = m.FrozenRun(
            run_id=claimed.run_id, spec=claimed.spec, template_ref=m.TemplateRef(
                template_id=template.template_id, version=template.version,
            ),
            source_version_ids=self.store.ready_version_ids(),
            facts_version="f0", facts=[],
        )
        chapters = [
            ChapterRecord(
                chapter_id=task.chapter_id,
                status="PENDING" if not task.depends_on else "WAITING_DEPENDENCY",
            )
            for task in sorted(outline.chapters, key=lambda item: item.order)
        ]
        self.store.update_job(
            job_id, frozen=frozen, stage="GENERATING", now=self.clock(),
        )
        self.store.replace_chapters(job_id, chapters)
        return frozen, outline

    def _reset_for_retry(self, claimed: JobRecord) -> None:
        """Re-queue failed and skipped chapters; keep verified results."""
        for record in claimed.chapters:
            if record.status in ("FAILED", "RUNNING"):
                self.store.update_chapter(
                    claimed.job_id, record.chapter_id, status="PENDING", error=None,
                )
            elif record.status == "SKIPPED":
                self.store.update_chapter(
                    claimed.job_id, record.chapter_id,
                    status="WAITING_DEPENDENCY", error=None,
                )
        self.store.update_job(
            claimed.job_id, status="RUNNING", stage="GENERATING", error=None,
            now=self.clock(),
        )
        self.store.heartbeat_job(claimed.job_id, self.clock())

    def _run_chapter(
        self,
        job_id: str,
        frozen: m.FrozenRun,
        tasks: dict[str, m.Task],
        chapter_id: str,
        failed: dict[str, m.ErrorInfo],
    ) -> m.FrozenRun:
        task = tasks[chapter_id]
        attempt_id = new_id()
        self.store.update_chapter(
            job_id, chapter_id, status="RUNNING", attempt_id=attempt_id,
        )
        self.store.heartbeat_job(job_id, self.clock())
        transient = 0
        format_retries = 0
        attempt = 0
        while True:
            attempt += 1
            try:
                content = self._generate_and_check(job_id, frozen, task)
            except ServiceError as exc:
                error = exc.error
                if not error.retryable or attempt >= MAX_ATTEMPTS:
                    self._chapter_failed(job_id, tasks, chapter_id, failed, error)
                    return frozen
                if error.code in FORMAT_CODES:
                    if format_retries >= FORMAT_EXTRA:
                        self._chapter_failed(job_id, tasks, chapter_id, failed, error)
                        return frozen
                    format_retries += 1
                else:
                    if transient >= TRANSIENT_EXTRA:
                        self._chapter_failed(job_id, tasks, chapter_id, failed, error)
                        return frozen
                    transient += 1
                self.store.update_chapter(job_id, chapter_id, retries=attempt)
                self.sleeper(min(2.0 ** attempt, 5.0))
                continue
            except Exception as exc:  # infrastructure bug: fail fast, keep the rest
                logger.exception("chapter %s crashed", chapter_id)
                self._chapter_failed(
                    job_id, tasks, chapter_id, failed,
                    m.ErrorInfo(code="ORCHESTRATION_ERROR", message=str(exc),
                                retryable=False, details={}),
                )
                return frozen
            self.store.update_chapter(
                job_id, chapter_id, status="SUCCEEDED", content=content, error=None,
            )
            return self._absorb_facts(job_id, frozen, content)

    def _chapter_failed(
        self,
        job_id: str,
        tasks: dict[str, m.Task],
        chapter_id: str,
        failed: dict[str, m.ErrorInfo],
        error: m.ErrorInfo,
    ) -> None:
        failed[chapter_id] = error
        self.store.update_chapter(job_id, chapter_id, status="FAILED", error=error)
        self._skip_dependents(job_id, tasks, chapter_id, error)

    def _skip_dependents(
        self,
        job_id: str,
        tasks: dict[str, m.Task],
        chapter_id: str,
        error: m.ErrorInfo,
    ) -> None:
        changed = True
        skipped = {chapter_id}
        while changed:
            changed = False
            job = self.store.get_job(job_id)
            assert job is not None
            for record in job.chapters:
                if record.chapter_id in skipped:
                    continue
                deps = set(tasks[record.chapter_id].depends_on)
                if deps & skipped:
                    self.store.update_chapter(
                        job_id, record.chapter_id, status="SKIPPED",
                        error=m.ErrorInfo(
                            code="DEPENDENCY_FAILED",
                            message=f"依赖的章节「{chapter_id}」未成功，本章已跳过。",
                            retryable=False,
                            details={"failed_chapter_ids": sorted(deps & skipped)},
                        ),
                    )
                    skipped.add(record.chapter_id)
                    changed = True

    def _generate_and_check(
        self, job_id: str, frozen: m.FrozenRun, task: m.Task
    ) -> m.Chapter:
        job = self.store.get_job(job_id)
        assert job is not None
        self._reserve_budget(job_id, task.chapter_id)
        chapters = {record.chapter_id: record for record in job.chapters}
        predecessors = [
            summarize_chapter(chapters[dep].content)
            for dep in task.depends_on
            if dep in chapters and chapters[dep].content is not None
        ]
        context = self.prepare_chapter_context(task, frozen, predecessors)
        if task.depends_on:
            chapter = self.generation.generate_synthesis(context)
        else:
            chapter = self.generation.generate_chapter(context)
        quality = self.quality.validate_chapter(chapter, context.evidences, context.metrics)
        blocking = [
            issue.issue_id for issue in quality.issues if issue.severity == "BLOCK"
        ]
        if blocking:
            raise _error(
                "QUALITY_BLOCK", "章节质量检查存在 BLOCK 问题。", issues=blocking,
            )
        charts = self.quality.build_charts(
            chapter.chapter_id, context.metrics, chapter.chart_requests,
        )
        blocking = [
            issue.issue_id for issue in charts.issues if issue.severity == "BLOCK"
        ]
        if blocking:
            raise _error(
                "QUALITY_BLOCK", "图表数据存在 BLOCK 问题。", issues=blocking,
            )
        self._render_assets(frozen.run_id, charts.charts)
        return chapter

    def _render_assets(self, run_id: str, charts: list[m.Chart]) -> None:
        assets = self._assets.setdefault(run_id, {})
        for chart in charts:
            if chart.asset_id not in assets:
                assets[chart.asset_id] = render_chart_png(chart)

    def _absorb_facts(
        self, job_id: str, frozen: m.FrozenRun, chapter: m.Chapter
    ) -> m.FrozenRun:
        verified = [
            fact for fact in chapter.fact_candidates
            if fact.evidence_ids or fact.metric_ids
        ]
        if verified:
            frozen = frozen.model_copy(update={
                "facts": [*frozen.facts, *verified],
                "facts_version": f"f{len(frozen.facts) + len(verified)}",
            })
            self.store.update_job(job_id, frozen=frozen, now=self.clock())
        job = self.store.get_job(job_id)
        assert job is not None
        self._persist_pack(job)
        return frozen

    # ------------------------------------------------------------------
    # Version snapshot
    # ------------------------------------------------------------------
    def _finalize_job(
        self,
        job_id: str,
        frozen: m.FrozenRun,
        tasks: dict[str, m.Task],
        failed: dict[str, m.ErrorInfo],
    ) -> None:
        job = self.store.get_job(job_id)
        assert job is not None
        now = self.clock()
        required_failed = [
            chapter_id for chapter_id, error in failed.items()
            if tasks[chapter_id].required
        ]
        if required_failed:
            self.store.update_job(
                job_id, status="FAILED", stage=None,
                error=failed[required_failed[0]], now=now,
            )
            self.store.set_report_job(job.report_id, job_id, "FAILED", now)
            return
        # Chapters that never became ready (e.g. their dependencies failed).
        for record in job.chapters:
            if record.status in ("WAITING_DEPENDENCY", "RUNNING", "PENDING"):
                self.store.update_chapter(
                    job_id, record.chapter_id, status="SKIPPED",
                    error=m.ErrorInfo(
                        code="DEPENDENCY_FAILED", message="前置章节未成功，本章已跳过。",
                        retryable=False, details={},
                    ),
                )
        self._save_snapshot(job, frozen, failed=bool(failed))

    def _save_snapshot(
        self, job: JobRecord, frozen: m.FrozenRun, *, failed: bool
    ) -> None:
        now = self.clock()
        template = self.templates.get_template(
            frozen.template_ref.template_id, frozen.template_ref.version,
        )
        pack = self._load_pack(job)
        outline = self._outline(frozen)
        chapters = [
            record.content for record in job.chapters if record.content is not None
        ]
        metrics = sorted(pack["metrics"].values(), key=lambda item: item.metric_id)
        charts: list[m.Chart] = []
        chart_issues: list[m.Issue] = []
        for chapter in chapters:
            result = self.quality.build_charts(
                chapter.chapter_id, metrics, chapter.chart_requests,
            )
            charts.extend(result.charts)
            chart_issues.extend(result.issues)
        document = m.ReportDocument(
            run_id=job.run_id, spec=frozen.spec, template_ref=frozen.template_ref,
            source_version_ids=frozen.source_version_ids,
            facts_version=frozen.facts_version, facts=frozen.facts,
            version_id=new_id(), parent_version_id=None, created_at=now,
            stale_chapter_ids=[], exportable=False,
            report_id=job.report_id, schema_version="1.0",
            layout_version=template.layout_version, model_id=self.model_id,
            outline=outline, chapters=chapters,
            evidences=sorted(pack["evidences"].values(), key=lambda item: item.evidence_id),
            metrics=metrics, charts=charts, quality=m.QualitySummary(rules=[], issues=[]),
        )
        quality = self.quality.validate_report(document)
        known = {issue.issue_id for issue in quality.issues}
        quality.issues.extend(
            issue for issue in chart_issues if issue.issue_id not in known
        )
        document.quality = quality
        blocked = any(issue.severity == "BLOCK" for issue in quality.issues)
        if blocked:
            self._fail_with_draft(job, document, now)
            return
        try:
            self._write_assets(document)
            present = {chapter.chapter_id for chapter in document.chapters}
            not_checked = any(
                rule.required and rule.result == "NOT_CHECKED"
                for rule in quality.rules
            )
            document.exportable = (
                not document.stale_chapter_ids
                and all(task.chapter_id in present for task in outline.chapters if task.required)
                and not not_checked
            )
            check_snapshot(document)
        except (ValueError, OSError) as exc:
            logger.exception("snapshot for job %s failed validation", job.job_id)
            self.store.save_version(document, make_current=False, now=now)
            self.store.update_job(
                job.job_id, status="FAILED", stage=None,
                error=m.ErrorInfo(
                    code="INVALID_SNAPSHOT", message="报告快照未通过一致性检查。",
                    retryable=False, details={"reason": str(exc)},
                ),
                now=now,
            )
            self.store.set_report_job(job.report_id, job.job_id, "FAILED", now)
            return
        self.store.save_version(document, make_current=True, now=now)
        status = "COMPLETED"
        if quality.issues or failed or any(
            record.status == "SKIPPED" for record in job.chapters
        ):
            status = "COMPLETED_WITH_WARNINGS"
        self.store.set_report_version(job.report_id, document.version_id, now)
        self.store.update_job(
            job.job_id, status=status, stage=None,
            result_version_id=document.version_id, now=self.clock(),
        )
        self.store.set_report_job(job.report_id, job.job_id, status, self.clock())

    def _fail_with_draft(self, job: JobRecord, document: m.ReportDocument, now: datetime) -> None:
        self.store.save_version(document, make_current=False, now=now)
        self.store.update_job(
            job.job_id, status="FAILED", stage=None,
            error=m.ErrorInfo(
                code="QUALITY_BLOCK", message="全文质量检查存在 BLOCK 问题，已保留草稿。",
                retryable=False, details={},
            ),
            now=now,
        )
        self.store.set_report_job(job.report_id, job.job_id, "FAILED", now)

    def _write_assets(self, document: m.ReportDocument) -> None:
        """Persist chart PNGs under the frozen version so exports can resolve them."""
        assets = self._assets.get(document.run_id, {})
        directory = self.runtime_root / "assets" / document.version_id
        directory.mkdir(parents=True, exist_ok=True)
        for chart in document.charts:
            payload = assets.get(chart.asset_id)
            if payload is None:
                payload = render_chart_png(chart)
            (directory / f"{chart.asset_id}.png").write_bytes(payload)

    def _reserve_budget(self, job_id: str, chapter_id: str) -> None:
        job = self.store.get_job(job_id)
        assert job is not None
        projected = job.estimated_cost_cny + self.per_call_reserve_cny
        if projected > self.budget_cny:
            raise _error(
                "BUDGET_LIMIT",
                "预计调用费用超过剩余预算，已停止模型调用。",
                budget_cny=str(self.budget_cny),
            )
        self.store.update_job(
            job_id, estimated_cost_cny=projected, now=self.clock(),
        )

    # ------------------------------------------------------------------
    # Retry / cancel
    # ------------------------------------------------------------------
    def retry_job(self, job_id: str, idempotency_key: str) -> m.JobAccepted:
        source = self.store.get_job(job_id)
        if source is None:
            raise _resource_not_found("任务")
        if source.status != "FAILED":
            raise _error(
                "INVALID_JOB_STATE", "只能重试失败的任务。",
                current_status=source.status,
            )
        guard = IdempotencyRequest(
            operation="job_retry", target_id=job_id, key=idempotency_key,
            hash=request_hash({"job_id": job_id}),
        )
        now = self.clock()
        new_job_id = new_id()
        accepted = {
            "job_id": new_job_id, "report_id": source.report_id,
            "previous_job_id": job_id, "status_url": JOB_URL.format(new_job_id),
        }
        chapters: list[ChapterRecord] = []
        for record in source.chapters:
            state = record.status
            if state in ("FAILED", "RUNNING"):
                state = "PENDING"
            elif state == "SKIPPED":
                state = "WAITING_DEPENDENCY"
            chapters.append(ChapterRecord(
                chapter_id=record.chapter_id, status=state,
                attempt_id=record.attempt_id if state == "SUCCEEDED" else None,
                retries=record.retries if state == "SUCCEEDED" else 0,
                content=record.content, error=None,
            ))
        retry = JobRecord(
            job_id=new_job_id, report_id=source.report_id, run_id=source.run_id,
            previous_job_id=job_id, spec=source.spec, frozen=source.frozen,
            context=source.context, chapters=chapters,
        )
        response = self.store.begin_retry(guard, retry, accepted, now)
        self.store.set_report_job(source.report_id, new_job_id, "QUEUED", self.clock())
        return m.JobAccepted.model_validate(response)

    def cancel_job(self, job_id: str) -> m.JobDetail:
        job = self.store.get_job(job_id)
        if job is None:
            raise _resource_not_found("任务")
        if job.status == "CANCELLED":
            return self.job_detail(job)
        cancelled = self.store.cancel_job(job_id, self.clock())
        if cancelled is None:
            raise _error(
                "INVALID_JOB_STATE", "任务已结束，不能取消。",
                current_status=job.status,
            )
        return self.job_detail(cancelled)

    # ------------------------------------------------------------------
    # Exports
    # ------------------------------------------------------------------
    def create_export(
        self, report_id: str, version_id: str, format: str, idempotency_key: str
    ) -> m.ExportAccepted:
        document = self.store.get_version(report_id, version_id)
        if document is None:
            raise _resource_not_found("报告版本")
        if not document.exportable:
            raise _error(
                "REPORT_NOT_EXPORTABLE",
                "该版本暂不可正式导出：存在未完成章节、过期章节或 BLOCK 问题。",
                current_version_id=version_id,
            )
        guard = IdempotencyRequest(
            operation="export_create", target_id=version_id, key=idempotency_key,
            hash=request_hash({"format": format}), report_id=report_id,
        )
        now = self.clock()
        export_id = new_id()
        accepted = {"export_id": export_id, "status_url": EXPORT_URL.format(export_id)}
        record = ExportRecord(
            export_id=export_id, report_id=report_id, version_id=version_id, format=format,
        )
        response = self.store.begin_export(guard, record, accepted, now)
        return m.ExportAccepted.model_validate(response)

    def _execute_export(self, export: ExportRecord) -> None:
        document = self.store.get_version(export.report_id, export.version_id)
        if document is None:
            self.store.update_export(
                export.export_id, status="FAILED",
                error=m.ErrorInfo(
                    code="RESOURCE_NOT_FOUND", message="报告版本不存在。",
                    retryable=False, details={},
                ),
                now=self.clock(),
            )
            return
        try:
            artifact = self.export.render_report(document, export.format)
        except ServiceError as exc:
            self.store.update_export(
                export.export_id, status="FAILED", error=exc.error, now=self.clock(),
            )
            return
        self.store.update_export(
            export.export_id, status="READY", storage_key=artifact.storage_key,
            checksum=artifact.sha256, size_bytes=artifact.size_bytes, now=self.clock(),
        )

    def export_file(self, export_id: str) -> tuple[Path, str, str]:
        export = self.store.get_export(export_id)
        if export is None:
            raise _resource_not_found("导出任务")
        if export.status != "READY" or export.storage_key is None:
            raise _error(
                "EXPORT_NOT_READY", "导出尚未完成，不能下载。",
                current_status=export.status,
            )
        path = (self.runtime_root / export.storage_key).resolve()
        if not path.is_relative_to(self.runtime_root.resolve()) or not path.is_file():
            raise _error(
                "EXPORT_FAILED", "导出文件缺失，请更换幂等键重新导出。",
                retryable=False,
            )
        media = (
            "application/pdf" if export.format == "pdf"
            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        filename = f"report-{export.version_id}.{export.format}"
        return path, media, filename

    # ------------------------------------------------------------------
    # Read models used by the HTTP layer
    # ------------------------------------------------------------------
    def job_detail(self, job: JobRecord) -> m.JobDetail:
        return m.JobDetail(
            job_id=job.job_id, report_id=job.report_id, run_id=job.run_id,
            status=job.status, stage=job.stage,
            chapters=[
                m.ChapterRun(
                    chapter_id=chapter.chapter_id, status=chapter.status,
                    attempt_id=chapter.attempt_id, retries=chapter.retries,
                    error=chapter.error,
                )
                for chapter in job.chapters
            ],
            result_version_id=job.result_version_id,
            updated_at=job.updated_at or self.clock(),
            estimated_cost_cny=str(job.estimated_cost_cny),
            error=job.error,
        )

    def preview(self, job_id: str, chapter_id: str) -> m.Preview:
        job = self.store.get_job(job_id)
        if job is None:
            raise _resource_not_found("任务")
        for chapter in job.chapters:
            if chapter.chapter_id == chapter_id:
                return m.Preview(status=chapter.status, content=chapter.content)
        raise _resource_not_found("章节")

    def report_summary(self, report: ReportRecord) -> m.ReportSummary:
        return m.ReportSummary(
            report_id=report.report_id, title=report.title,
            report_type=report.report_type,
            current_version_id=report.current_version_id,
            latest_job_id=report.latest_job_id or report.report_id,
            status=report.job_status,
        )
