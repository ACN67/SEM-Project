"""Read-only Knowledge implementation backed by the reviewed W5 JSON pack."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from pydantic import TypeAdapter

from backend.app.contracts import models as m
from backend.app.contracts.services import ServiceError
from backend.app.quality import QualityService

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PACK = ROOT / "data/samples/w5"
DEFAULT_METRICS = ROOT / "data/samples/acceptance_metrics.json"

_TASKS = {
    "R01": ("market", "市场规模", ["sales"], "line"),
    "R02": ("operations", "经营指标", ["rd_investment"], "table"),
    "R03": ("prosperity", "季度景气", ["sales"], "line"),
    "R04": ("demand", "地区需求", [], None),
    "R05": ("opportunity", "市场机会", ["bev_registrations"], "bar"),
}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class W5FixtureKnowledge:
    """Test-only adapter. It does not parse files or replace the real knowledge service."""

    def __init__(
        self,
        pack_root: Path = DEFAULT_PACK,
        metrics_path: Path = DEFAULT_METRICS,
    ):
        metrics_file = _read_json(metrics_path)
        evidence_file = _read_json(pack_root / "evidence.json")
        cases_file = _read_json(pack_root / "cases.json")
        self.contract_indicators = metrics_file["technical_review"][
            "contract_indicators"
        ]
        self.metric_records = {
            record["record_id"]: record for record in metrics_file["metrics"]
        }
        self.evidence_records = {
            record["evidence_key"]: record
            for record in evidence_file["records"]
        }
        self.cases = {
            case["case_id"]: case for case in cases_file["cases"]
        }

    def ingest_document(self, file: Path, metadata: m.Metadata) -> m.IngestAccepted:
        raise ServiceError(
            m.ErrorInfo(
                code="FIXTURE_READ_ONLY",
                message="W5 fixture knowledge is read-only.",
                retryable=False,
                details={},
            )
        )

    def source_version_ids(self, case_id: str) -> list[str]:
        case = self._case(case_id)
        return sorted(
            {
                self.evidence_records[key]["document_version_id"]
                for key in case["evidence_keys"]
            }
        )

    def _case(self, case_id: str) -> dict:
        if case_id not in self.cases:
            raise ServiceError(
                m.ErrorInfo(
                    code="FIXTURE_CASE_NOT_FOUND",
                    message=f"W5 case {case_id} does not exist.",
                    retryable=False,
                    details={"case_id": case_id},
                )
            )
        return self.cases[case_id]

    @staticmethod
    def _shared_evidence(record: dict) -> m.Evidence:
        locator_data = record["locator"]
        if locator_data["kind"] == "pdf":
            locator = m.Pdf(kind="pdf", page=locator_data["page"])
        elif locator_data["kind"] == "xlsx":
            locator = m.Sheet(
                kind="xlsx",
                sheet=locator_data["sheet"],
                cells=locator_data["cell"],
            )
        else:
            # Test transport only. The real knowledge service must preserve a parsed
            # web snapshot and cannot claim this DOCX locator as authoritative.
            locator = m.Word(
                kind="docx",
                section_path=[
                    "网页快照",
                    locator_data["anchor"],
                ],
                paragraph=1,
            )
        if record["published_at"] is None:
            raise ValueError(f"Evidence {record['evidence_key']} has no publication date")
        return m.Evidence(
            title=record["title"],
            publisher=record["publisher"],
            published_at=record["published_at"],
            source_url=record["source_url"],
            evidence_id=record["evidence_key"],
            document_id=record["document_id"],
            document_version_id=record["document_version_id"],
            chunk_id=f"chunk_{record['evidence_key']}",
            locator=locator,
            excerpt=record["excerpt"],
            score=None,
        )

    def _metric(self, record: dict) -> m.Metric:
        return m.Metric(
            metric_id=record["record_id"],
            indicator=self.contract_indicators[record["indicator"]],
            name=record["name"],
            entity_id=record.get("entity_id"),
            period=record["period"],
            granularity=record["granularity"],
            region=record["region"],
            scope=record["scope"],
            raw_value=record.get("value"),
            raw_unit=record.get("unit"),
            multiplier="1",
            value=record.get("value"),
            unit=record["unit"],
            currency=record.get("currency"),
            source_evidence_ids=list(record["evidence_keys"]),
            formula_id=None,
            input_metric_ids=[],
            missing_reason=None,
        )

    def _matches_query(self, case_id: str, query: str) -> bool:
        normalized = query.strip().upper()
        if not normalized:
            return True
        if case_id in normalized:
            return True
        needle = query.strip().casefold()
        for key in self.cases[case_id]["evidence_keys"]:
            record = self.evidence_records[key]
            haystack = " ".join(
                [
                    record["title"],
                    record["excerpt"],
                    record["applies_to"] or "",
                ]
            ).casefold()
            if needle in haystack:
                return True
        return False

    @staticmethod
    def _evidence_in_filters(record: dict, filters: m.Filters) -> bool:
        if filters.as_of is not None and record["published_at"]:
            if date.fromisoformat(record["published_at"]) > filters.as_of:
                return False
        applies_to = record["applies_to"] or ""
        if filters.regions and not any(region in applies_to for region in filters.regions):
            return False
        if filters.industries and not any(
            industry in applies_to for industry in filters.industries
        ):
            return False
        return True

    def retrieve(
        self,
        query: str,
        filters: m.Filters,
        top_k: int,
        source_version_ids: list[str],
    ) -> list[m.Evidence]:
        if not 1 <= top_k <= 50:
            raise ServiceError(
                m.ErrorInfo(
                    code="INVALID_TOP_K",
                    message="top_k must be between 1 and 50.",
                    retryable=False,
                    details={"top_k": top_k},
                )
            )
        allowed_versions = set(source_version_ids)
        if not allowed_versions:
            return []
        results: list[m.Evidence] = []
        for case_id in sorted(self.cases):
            if not self._matches_query(case_id, query):
                continue
            for key in sorted(self.cases[case_id]["evidence_keys"]):
                record = self.evidence_records[key]
                if record["review_status"] != "PASS":
                    continue
                if record["document_version_id"] not in allowed_versions:
                    continue
                if not self._evidence_in_filters(record, filters):
                    continue
                results.append(self._shared_evidence(record))
                if len(results) == top_k:
                    return results
        return results

    def get_metrics(
        self,
        filters: m.Filters,
        source_version_ids: list[str],
    ) -> list[m.Metric]:
        allowed_versions = set(source_version_ids)
        if not allowed_versions:
            return []
        results: list[m.Metric] = []
        for record_id in sorted(self.metric_records):
            record = self.metric_records[record_id]
            evidence_keys = record.get("evidence_keys", [])
            if not evidence_keys:
                continue
            if record.get("input_record_ids"):
                continue
            if any(
                self.evidence_records[key]["document_version_id"]
                not in allowed_versions
                for key in evidence_keys
                if key in self.evidence_records
            ):
                continue
            indicator = self.contract_indicators[record["indicator"]]
            if filters.indicators and indicator not in filters.indicators:
                continue
            if filters.entity_ids and record.get("entity_id") not in filters.entity_ids:
                continue
            if filters.periods and record["period"] not in filters.periods:
                continue
            if filters.regions and record["region"] not in filters.regions:
                continue
            results.append(self._metric(record))
        return results

    def case_context(self, case_id: str) -> m.ChapterInput:
        case = self._case(case_id)
        spec = TypeAdapter(m.Spec).validate_python(case["request"])
        section_key, title, required_metrics, desired_chart = _TASKS[case_id]
        task = m.Task(
            chapter_id=section_key,
            section_key=section_key,
            title=title,
            order=0,
            required=True,
            depends_on=[],
            research_questions=[],
            evidence_requirements=[],
            required_metrics=required_metrics,
            desired_chart=desired_chart,
        )
        versions = self.source_version_ids(case_id)
        filters = m.Filters(as_of=spec.as_of)
        evidences = self.retrieve(case_id, filters, 50, versions)
        raw_metrics = self.get_metrics(filters, versions)
        calculation = QualityService().calculate_metrics(raw_metrics, task)
        return m.ChapterInput(
            run_id=f"fixture_{case_id}",
            spec=spec,
            template_ref=m.TemplateRef(
                template_id=spec.template_id,
                version=1,
            ),
            source_version_ids=versions,
            facts_version="fixture_f0",
            facts=[],
            task=task,
            predecessors=[],
            evidences=evidences,
            metrics=calculation.metrics,
            gaps=calculation.gaps,
        )
