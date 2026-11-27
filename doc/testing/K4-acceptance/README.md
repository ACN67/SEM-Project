# K4 PDF acceptance

LibreOffice 26.8.3.1 converted two frozen versions with different paragraphs to four-page PDFs. Both contain Chinese text, a chart, a table, warnings and references. All four V1 pages were rendered and visually checked. The directory has cached chapter names for unattended conversion.

The source is the controlled K3 unit fixture. File SHA-256 and byte counts were recalculated. Two-version Word/PDF export took 37.265 seconds. See result.json.

Reproduce from the repository root using python -m backend.tests.integration.verify_pdf <soffice.com-path> <output-root>.

Unit tests cover same-version concurrent Word requests, missing converter, timeout, successful exit without PDF, nonzero exit, version isolation and staging cleanup. Converter timeouts kill only the conversion process tree on Windows.

HTTP download and backend artifact registration require orchestration integration. This module returns storage_key, sha256 and size_bytes through the existing contract.

## Review rework

Missing or corrupt fixed layouts now return EXPORT_FAILED with reason=layout_invalid and retryable=false. The TOC cache is inside its Word field and follows the same outline ordering as the report body. Each chapter is cached on a separate line.

The current V1/V2 PDFs were regenerated with LibreOffice and the directory page was inspected (contents.png). Microsoft Word updated the TOC in a real exported DOCX, producing chapter page numbers and replacing the cache without duplicate static titles. The PDF cache displays chapter names; interactive Word update produces the expanded directory and page numbers.

Regression coverage includes missing/corrupt templates, reversed chapter storage order, field nesting and existing conversion timeouts.

The final layout regression saves readable DOCX templates with individual required styles removed (cover title, table text and heading). Each returns layout_invalid with retryable=false before rendering. 109 unit tests and 6 contract tests passed. Normal rendering and timeout coverage remain passing.

Table Grid is now required with table style type. Missing or wrong-type definitions return layout_invalid with retryable=false through report export. The cached contents paragraph has zero first-line indentation so all chapter entries align. 110 unit and 6 contract tests passed.

Final review verification regenerated both frozen versions with real LibreOffice. Each PDF has four pages with matching artifact hashes and sizes. The refreshed contents.png confirms aligned cached chapter entries.
