"""Retrieve within READY source versions before vector ranking."""
from contextlib import closing
import json
from ..contracts import models as m
from ..contracts.services import ServiceError

class Retrieval:
    def __init__(self, connect, index):
        self.connect = connect
        self.index = index

    def retrieve(self, query, filters, top_k, source_version_ids):
        if isinstance(top_k,bool) or not isinstance(top_k,int) or not 1<=top_k<=50 or not query.strip():
            raise ServiceError(m.ErrorInfo(code="INVALID_REQUEST",message="Invalid retrieval query or top_k",retryable=False,details={}))
        if not source_version_ids:
            return []
        placeholders = ",".join(["%s"]*len(source_version_ids))
        with closing(self.connect()) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT e.payload,c.industry,v.metadata_json,l.payload,m.payload FROM evidences e "
                "JOIN chunks c ON c.chunk_id=e.chunk_id JOIN document_versions v ON v.document_version_id=e.document_version_id "
                 "LEFT JOIN metrics m ON m.evidence_id=e.evidence_id LEFT JOIN knowledge_labels l ON l.document_version_id=v.document_version_id "
                f"WHERE v.ingest_status='READY' AND v.document_version_id IN ({placeholders})",
                tuple(source_version_ids))
            rows = cursor.fetchall()
        candidates = {}
        for payload,industry,metadata,labels,metric_payload in rows:
            evidence = m.Evidence.model_validate_json(payload)
            tags = json.loads(labels) if labels else {}
            if metric_payload:
                metric = m.Metric.model_validate_json(metric_payload)
                for field,value in (("entity_ids",metric.entity_id),("indicators",metric.indicator),
                    ("periods",metric.period),("regions",metric.region)):
                    tags[field] = [value] if value else []
            if industry:
                tags["industries"] = [industry]
            if filters.as_of and m.Metadata.model_validate_json(metadata).published_at > filters.as_of:
                continue
            if any(values and not set(values).intersection(tags.get(field,[])) for field,values in (
                ("entity_ids",filters.entity_ids),("indicators",filters.indicators),("periods",filters.periods),
                ("regions",filters.regions),("industries",filters.industries))):
                continue
            candidates[evidence.evidence_id]=evidence
        if not candidates:
            return []
        try:
            ranked=self.index.rank(query,sorted(candidates))
        except Exception as exc:
            raise ServiceError(m.ErrorInfo(code="RETRIEVAL_FAILED",message="Evidence index unavailable",
                retryable=True,details={})) from exc
        return [candidates[id].model_copy(update={"score":score}) for id,score in ranked[:top_k]]
