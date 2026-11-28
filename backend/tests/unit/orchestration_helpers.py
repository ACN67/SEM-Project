"""Shared test wiring for orchestration and API tests.

Builds the backend stack on MemoryStore with the W5 fixture knowledge and the
fixed-answer writer, so tests run without MySQL, model keys or Chroma.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from backend.app.export.service import ExportService
from backend.app.generation import GenerationService
from backend.app.orchestration import (
    DemoKnowledge,
    FixedWriter,
    OrchestrationService,
    seed_demo_documents,
)
from backend.app.orchestration.fixture_knowledge import W5FixtureKnowledge
from backend.app.quality import QualityService
from backend.app.templates.catalog import TemplateCatalog
from backend.app.database.store import MemoryStore

_UTC = timezone.utc


class Clock:
    """Deterministic clock advancing 15 seconds per call (one heartbeat)."""

    def __init__(self) -> None:
        self._now = datetime(2026, 11, 28, 9, 0, 0, tzinfo=_UTC)

    def now(self) -> datetime:
        self._now += timedelta(seconds=15)
        return self._now

    @property
    def value(self) -> datetime:
        return self._now


def build_stack(runtime_root: Path, *, writer=None, knowledge=None,
                budget_cny: Decimal = Decimal("100.00")) -> SimpleNamespace:
    """Assemble store + services + orchestration on the fixture pack."""
    store = MemoryStore()
    catalog = TemplateCatalog()
    fixture = knowledge if knowledge is not None else W5FixtureKnowledge()
    seed_demo_documents(store, fixture)
    generation = GenerationService(writer or FixedWriter())
    quality = QualityService()

    def asset_resolver(version_id: str, asset_id: str) -> bytes:
        return (runtime_root / "assets" / version_id / f"{asset_id}.png").read_bytes()

    export = ExportService(runtime_root, asset_resolver=asset_resolver)
    clock = Clock()
    knowledge_service = DemoKnowledge(fixture)
    orchestration = OrchestrationService(
        store, catalog, knowledge_service, generation, quality, export,
        runtime_root=runtime_root, model_id="test-fixed-answers",
        budget_cny=budget_cny, per_call_reserve_cny=Decimal("0.10"),
        clock=clock.now, sleeper=lambda seconds: None,
    )
    return SimpleNamespace(
        store=store, catalog=catalog, knowledge=knowledge_service,
        orchestration=orchestration, clock=clock,
    )


def run_until_terminal(orchestration: OrchestrationService, job_id: str,
                       max_cycles: int = 40) -> dict:
    """Run worker cycles until the job reaches a terminal state."""
    for _ in range(max_cycles):
        orchestration.run_cycle()
        job = orchestration.store.get_job(job_id)
        if job.status in ("COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "CANCELLED"):
            return orchestration.job_detail(job)
    raise AssertionError(f"job {job_id} did not reach a terminal state")


def fixture_request(case_id: str = "R01") -> dict:
    return W5FixtureKnowledge().cases[case_id]["request"]
