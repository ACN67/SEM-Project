"""End-to-end orchestration tests on MemoryStore with fixture knowledge."""
from __future__ import annotations

import json
from datetime import timedelta
from io import BytesIO
import unittest

from docx import Document

from backend.app.contracts import models as m
from backend.app.contracts.services import ServiceError
from backend.app.orchestration import FixedWriter
from backend.tests.unit.orchestration_helpers import (
    build_stack,
    fixture_request,
    run_until_terminal,
)


class OrchestrationFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = __import__("tempfile").TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from pathlib import Path

        self.root = Path(self.tmp.name)
        self.stack = build_stack(self.root)
        self.orchestration = self.stack.orchestration
        self.store = self.stack.store

    def _create(self, key: str = "key-1", request: dict | None = None):
        return self.orchestration.schedule_report(
            request or fixture_request("R01"), key,
        )

    def _completed(self):
        accepted = self._create()
        detail = run_until_terminal(self.orchestration, accepted.job_id)
        return accepted, detail

    def test_report_completes_with_versions_assets_and_export(self):
        accepted, detail = self._completed()
        self.assertEqual(detail.status, "COMPLETED", detail.error)
        self.assertIsNotNone(detail.result_version_id)
        self.assertTrue(
            all(chapter.status == "SUCCEEDED" for chapter in detail.chapters)
        )
        document = self.store.get_version(accepted.report_id, detail.result_version_id)
        self.assertIsNotNone(document)
        self.assertTrue(document.exportable)
        self.assertEqual(document.quality.issues, [])
        self.assertTrue(document.charts)
        # Chart PNGs are stored under the frozen version for export and preview.
        for chart in document.charts:
            path = self.root / "assets" / document.version_id / f"{chart.asset_id}.png"
            self.assertTrue(path.is_file(), path)
        # Version index is readable through the store.
        total, versions = self.store.list_versions(accepted.report_id, 1, 20)
        self.assertEqual(total, 1)
        self.assertEqual(versions[0].version_id, document.version_id)

        export = self.orchestration.create_export(
            accepted.report_id, document.version_id, "docx", "export-1",
        )
        run_until_terminal(self.orchestration, accepted.job_id)
        record = self.store.get_export(export.export_id)
        self.assertEqual(record.status, "READY")
        self.assertTrue(record.checksum and record.size_bytes)
        path, media, filename = self.orchestration.export_file(export.export_id)
        document_bytes = path.read_bytes()
        self.assertIn("wordprocessingml", media)
        self.assertTrue(filename.endswith(".docx"))
        opened = Document(BytesIO(document_bytes))
        self.assertTrue(opened.paragraphs)

    def test_idempotent_create_replays_and_rejects_conflicting_bodies(self):
        first = self._create("same-key")
        replay = self._create("same-key")
        self.assertEqual(first.job_id, replay.job_id)
        self.assertEqual(first.report_id, replay.report_id)
        conflicting = dict(fixture_request("R01"))
        conflicting["focus"] = ["其他重点"]
        with self.assertRaises(ServiceError) as error:
            self.orchestration.schedule_report(conflicting, "same-key")
        self.assertEqual(error.exception.error.code, "IDEMPOTENCY_CONFLICT")
        fresh = self._create("other-key")
        self.assertNotEqual(fresh.job_id, first.job_id)

    def test_invalid_requests_are_rejected_before_enqueue(self):
        bad = dict(fixture_request("R01"))
        bad["period"] = {"from": "2025", "to": "2024"}
        with self.assertRaises(ServiceError) as error:
            self._create(request=bad)
        self.assertEqual(error.exception.error.code, "VALIDATION_ERROR")
        self.assertTrue(error.exception.error.details["errors"][0]["path"])
        unknown = dict(fixture_request("R01"))
        unknown["template_id"] = "no_such_template_v1"
        with self.assertRaises(ServiceError) as error:
            self.orchestration.schedule_report(unknown, "key-unknown")
        self.assertEqual(error.exception.error.code, "TEMPLATE_NOT_FOUND")

    def test_cancel_before_and_after_completion(self):
        accepted = self._create()
        detail = self.orchestration.cancel_job(accepted.job_id)
        self.assertEqual(detail.status, "CANCELLED")
        # Already cancelled: returns the current state instead of failing.
        again = self.orchestration.cancel_job(accepted.job_id)
        self.assertEqual(again.status, "CANCELLED")
        self.orchestration.run_cycle()
        job = self.store.get_job(accepted.job_id)
        self.assertEqual(job.status, "CANCELLED")
        # Completed jobs can no longer be cancelled.
        finished = self._create("key-2")
        run_until_terminal(self.orchestration, finished.job_id)
        with self.assertRaises(ServiceError) as error:
            self.orchestration.cancel_job(finished.job_id)
        self.assertEqual(error.exception.error.code, "INVALID_JOB_STATE")

    def test_retry_after_required_chapter_failure(self):
        class FlakyWriter:
            """Fails the market chapter once, then behaves normally."""

            def __init__(self):
                self.base = FixedWriter()
                self.failed_once = False

            def complete(self, instructions: str, context: str) -> str:
                task = json.loads(context)["task"]
                if task["chapter_id"] == "market" and not self.failed_once:
                    self.failed_once = True
                    raise ServiceError(m.ErrorInfo(
                        code="MODEL_UNAVAILABLE",
                        message="模拟不可恢复故障。", retryable=False, details={},
                    ))
                return self.base.complete(instructions, context)

        flaky = build_stack(self.root, writer=FlakyWriter())
        accepted = flaky.orchestration.schedule_report(fixture_request("R01"), "key-flaky")
        detail = run_until_terminal(flaky.orchestration, accepted.job_id)
        self.assertEqual(detail.status, "FAILED")
        statuses = {c.chapter_id: c.status for c in detail.chapters}
        self.assertEqual(statuses["market"], "FAILED")
        self.assertEqual(statuses["trend"], "SKIPPED")
        # Only failed jobs can be retried; a queued job is rejected.
        other = flaky.orchestration.schedule_report(fixture_request("R01"), "key-other")
        with self.assertRaises(ServiceError) as error:
            flaky.orchestration.retry_job(other.job_id, "retry-not-failed")
        self.assertEqual(error.exception.error.code, "INVALID_JOB_STATE")
        retry = flaky.orchestration.retry_job(accepted.job_id, "retry-key")
        self.assertEqual(retry.previous_job_id, accepted.job_id)
        self.assertNotEqual(retry.job_id, accepted.job_id)
        final = run_until_terminal(flaky.orchestration, retry.job_id)
        self.assertEqual(final.status, "COMPLETED", final.error)
        self.assertTrue(all(c.status == "SUCCEEDED" for c in final.chapters))

    def test_worker_lost_recovery_marks_running_jobs_failed(self):
        accepted = self._create()
        claimed = self.store.claim_next_queued(self.stack.clock.now())
        self.assertIsNotNone(claimed)
        # Simulate a dead worker: heartbeat far in the past.
        stale = self.stack.clock.value - timedelta(seconds=200)
        self.store.heartbeat_job(claimed.job_id, stale)
        result = self.orchestration.run_cycle()
        self.assertIn(claimed.job_id, result["lost"])
        job = self.store.get_job(claimed.job_id)
        self.assertEqual(job.status, "FAILED")
        self.assertEqual(job.error.code, "WORKER_LOST")
        retry = self.orchestration.retry_job(accepted.job_id, "lost-retry")
        detail = run_until_terminal(self.orchestration, retry.job_id)
        self.assertEqual(detail.status, "COMPLETED", detail.error)

    def test_budget_limit_stops_model_calls(self):
        from pathlib import Path

        small = build_stack(self.root, budget_cny=__import__("decimal").Decimal("0.15"))
        accepted = small.orchestration.schedule_report(fixture_request("R01"), "key-budget")
        detail = run_until_terminal(small.orchestration, accepted.job_id)
        self.assertEqual(detail.status, "FAILED")
        self.assertEqual(detail.error.code, "BUDGET_LIMIT")

    def test_dependency_chapters_wait_and_inherit(self):
        accepted = self._create()
        detail = run_until_terminal(self.orchestration, accepted.job_id)
        self.assertEqual(detail.status, "COMPLETED", detail.error)
        document = self.store.get_version(
            accepted.report_id, detail.result_version_id,
        )
        by_key = {task.section_key: task.chapter_id for task in document.outline.chapters}
        chapters = {c.chapter_id: c for c in document.chapters}
        synthesis = chapters[by_key["summary"]]
        self.assertTrue(synthesis.summary)
        # The synthesis chapter may only reference evidence from the frozen run.
        evidence_ids = {item.evidence_id for item in document.evidences}
        for block in synthesis.blocks:
            if block.type == "paragraph":
                self.assertTrue(set(block.evidence_ids) <= evidence_ids)


class OrchestrationPreviewTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stack = build_stack(Path(self.tmp.name))

    def test_preview_returns_content_after_success(self):
        accepted = self.stack.orchestration.schedule_report(fixture_request("R01"), "key-p")
        detail = run_until_terminal(self.stack.orchestration, accepted.job_id)
        succeeded = next(c for c in detail.chapters if c.status == "SUCCEEDED")
        preview = self.stack.orchestration.preview(accepted.job_id, succeeded.chapter_id)
        self.assertEqual(preview.status, "SUCCEEDED")
        self.assertEqual(preview.content.chapter_id, succeeded.chapter_id)
        with self.assertRaises(ServiceError):
            self.stack.orchestration.preview(accepted.job_id, "no-such-chapter")

    def test_report_summary_exposes_latest_state(self):
        accepted = self.stack.orchestration.schedule_report(fixture_request("R01"), "key-s")
        run_until_terminal(self.stack.orchestration, accepted.job_id)
        report = self.stack.store.get_report(accepted.report_id)
        summary = self.stack.orchestration.report_summary(report)
        self.assertEqual(summary.status, "COMPLETED")
        self.assertEqual(summary.current_version_id, report.current_version_id)
        self.assertTrue(summary.title)


if __name__ == "__main__":
    unittest.main()

    def test_sources_frozen_before_worker(self):
        accepted=self._create()
        frozen=self.store.get_job(accepted.job_id).frozen
        self.assertIsNotNone(frozen)
        before=list(frozen.source_version_ids)
        self.store.ready_version_ids=lambda:['later-source']
        self.orchestration.run_cycle()
        self.assertEqual(self.store.get_job(accepted.job_id).frozen.source_version_ids,before)

    def test_budget_shared_between_jobs(self):
        from decimal import Decimal
        self.orchestration.budget_cny=Decimal('0.10')
        first=self._create('one');second=self._create('two')
        self.orchestration._reserve_budget(first.job_id,'chapter')
        with self.assertRaises(ServiceError):self.orchestration._reserve_budget(second.job_id,'chapter')
