# K4 PDF acceptance

LibreOffice 26.8.3.1 converted two frozen versions with different paragraphs to four-page PDFs. Both contain Chinese text, a chart, a table, warnings and references. All four V1 pages were rendered and visually checked. The directory has cached chapter names for unattended conversion.

The source is the controlled K3 unit fixture. File SHA-256 and byte counts were recalculated. Two-version Word/PDF export took 37.265 seconds. See result.json.

Reproduce from the repository root using python -m backend.tests.integration.verify_pdf <soffice.com-path> <output-root>.

Unit tests cover same-version concurrent Word requests, missing converter, timeout, successful exit without PDF, nonzero exit, version isolation and staging cleanup. Converter timeouts kill only the conversion process tree on Windows.

HTTP download and backend artifact registration require orchestration integration. This module returns storage_key, sha256 and size_bytes through the existing contract.
