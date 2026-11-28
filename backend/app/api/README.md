# Application verification

Start with `python -m uvicorn backend.app.main:create_app --factory --port 8000`.
Production requires DATABASE_URL, knowledge dependencies/model cache and migrations 010, 011, 012, 020. Reapply idempotent migration 020 to create project_budget on an existing database. Configure MODEL_* for real generation; missing model configuration selects explicitly labeled fixed answers.

Set INGEST_LABELS_JSON to administrator-provided JSON for the current upload batch, for example {"industries":["新能源汽车"],"regions":["中国"]}. Industry currently supports one value per document. Configure labels before processing uploads from a different subject or region; do not remove report query filters. Existing READY records are immutable and are not relabeled by changing this setting. No public request schema was changed.

Review verification (2026-11-28): 158 unit tests and 6 contract tests pass, generated contracts unchanged. Four dedicated MySQL storage tests cover snapshot/nullable-field parity, persistent budget, concurrent idempotency/claims and rollback. API MySQL discovery runs nine tests (eight inherited/re-discovered storage tests plus one actual API lifecycle test). The lifecycle uses actual MySQL, real BGE/Chroma, administrator upload, duplicate and revised uploads, filtered retrieval and source lookup, job creation, immutable version reads, DOCX export and download. Generation is fixed-answer, not real-model acceptance. No paid model calls or real PDF conversion were executed in this rework.

Commands: set W1_MYSQL_PORT to an isolated MySQL port; run unittest discovery under backend/tests/integration with patterns test_orchestration_mysql.py and test_api_mysql.py. The latter requires cached BGE and full knowledge dependencies. Unit/contract discovery and scripts/export_contracts.py --check run from the repository root.
