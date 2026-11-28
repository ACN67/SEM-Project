"""HTTP layer tests via FastAPI TestClient (MemoryStore + fixture knowledge)."""
from __future__ import annotations

from decimal import Decimal
import tempfile
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from backend.app.api.routes import AppServices
from backend.app.config import Settings
from backend.app.main import create_app
from backend.tests.unit.orchestration_helpers import build_stack, fixture_request


class ApiTestBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        components = build_stack(self.root)
        settings = Settings(
            database_url=None, model_base_url=None, model_name=None,
            model_api_key=None, runtime_dir=self.root,
            admin_credential="test-credential", budget_cny=Decimal("100.00"),
            per_call_reserve_cny=Decimal("0.10"), top_k=10,
        )
        services = AppServices(
            store=components.store, catalog=components.catalog,
            knowledge=components.knowledge, orchestration=components.orchestration,
            settings=settings,
        )
        self.stack = services
        self.stack.clock = components.clock
        self.client = TestClient(create_app(settings, services=services, start_worker=False))


class AdminSessionTests(ApiTestBase):
    def test_login_requires_valid_credential(self):
        response = self.client.post(
            "/api/v1/admin/session", json={"credential": "wrong"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTH_REQUIRED")

    def test_session_roundtrip_and_logout(self):
        response = self.client.post(
            "/api/v1/admin/session", json={"credential": "test-credential"},
        )
        self.assertEqual(response.status_code, 204)
        cookie = response.headers.get("set-cookie") or ""
        self.assertIn("sem_admin", cookie)
        self.assertIn("HttpOnly", cookie)
        # Wrong-credential sessions never grant upload rights; the demo
        # knowledge refuses uploads with an explicit service error.
        upload = self.client.post(
            "/api/v1/documents",
            files={"file": ("资料.csv", "id,name\n1,示例\n".encode("utf-8"))},
            data={"metadata": '{"title":"资料","publisher":"机构","published_at":"2026-01-01"}'},
        )
        self.assertIn(upload.status_code, (503, 401))
        logout = self.client.delete("/api/v1/admin/session")
        self.assertEqual(logout.status_code, 204)


class TemplateAndDocumentTests(ApiTestBase):
    def test_template_listing_and_detail(self):
        listing = self.client.get("/api/v1/templates")
        self.assertEqual(listing.status_code, 200)
        body = listing.json()
        self.assertEqual(body["total"], 5)
        self.assertEqual(body["page"], 1)
        template = body["items"][0]
        detail = self.client.get(
            f"/api/v1/templates/{template['template_id']}/versions/{template['version']}",
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["template_id"], template["template_id"])
        missing = self.client.get("/api/v1/templates/none_v1/versions/1")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "TEMPLATE_NOT_FOUND")

    def test_documents_listing_and_missing_document(self):
        body = self.client.get("/api/v1/documents?limit=100").json()
        self.assertEqual(body["total"], len(body["items"]))
        self.assertGreater(body["total"], 0)  # demo documents seeded from the W5 pack
        missing = self.client.get("/api/v1/documents/doc_none")
        self.assertEqual(missing.status_code, 404)


class ReportLifecycleTests(ApiTestBase):
    def _create(self, key: str = "key-1", request: dict | None = None):
        response = self.client.post(
            "/api/v1/reports",
            json=request or fixture_request("R01"),
            headers={"Idempotency-Key": key},
        )
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def _run_to_completion(self, job_id: str) -> dict:
        for _ in range(40):
            self.stack.orchestration.run_cycle()
            body = self.client.get(f"/api/v1/jobs/{job_id}").json()
            if body["status"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "CANCELLED"):
                return body
        raise AssertionError("job did not finish")

    def test_create_requires_idempotency_key_and_valid_payload(self):
        missing = self.client.post("/api/v1/reports", json=fixture_request("R01"))
        self.assertEqual(missing.status_code, 422)
        self.assertEqual(missing.json()["error"]["code"], "VALIDATION_ERROR")
        bad = fixture_request("R01")
        bad["period"] = {"from": "2025", "to": "2024"}
        invalid = self.client.post(
            "/api/v1/reports", json=bad, headers={"Idempotency-Key": "k"},
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertTrue(invalid.json()["error"]["details"]["errors"])
        unknown = fixture_request("R01")
        unknown["template_id"] = "none_v1"
        response = self.client.post(
            "/api/v1/reports", json=unknown, headers={"Idempotency-Key": "k2"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "TEMPLATE_NOT_FOUND")

    def test_idempotent_replay_returns_same_job(self):
        first = self._create("same")
        replay = self._create("same")
        self.assertEqual(first["job_id"], replay["job_id"])

    def test_full_lifecycle_with_polling_preview_and_export(self):
        created = self._create()
        job_id = created["job_id"]
        report_id = created["report_id"]
        self.assertEqual(created["status_url"], f"/api/v1/jobs/{job_id}")

        detail = self._run_to_completion(job_id)
        self.assertEqual(detail["status"], "COMPLETED", detail.get("error"))
        version_id = detail["result_version_id"]
        self.assertTrue(version_id)

        summary = self.client.get(f"/api/v1/reports/{report_id}").json()
        self.assertEqual(summary["current_version_id"], version_id)
        self.assertIn("产业分析", summary["title"])

        listing = self.client.get(
            f"/api/v1/reports/{report_id}/versions",
        ).json()
        self.assertEqual(listing["total"], 1)

        document = self.client.get(
            f"/api/v1/reports/{report_id}/versions/{version_id}",
        ).json()
        self.assertTrue(document["exportable"])
        self.assertEqual(document["schema_version"], "1.0")

        chapter_id = detail["chapters"][0]["chapter_id"]
        preview = self.client.get(f"/api/v1/jobs/{job_id}/chapters/{chapter_id}").json()
        self.assertEqual(preview["status"], "SUCCEEDED")
        self.assertIsNotNone(preview["content"])

        if document["charts"]:
            chart = document["charts"][0]
            asset = self.client.get(
                f"/api/v1/reports/{report_id}/versions/{version_id}/assets/{chart['asset_id']}",
            )
            self.assertEqual(asset.status_code, 200)
            self.assertEqual(asset.headers["content-type"], "image/png")

        export = self.client.post(
            f"/api/v1/reports/{report_id}/versions/{version_id}/exports",
            json={"format": "docx"},
            headers={"Idempotency-Key": "export-1"},
        )
        self.assertEqual(export.status_code, 202)
        # Not run yet: download must be rejected with EXPORT_NOT_READY.
        early = self.client.get(f"/api/v1/exports/{export.json()['export_id']}/download")
        self.assertEqual(early.status_code, 409)
        self.assertEqual(early.json()["error"]["code"], "EXPORT_NOT_READY")

        self.stack.orchestration.run_cycle()
        summary = self.client.get(f"/api/v1/exports/{export.json()['export_id']}").json()
        self.assertEqual(summary["status"], "READY")
        self.assertIn("download_url", summary)
        download = self.client.get(f"/api/v1/exports/{export.json()['export_id']}/download")
        self.assertEqual(download.status_code, 200)
        self.assertIn("wordprocessingml", download.headers["content-type"])

    def test_export_idempotency_replay_and_conflict(self):
        created = self._create()
        detail = self._run_to_completion(created["job_id"])
        version_id = detail["result_version_id"]
        url = f"/api/v1/reports/{created['report_id']}/versions/{version_id}/exports"
        first = self.client.post(
            url, json={"format": "docx"}, headers={"Idempotency-Key": "export-1"},
        )
        self.assertEqual(first.status_code, 202)
        # Same key and body: replay the first accepted response.
        replay = self.client.post(
            url, json={"format": "docx"}, headers={"Idempotency-Key": "export-1"},
        )
        self.assertEqual(replay.status_code, 202)
        self.assertEqual(replay.json()["export_id"], first.json()["export_id"])
        # Same key with a different body: 409 conflict.
        conflicting = self.client.post(
            url, json={"format": "pdf"}, headers={"Idempotency-Key": "export-1"},
        )
        self.assertEqual(conflicting.status_code, 409)
        self.assertEqual(conflicting.json()["error"]["code"], "IDEMPOTENCY_CONFLICT")

    def test_cancel_and_retry_routes(self):
        created = self._create("key-cancel")
        cancelled = self.client.post(f"/api/v1/jobs/{created['job_id']}/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "CANCELLED")
        # A cancelled job is not retryable; only FAILED jobs are.
        retry = self.client.post(
            f"/api/v1/jobs/{created['job_id']}/retry",
            headers={"Idempotency-Key": "retry-1"},
        )
        self.assertEqual(retry.status_code, 409)
        missing = self.client.post("/api/v1/jobs/job_none/cancel")
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
