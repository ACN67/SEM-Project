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

    def ingest_version(self, version_id: str, *, source_evidence_ids=None, **mapping) -> list[m.Metric]:
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
                    if source_evidence_ids is not None:
                        if len(observations)!=1 or not source_evidence_ids:
                            raise ValueError('source-linked transcription requires one observation')
                        ids=list(dict.fromkeys(source_evidence_ids))
                        cursor.execute('SELECT e.payload,v.ingest_status FROM evidences e JOIN document_versions v ON v.document_version_id=e.document_version_id WHERE e.evidence_id IN ('+','.join(['%s']*len(ids))+')',tuple(ids))
                        sources=cursor.fetchall()
                        if len(sources)!=len(ids) or any(status!='READY' for _,status in sources):
                            raise ValueError('original evidence unavailable')
                        if record.published_at!=max(m.Evidence.model_validate_json(p).published_at for p,_ in sources):
                            raise ValueError('transcription date must match latest input evidence')
                        observations[0].metric.source_evidence_ids=ids
                    cursor.execute("SELECT m.payload, e.payload, c.industry FROM metrics m "
                                   "JOIN evidences e ON m.evidence_id=e.evidence_id "
                                   "JOIN chunks c ON e.chunk_id=c.chunk_id WHERE m.document_version_id=%s", (version_id,))
                    existing = {}
                    for metric_json, evidence_json, industry in cursor.fetchall():
                        metric = m.Metric.model_validate_json(metric_json)
                        existing[metric.metric_id] = (metric, m.Evidence.model_validate_json(evidence_json), industry)
                    incoming = {o.metric.metric_id: (o.metric, o.evidence, o.industry) for o in observations}
                    if existing and existing != incoming:
                        raise ValueError("immutable version has different parsed observations or metadata")
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
            with closing(self._connect()) as connection, connection.cursor() as cursor:
                ids=metric.source_evidence_ids
                if not ids:
                    continue
                cursor.execute('SELECT e.payload,v.ingest_status FROM evidences e JOIN document_versions v ON v.document_version_id=e.document_version_id WHERE e.evidence_id IN ('+','.join(['%s']*len(ids))+')',tuple(ids))
                sources=cursor.fetchall()
            if len(sources)!=len(set(ids)) or any(status!='READY' or m.Evidence.model_validate_json(p).document_version_id not in source_version_ids for p,status in sources):
                continue
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
