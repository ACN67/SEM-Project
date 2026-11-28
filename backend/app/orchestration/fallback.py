"""Deterministic ChapterWriter for integration runs without a model service.

The plan calls this mode 固定应答联调: results are marked as fixed-answer runs
via `model_id` and must never be presented as real model acceptance.
"""
from __future__ import annotations

import json

from ..contracts import models as m
from ..contracts.services import ServiceError

FIXED_WRITER_MODEL_ID = "fixed-answer-writer"


class FixedWriter:
    """Produces a contract-valid chapter JSON from the prepared context."""

    def complete(self, instructions: str, context: str) -> str:
        payload = json.loads(context)
        task = payload["task"]
        evidences = payload.get("evidences") or []
        metrics = payload.get("metrics") or []
        metric_ids = [item["metric_id"] for item in metrics[:1]]
        if metrics:
            # Keep the citation aligned with the referenced metric's own sources.
            evidence_ids = list(metrics[0]["source_evidence_ids"])
        else:
            evidence_ids = [item["evidence_id"] for item in evidences[:1]]
        summary = "本章基于冻结资料范围内检索到的证据形成示例结论，供联调验证，正式结论需真实模型与人工复核。"
        # No digits in the sample text: the quality module checks that years in
        # a paragraph match the periods of its referenced metrics.
        text = (
            f"根据冻结资料范围内的已登记来源，本节围绕「{task['title']}」给出示例性分析。"
            "相关数字来自共享指标记录，引用编号由导出阶段统一编排。"
        )
        blocks: list[dict] = [{
            "type": "paragraph",
            "block_id": "b1",
            "text": text,
            "evidence_ids": evidence_ids,
            "metric_ids": metric_ids,
        }]
        requests: list[dict] = []
        desired = task.get("desired_chart")
        if desired and metric_ids:
            blocks.append({
                "type": "chart_ref",
                "block_id": "b2",
                "chart_id": "placeholder",
            })
            requests.append({
                "chart_id": "placeholder",
                "block_id": "b2",
                "title": f"{task['title']}示意",
                "type": desired,
                "source_metric_ids": metric_ids,
            })
        chapter = {
            "chapter_id": task["chapter_id"],
            "title": task["title"],
            "blocks": blocks,
            "summary": summary,
            "fact_candidates": [],
            "chart_requests": requests,
            "missing_evidence": [],
        }
        return json.dumps(chapter, ensure_ascii=False)


def demo_knowledge():
    """Fixture knowledge instance for the zero-config demo; import here to
    keep the heavy JSON load out of module import time."""
    from .fixture_knowledge import W5FixtureKnowledge

    return W5FixtureKnowledge()


class DemoKnowledge:
    """Fixture knowledge with an explicit upload refusal for demo mode."""

    def __init__(self, fixture) -> None:
        self._fixture = fixture

    def ingest_document(self, file, metadata: m.Metadata) -> m.IngestAccepted:
        raise ServiceError(m.ErrorInfo(
            code="DOCUMENT_STORE_UNAVAILABLE",
            message="演示模式未连接数据库，不支持上传；请配置 DATABASE_URL 后使用完整知识库。",
            retryable=False, details={},
        ))

    def retrieve(self, query, filters, top_k, source_version_ids):
        return self._fixture.retrieve(query, filters, top_k, source_version_ids)

    def get_evidences(self, evidence_ids, source_version_ids):
        return self._fixture.get_evidences(evidence_ids, source_version_ids)

    def get_metrics(self, filters, source_version_ids):
        return self._fixture.get_metrics(filters, source_version_ids)


class EmptyKnowledge:
    """Knowledge stub that reports honest gaps when no source is available."""

    def ingest_document(self, file, metadata: m.Metadata) -> m.IngestAccepted:
        raise ServiceError(m.ErrorInfo(
            code="RESOURCE_NOT_FOUND",
            message="当前演示模式未连接资料库，无法导入文件；请配置 DATABASE_URL 后使用完整知识库。",
            retryable=False, details={},
        ))

    def retrieve(self, query, filters, top_k, source_version_ids) -> list[m.Evidence]:
        return []

    def get_metrics(self, filters, source_version_ids) -> list[m.Metric]:
        return []


def seed_demo_documents(store, fixture) -> None:
    """Register the fixture pack's reviewed versions as READY demo documents."""
    seen: set[str] = set()
    for record in fixture.evidence_records.values():
        if record["review_status"] != "PASS" or not record.get("published_at"):
            continue
        version_id = record["document_version_id"]
        if version_id in seen:
            continue
        seen.add(version_id)
        store.seed_document(m.DocumentRecord(
            document_id=record["document_id"], document_version_id=version_id,
            ingest_status="READY", error=None,
            title=record["title"], publisher=record["publisher"],
            published_at=record["published_at"], source_url=record["source_url"],
        ))
