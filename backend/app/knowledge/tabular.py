"""Parse immutable CSV/XLSX originals into source-linked raw observations."""
import csv
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import re

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from ..contracts import models as m
from .storage import ingest_error

COLUMNS = ("indicator", "name", "entity_id", "period", "granularity", "region",
           "scope", "raw_value", "raw_unit", "multiplier", "unit", "currency")
RULE_VERSION = "1"
UNITS = {("万辆", "辆"): Decimal(10000), ("万元", "元"): Decimal(10000),
         ("亿元", "元"): Decimal(100000000)}

@dataclass(frozen=True)
class Observation:
    evidence: m.Evidence
    metric: m.Metric
    industry: str | None = None

def _id(*parts):
    return sha256("\0".join(parts).encode()).hexdigest()

def _text(value):
    return "" if value is None else str(value).strip()

def _number(value):
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError("nonfinite number")
    text = format(number, "f")
    return text if number else "0"

def _observation(record, row, locator, header, industry):
    raw = _text(row["raw_value"])
    raw_unit, unit = _text(row["raw_unit"]), _text(row["unit"])
    multiplier = Decimal(_text(row["multiplier"]))
    expected = Decimal(1) if raw_unit == unit else UNITS.get((raw_unit, unit))
    if expected is None or multiplier != expected:
        raise ValueError("unregistered unit conversion")
    period, granularity = _text(row["period"]), _text(row["granularity"])
    patterns = {"year": r"\d{4}", "quarter": r"\d{4}-Q[1-4]", "month": r"\d{4}-(0[1-9]|1[0-2])"}
    if not re.fullmatch(patterns.get(granularity, "(?!)"), period):
        raise ValueError("invalid period")
    currency = _text(row["currency"]) or None
    if unit in {"元", "美元", "欧元"} and not currency:
        raise ValueError("currency required")
    missing = raw in {"", "-", "—", "–"}
    value = None if missing else _number((Decimal(raw) * multiplier).normalize())
    key = _id(record.document_version_id, locator.model_dump_json(), _text(row["indicator"]), RULE_VERSION)
    evidence = m.Evidence(**record.model_dump(include=set(m.Metadata.model_fields)),
        evidence_id=key, chunk_id=key, document_id=record.document_id,
        document_version_id=record.document_version_id, locator=locator,
        excerpt=f"{header}: {raw or '[missing]'} {raw_unit}")
    metric = m.Metric(metric_id=key, **{k: _text(row[k]) for k in
        ("indicator","name","period","granularity","region","scope","unit")},
        entity_id=_text(row["entity_id"]) or None, raw_value=None if missing else _number(raw),
        raw_unit=raw_unit or None, multiplier=_number(multiplier), value=value,
        currency=currency, source_evidence_ids=[key], formula_id=None, input_metric_ids=[],
        missing_reason="missing source value" if missing else None)
    return Observation(evidence, metric, industry)

def parse_tabular(path: Path, original_filename: str, record: m.DocumentRecord, *,
                  header_mapping=None, wide_rows=None, industry=None) -> list[Observation]:
    """wide_rows is a registered mapping of (sheet, cell, indicator fields) for a source."""
    mapping = header_mapping or {}
    results = []
    def consume(headers, values, locator):
        names = [mapping.get(_text(h), _text(h)) for h in headers]
        if len(set(names)) != len(names) or not set(COLUMNS) <= set(names):
            raise ValueError("unknown or duplicate headers")
        row = dict(zip(names, values, strict=True))
        if any(_text(v) for v in values):
            index = names.index("raw_value")
            results.append(_observation(record, row, locator(index), _text(headers[index]), industry))
    try:
        suffix = Path(original_filename).suffix.lower()
        if suffix == ".csv":
            with path.open(encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f, strict=True)
                headers = next(reader)
                for number, values in enumerate(reader, 2):
                    if values:
                        consume(headers, values, lambda i: m.Csv(kind="csv", row=number, column=_text(headers[i])))
        elif suffix == ".xlsx":
            with path.open("rb") as f:
                book = load_workbook(f, read_only=True, data_only=True)
                try:
                    if wide_rows is not None:
                        for sheet, coordinate, fields in wide_rows:
                            row = dict(fields, raw_value=book[sheet][coordinate].value)
                            results.append(_observation(record, row, m.Sheet(kind="xlsx", sheet=sheet, cells=coordinate),
                                                        fields["name"], industry))
                    else:
                        for sheet in book:
                            rows = sheet.iter_rows()
                            headers = [cell.value for cell in next(rows)]
                            for number, cells in enumerate(rows, 2):
                                consume(headers, [cell.value for cell in cells],
                                        lambda i: m.Sheet(kind="xlsx", sheet=sheet.title, cells=cells[i].coordinate
                                            if hasattr(cells[i], "coordinate") else
                                            f"{get_column_letter(i+1)}{number}"))
                finally:
                    book.close()
        else:
            raise ValueError("unsupported tabular format")
        seen = set()
        for observation in results:
            metric = observation.metric
            key = (metric.indicator, metric.entity_id, metric.period, metric.region, metric.scope, metric.currency)
            if key in seen:
                raise ValueError(f"duplicate observation: {key}")
            seen.add(key)
        if not results:
            raise ValueError("no observations")
        return results
    except Exception as exc:
        raise ingest_error("TABULAR_PARSE_ERROR", f"{original_filename}: {exc}") from exc

