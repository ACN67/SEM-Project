# W5 acquisition, candidates and incomplete acceptance

Run python scripts/verify_w5_sources.py <external-source-directory> to fetch originals, check registered hashes and read expected pages. Raw files remain outside Git. Failed cache files are reacquired. Download, hash and page checks do not sign off source facts.

Run python scripts/import_w5_sources.py <external-source-directory> <runtime-directory> --database <isolated-database> after applying migrations 010, 011 and 012. Set W1_MYSQL_PORT, MYSQL_USER and MYSQL_PASSWORD. This uses KnowledgeService, real BGE and Chroma. Each outcome is atomically saved and independent sources continue after failures.

PDF evidence is classified as CANDIDATE_MATCH or CANDIDATE_NOT_FOUND using explicit search tokens on the expected page. The matcher may match a sentence that negates a claim. Its output is only a retrieval aid. fact_review remains PENDING for every such record. No automatic fact PASS is emitted.

Two KBA spreadsheets are imported through hash-locked mappings. Their reviewed cells contain 524219 and 380609 BEV registrations in 2023 and 2024. Run python scripts/summarize_w5.py <runtime-directory> to execute QualityService growth calculation, compare independent arithmetic and remove the 2023 baseline before recalculation. The missing-baseline check executes the real quality entry point and verifies no defined同比 result is produced.

Five-case summaries report source_coverage, fact_review, independent_signoff and acceptance separately. All case acceptance results remain INCOMPLETE. Business negative scenarios without completed prerequisites are NOT_RUN. The KBA missing-baseline result is a metric-level check and does not establish R05 policy acceptance.

The source-results.json runtime evidence was obtained from complete originals. In this rework the saved fragments were re-evaluated under candidate-only classification and the KBA quality calculation/negative were rerun. Unchanged embeddings were reused. Acquisition includes failed government downloads and incomplete web snapshots. Empty EUR-Lex printouts were excluded. One Gotion snapshot contains its title and publication date but still requires fact review. R04 sources and contact evidence, R05 ordinary tariff evidence, other structured indicator mappings and independent signoff remain incomplete. Issues #11, #12 and #13 remain open.

121 unit tests and 6 contract tests passed. Regressions explicitly cover invalid cache recovery, empty results and negated candidate text. None of these gives a five-case acceptance PASS.
