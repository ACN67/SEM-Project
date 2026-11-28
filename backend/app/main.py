"""FastAPI application entry point.

Production wiring needs DATABASE_URL and the MODEL_* variables; without them
the app starts in demo mode: MemoryStore + W5 fixture knowledge + fixed-answer
writer, so the full create→generate→export loop runs with zero configuration.

Run from the repository root (the factory builds services from the process
environment, so pass --factory):
    .venv/Scripts/python.exe -m uvicorn backend.app.main:create_app --factory --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import errors
from .api.routes import (
    AppServices,
    admin_router,
    documents_router,
    exports_router,
    jobs_router,
    reports_router,
    templates_router,
)
from .config import Settings
from .database.store import MemoryStore
from .export.service import ExportService
from .generation import ChatCompletionsClient, GenerationService
from .quality import QualityService
from .templates.catalog import TemplateCatalog

ROUTER_PREFIX = "/api/v1"


def _build_services(settings: Settings) -> AppServices:
    from .orchestration import (
        FIXED_WRITER_MODEL_ID,
        FixedWriter,
        DemoKnowledge,
        OrchestrationService,
        seed_demo_documents,
    )

    runtime_root = settings.runtime_dir
    runtime_root.mkdir(parents=True, exist_ok=True)
    ingest_runner = None

    if settings.database_url:
        from .database.mysql import MySqlStore, connect_from_url
        from .knowledge.index import EvidenceIndex
        from .knowledge.service import KnowledgeService

        connect = connect_from_url(settings.database_url)
        store = MySqlStore(connect)
        def knowledge_connect():
            import pymysql
            connection=connect()
            connection.cursorclass=pymysql.cursors.Cursor
            return connection
        knowledge = KnowledgeService(
            knowledge_connect, runtime_root, EvidenceIndex(runtime_root / "index"),
        )

        def ingest_runner(version: str):
            # Empty labels mean no metadata filters; documents are still
            # indexed and retrievable within their frozen version.
            import json
            import os
            labels=json.loads(os.getenv('INGEST_LABELS_JSON','{}'))
            return knowledge.pipeline.process(version, labels=labels)

    else:
        store = MemoryStore()
        from .orchestration.fixture_knowledge import W5FixtureKnowledge

        fixture = W5FixtureKnowledge()
        seed_demo_documents(store, fixture)
        knowledge = DemoKnowledge(fixture)
    catalog = TemplateCatalog()
    generation = (
        GenerationService(ChatCompletionsClient(
            settings.model_base_url, settings.model_name, settings.model_api_key,
        ))
        if settings.model_configured else GenerationService(FixedWriter())
    )
    model_id = settings.model_name if settings.model_configured else FIXED_WRITER_MODEL_ID

    def asset_resolver(version_id: str, asset_id: str) -> bytes:
        return (runtime_root / "assets" / version_id / f"{asset_id}.png").read_bytes()

    orchestration = OrchestrationService(
        store, catalog, knowledge, generation, QualityService(),
        ExportService(runtime_root, asset_resolver=asset_resolver),
        runtime_root=runtime_root, model_id=model_id, top_k=settings.top_k,
        budget_cny=settings.budget_cny,
        per_call_reserve_cny=settings.per_call_reserve_cny,
        ingest_runner=ingest_runner,
    )
    return AppServices(
        store=store, catalog=catalog, knowledge=knowledge,
        orchestration=orchestration, settings=settings,
    )


def create_app(
    settings: Settings | None = None,
    *,
    services: AppServices | None = None,
    start_worker: bool = True,
) -> FastAPI:
    settings = settings or Settings.from_env()
    if services is None:
        services = _build_services(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker = None
        if start_worker:
            from .orchestration import Worker

            worker = Worker(services.orchestration)
            worker.start()
        yield
        if worker is not None:
            worker.stop()

    app = FastAPI(title="SEM Project", version="1.0.0", lifespan=lifespan)
    app.state.services = services
    errors.install_error_handlers(app)
    for router in (
        admin_router, templates_router, documents_router,
        reports_router, jobs_router, exports_router,
    ):
        app.include_router(router, prefix=ROUTER_PREFIX)
    return app


def main() -> None:
    import uvicorn

    uvicorn.run("backend.app.main:create_app", factory=True, port=8000, reload=False)


if __name__ == "__main__":
    main()
