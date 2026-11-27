# W4 retrieval acceptance

The real bge-small-zh-v1.5 revision 7999e1d3359715c523056ef9478215996d62a620 was loaded locally. Embeddings are normalized, dimension 512. The full registered SAIC annual report was parsed through W3 and indexed into a Chroma cosine collection.

Two R&D questions returned the physical page 13 excerpt. Two unrelated questions (cooking and planetary motion) returned empty after calibration. The threshold 0.5142417848110199 is the midpoint between the lowest relevant top score and highest unrelated top score in this small calibration set. This is a limited initial calibration, not an accuracy estimate across all five report types. The measured scores and source hash are in result.json.

Reproduce: python -m backend.tests.integration.verify_retrieval <SAIC-original.pdf> <output-dir>.

Use KnowledgeService(connect, runtime_root, EvidenceIndex(index_root)) for the existing public service methods. Worker calls service.pipeline.process(version, labels=..., mapping=...) after file acceptance. Labels are an internal version-level manifest using Filters field names; tabular metrics override their own row dimensions. Apply migrations 010, 011, 012 in order. PROCESSING recovery must be requested only after the backend confirms the previous worker has stopped, using recover_abandoned(version).

MySQL acceptance on isolated port 33307 passed 7 tests through test_retrieval_mysql.py, including inherited original/version checks. Index failure kept the version invisible; retry published READY and returned located evidence within the allowed version and entity. Fake index tests and actual model tests are separate. An additional real-model/MySQL case parsed two versions of the same DOCX and verified that each frozen version returned its own 100/200 amount. HTTP integration requires the backend.

Run database checks with W1_MYSQL_PORT and W4_REAL_MODEL=1 set and python -m unittest discover -s backend/tests/integration -p test_retrieval_mysql.py -q.


## Review rework

The internal version manifest now stores the index fingerprint. An eligible candidate with a mismatched fingerprint or missing vector entry returns INDEX_NOT_READY. Worker may call pipeline.process(version, labels=..., mapping=..., rebuild=True) to rebuild a READY version; it becomes PROCESSING during replacement and only returns READY after validation. Rebuild reads persisted evidence and preserves the stored business labels. It updates only the active index fingerprint and processing state; original parser mappings are not required.

Nine isolated MySQL tests passed with the real model enabled. The real-model test removes indexed entries, checks the explicit error, rebuilds and checks the hit. A second collection fingerprint is simulated with the same injected model to test configuration switching without downloading another model revision. The parser error test verifies that the persisted failure exactly matches the ServiceError returned to the Worker, including retryable=false.

Unit tests cover missing entries, changed fingerprints, stable ranking and database failure conversion. 110 unit tests and 6 contract tests passed.


The final rebuild regression covers both a tagged DOCX and a CSV with a custom header mapping. Calling process(version, rebuild=True) without labels or mapping preserves the evidence, metric payloads, business labels and filtered retrieval result. Both parser entry points are guarded against accidental calls during this regression. The real BGE test also rebuilds without labels and checks the SAIC-filtered result. Nine MySQL integration tests passed with W4_REAL_MODEL=1.

Rebuild requires a previously published knowledge_labels manifest. An initial indexing failure without that manifest returns DOCUMENT_PROCESSING_FAILED, reason=rebuild_requires_published_manifest, retryable=false, before any state change. Worker must use normal ingestion retry with original labels and mapping. A failed rebuild of a previously published version retains its manifest and can be rebuilt again. The new MySQL regression verifies both recovery paths and SAIC-filtered retrieval. 110 unit, 6 contract and 10 real-model-enabled MySQL integration tests passed.
