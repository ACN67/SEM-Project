# W5 source acquisition and ingestion verification

Run python scripts/verify_w5_sources.py <external-source-directory> to fetch registered originals, verify existing hashes and inspect expected physical pages. Raw originals remain outside the repository. Each observation records the source URL, date, file hash, expected locator and acquisition outcome.

Run python scripts/import_w5_sources.py <external-source-directory> <runtime-directory> --database <isolated-database> after applying migrations 010, 011 and 012. Set W1_MYSQL_PORT, MYSQL_USER and MYSQL_PASSWORD. This uses KnowledgeService, real BGE and Chroma. The result contains real document version IDs and evidence locators. Only verified original PDFs enter this run. Original spreadsheets require manual cell review and W2-compatible mapping before import. This run does not extract structured business indicators from PDF text.

Acquisition currently located nine PDF evidence records and two spreadsheets. Thirteen records failed download or hash verification. Four downloaded HTML pages require validated frozen PDFs. Both system curl and Edge headless print were attempted for failed government URLs. Browser snapshots did not become available. No fixture DOCX has been promoted to original evidence.

Independent member signoff remains pending. Issues #11, #12 and #13 remain open. R04 planning copies require authoritative source replacement and separate contact evidence. Ordinary customs tariff evidence is still missing. Candidate annual tariff instrument EU 2024/2522 must be checked against R05 research and policy effective dates before adoption. Its EUR-Lex PDF endpoint returned an empty response during this run.

The existing five-case expected values and negative cases remain in data/samples/w5. Source review_status=PASS in that baseline is a prior editorial status and is not a claim that this acquisition run completed formal ingestion for that record. Use acquisition.json and source-results.json for actual runtime outcomes.

One Gotion news snapshot was validated by title and publication date. Empty EUR-Lex printouts were excluded. To recreate a candidate snapshot, print the original URL using Edge to <evidence_key>-snapshot.pdf in the source directory and rerun the acquisition verifier. This gate checks title/date presence and leaves human review pending.

Validation on this W5 branch passed 117 unit tests, 6 contract tests and schema regeneration checks. The original source list and per-case negative scenarios remain intact. This PR is draft while formal five-case knowledge acceptance and independent signoff are incomplete.

KBA original spreadsheet cells were independently read by openpyxl in this run: FZ 10.1 AL461=524219 for Jan.-Dezember 2023 and AL488=380609 for Jan.-Dezember 2024, both under the BEV column heading. spreadsheet-locators.json preserves the cell/header values. These cell checks do not claim W2 metric import.

The real KnowledgeService run returned expected-page evidence for 9 records. Full originals were indexed and filtered by their actual frozen version IDs. Runtime IDs and located excerpts are preserved in source-results.json. Local runtime and originals remain outside Git.
