"""MemoryStore behaviour: atomic claims, immutability, idempotency guards."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from backend.app.contracts import models as m
from backend.app.contracts.services import ServiceError
from backend.app.database.store import (
    ChapterRecord,
    ExportRecord,
    IdempotencyRequest,
    JobRecord,
    MemoryStore,
    ReportRecord,
    request_hash,
)

_UTC = timezone.utc
_NOW = datetime(2026, 11, 28, 9, 0, 0, tzinfo=_UTC)


def _report(report_id: str = "rep1") -> ReportRecord:
    return ReportRecord(
        report_id=report_id, title="测试报告", report_type="industry_analysis",
        template_id="industry_analysis_v1", template_version=1,
    )


def _job(job_id: str = "job1", report_id: str = "rep1", **kwargs) -> JobRecord:
    return JobRecord(job_id=job_id, report_id=report_id, run_id="run1", **kwargs)


def _document(report_id: str = "rep1", version_id: str = "ver1") -> m.ReportDocument:
    fixture = __import__("json").loads(
        __import__("pathlib").Path("shared/contracts/fixtures/report_document.json")
        .read_text(encoding="utf-8"),
    )
    fixture["report_id"] = report_id
    fixture["version_id"] = version_id
    return m.ReportDocument.model_validate(fixture)


class ReportAndJobTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()

    def test_reports_are_listed_newest_first_with_paging(self):
        for index in range(3):
            self.store.create_report(_report(f"rep{index}"), _NOW + timedelta(minutes=index))
        total, page_one = self.store.list_reports(1, 2)
        self.assertEqual(total, 3)
        self.assertEqual([report.report_id for report in page_one], ["rep2", "rep1"])
        total, page_two = self.store.list_reports(2, 2)
        self.assertEqual([report.report_id for report in page_two], ["rep0"])

    def test_claim_is_atomic_and_sets_running_stage(self):
        self.store.create_report(_report(), _NOW)
        self.store.create_job(_job(), _NOW)
        claimed = self.store.claim_next_queued(_NOW)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.status, "RUNNING")
        self.assertEqual(claimed.stage, "PLANNING")
        self.assertIsNone(self.store.claim_next_queued(_NOW))

    def test_fail_stale_jobs_only_touches_old_running_jobs(self):
        self.store.create_report(_report(), _NOW)
        self.store.create_job(_job("job-old"), _NOW)
        self.store.claim_next_queued(_NOW)  # claims job-old first
        self.store.create_job(_job("job-fresh", status="RUNNING"), _NOW)
        self.store.heartbeat_job("job-fresh", _NOW)
        # Simulate a dead worker: the claimed job stopped heartbeating long ago.
        self.store.heartbeat_job("job-old", _NOW - timedelta(seconds=200))
        cutoff = _NOW - timedelta(seconds=90)
        lost = self.store.fail_stale_jobs(cutoff, _NOW)
        self.assertEqual(lost, ["job-old"])
        self.assertEqual(self.store.get_job("job-old").status, "FAILED")
        self.assertEqual(self.store.get_job("job-old").error.code, "WORKER_LOST")
        self.assertEqual(self.store.get_job("job-fresh").status, "RUNNING")

    def test_cancel_rejects_terminal_jobs(self):
        self.store.create_report(_report(), _NOW)
        self.store.create_job(_job(), _NOW)
        self.assertIsNotNone(self.store.cancel_job("job1", _NOW))
        self.assertIsNone(self.store.cancel_job("job1", _NOW))

    def test_replace_chapters_and_update_chapter(self):
        self.store.create_report(_report(), _NOW)
        self.store.create_job(_job(), _NOW)
        chapters = [
            ChapterRecord(chapter_id="a", status="PENDING"),
            ChapterRecord(chapter_id="b", status="WAITING_DEPENDENCY"),
        ]
        self.store.replace_chapters("job1", chapters)
        self.store.update_chapter("job1", "b", status="SUCCEEDED")
        job = self.store.get_job("job1")
        self.assertEqual(
            [chapter.status for chapter in job.chapters], ["PENDING", "SUCCEEDED"],
        )
        with self.assertRaises(KeyError):
            self.store.update_chapter("job1", "missing", status="FAILED")


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.store.create_report(_report(), _NOW)

    def test_saved_versions_are_immutable_copies(self):
        document = _document()
        self.store.save_version(document, make_current=True, now=_NOW)
        document.exportable = False
        document.stale_chapter_ids = ["chapter_market"]
        fetched = self.store.get_version("rep1", "ver1")
        self.assertTrue(fetched.exportable)
        self.assertEqual(fetched.stale_chapter_ids, [])
        self.assertEqual(self.store.get_report("rep1").current_version_id, "ver1")

    def test_version_listing_is_ordered_and_paged(self):
        for index in range(3):
            self.store.save_version(_document(version_id=f"ver{index}"),
                                    make_current=False, now=_NOW + timedelta(minutes=index))
        total, versions = self.store.list_versions("rep1", 1, 2)
        self.assertEqual(total, 3)
        self.assertEqual([version.version_id for version in versions],
                         ["ver2", "ver1"])
        self.assertEqual(self.store.list_versions("rep2", 1, 10)[0], 0)

    def test_version_lookup_requires_matching_report(self):
        self.store.save_version(_document(), make_current=True, now=_NOW)
        self.assertIsNone(self.store.get_version("rep-other", "ver1"))


class IdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.guard = IdempotencyRequest(
            operation="report_create", target_id="", key="key-1",
            hash=request_hash({"a": 1}),
        )
        self.accepted = {"job_id": "job1", "status_url": "/api/v1/jobs/job1"}

    def test_begin_report_creates_once_and_replays(self):
        first = self.store.begin_report(self.guard, _report(), _job(), self.accepted, _NOW)
        self.assertEqual(first, self.accepted)
        replay = self.store.begin_report(self.guard, _report("rep2"), _job("job2"),
                                         {"job_id": "job2"}, _NOW)
        self.assertEqual(replay, self.accepted)
        self.assertIsNone(self.store.get_report("rep2"))
        self.assertIsNone(self.store.get_job("job2"))

    def test_conflicting_hash_is_rejected(self):
        self.store.begin_report(self.guard, _report(), _job(), self.accepted, _NOW)
        conflict = IdempotencyRequest(
            operation="report_create", target_id="", key="key-1", hash="different",
        )
        with self.assertRaises(ServiceError) as error:
            self.store.begin_report(conflict, _report("rep3"), _job("job3"),
                                    {"job_id": "job3"}, _NOW)
        self.assertEqual(error.exception.error.code, "IDEMPOTENCY_CONFLICT")

    def test_begin_retry_and_exports_share_the_guard(self):
        retry_guard = IdempotencyRequest(
            operation="job_retry", target_id="job-old", key="r1",
            hash=request_hash({"job_id": "job-old"}),
        )
        accepted = self.store.begin_retry(retry_guard, _job("job-new"), {"job_id": "job-new"}, _NOW)
        self.assertEqual(accepted, {"job_id": "job-new"})
        export_guard = IdempotencyRequest(
            operation="export_create", target_id="ver1", key="e1", hash="h",
        )
        export = self.store.begin_export(
            export_guard,
            ExportRecord(export_id="exp1", report_id="rep1", version_id="ver1", format="docx"),
            {"export_id": "exp1"}, _NOW,
        )
        self.assertEqual(export, {"export_id": "exp1"})
        self.assertEqual(self.store.get_export("exp1").status, "QUEUED")

    def test_export_claim_is_single_use(self):
        export_guard = IdempotencyRequest(
            operation="export_create", target_id="ver1", key="e1", hash="h",
        )
        self.store.begin_export(
            export_guard,
            ExportRecord(export_id="exp1", report_id="rep1", version_id="ver1", format="docx"),
            {"export_id": "exp1"}, _NOW,
        )
        claimed = self.store.claim_next_export(_NOW)
        self.assertEqual(claimed.export_id, "exp1")
        self.assertEqual(claimed.status, "RUNNING")
        self.assertIsNone(self.store.claim_next_export(_NOW))


class AdminSessionTests(unittest.TestCase):
    def test_sessions_expire(self):
        store = MemoryStore()
        store.create_admin_session("sess", _NOW + timedelta(hours=1))
        self.assertTrue(store.admin_session_valid("sess", _NOW))
        self.assertFalse(store.admin_session_valid("sess", _NOW + timedelta(hours=2)))
        store.delete_admin_session("sess")
        self.assertFalse(store.admin_session_valid("sess", _NOW))


if __name__ == "__main__":
    unittest.main()
