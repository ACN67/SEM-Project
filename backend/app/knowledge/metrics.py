"""Atomic storage and frozen-version queries for raw tabular observations."""
from contextlib import closing
from pathlib import Path

from ..contracts import models as m
from .repository import ConnectionFactory, VERSION_FIELDS, _record
from .tabular import parse_tabular

class MetricRepository:
    def __init__(self, connect: ConnectionFactory, runtime_root: Path):
        self._connect = connect
        self._runtime_root = runtime_root

    def ingest_version(self, version_id: str, **mapping) -> list[m.Metric]:
        with closing(self._connect()) as connection:
            connection.begin()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(f"SELECT {VERSION_FIELDS}, original_filename, storage_key "
                                   "FROM document_versions WHERE document_version_id=%s FOR UPDATE", (version_id,))
                    row = cursor.fetchone()
                    if row is None:
                        raise ValueError("unknown document version")
                    record = _record(row[:5])
                    observations = parse_tabular(self._runtime_root / row[6], row[5], record, **mapping)
                    cursor.execute("SELECT payload FROM metrics WHERE document_version_id=%s", (version_id,))
                    existing = {metric.metric_id: metric for (payload,) in cursor.fetchall()
                                if (metric := m.Metric.model_validate_json(payload))}
                    if existing and existing != {o.metric.metric_id: o.metric for o in observations}:
                        raise ValueError("immutable version has different parsed observations")
                    for observation in observations:
                        evidence, metric = observation.evidence, observation.metric
                        cursor.execute("INSERT INTO chunks VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE chunk_id=chunk_id",
                                       (evidence.chunk_id, version_id, evidence.excerpt, observation.industry))
                        cursor.execute("INSERT INTO evidences VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE evidence_id=evidence_id",
                                       (evidence.evidence_id, evidence.chunk_id, version_id, evidence.model_dump_json()))
                        cursor.execute("INSERT INTO metrics VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE metric_id=metric_id",
                                       (metric.metric_id, evidence.evidence_id, version_id, metric.model_dump_json()))
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return [o.metric for o in observations]

    def get_metrics(self, filters: m.Filters, source_version_ids: list[str]) -> list[m.Metric]:
        if not source_version_ids:
            return []
        placeholders = ",".join(["%s"] * len(source_version_ids))
        with closing(self._connect()) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT m.payload,c.industry,v.metadata_json FROM metrics m "
                           "JOIN evidences e ON e.evidence_id=m.evidence_id "
                           "JOIN chunks c ON c.chunk_id=e.chunk_id "
                           "JOIN document_versions v ON v.document_version_id=m.document_version_id "
                           f"WHERE v.ingest_status='READY' AND v.document_version_id IN ({placeholders}) "
                           "ORDER BY m.metric_id", tuple(source_version_ids))
            rows = cursor.fetchall()
        result = []
        for payload, industry, metadata in rows:
            metric = m.Metric.model_validate_json(payload)
            if filters.as_of and m.Metadata.model_validate_json(metadata).published_at > filters.as_of:
                continue
            if any(values and actual not in values for values, actual in (
                (filters.entity_ids, metric.entity_id), (filters.indicators, metric.indicator),
                (filters.periods, metric.period), (filters.regions, metric.region),
                (filters.industries, industry),
            )):
                continue
            result.append(metric)
        return result
