"""Resolve citations from frozen direct evidence and recursive metric inputs."""
from ..contracts import models as m

class Citations:
    def __init__(self, snapshot: m.ReportDocument):
        self.evidences = {e.evidence_id:e for e in snapshot.evidences}
        self.metrics = {metric.metric_id:metric for metric in snapshot.metrics}
        self.numbers: dict[str,int] = {}
        self._metric_sources: dict[str,tuple[str,...]] = {}
        for metric in self.metrics:
            self._sources(metric,set())

    def _sources(self, metric_id, active):
        if metric_id in active:
            raise ValueError(f"cyclic metric inputs: {metric_id}")
        if metric_id in self._metric_sources:
            return self._metric_sources[metric_id]
        metric = self.metrics[metric_id]
        active.add(metric_id)
        sources = list(metric.source_evidence_ids)
        for child in metric.input_metric_ids:
            sources.extend(self._sources(child,active))
        active.remove(metric_id)
        result = tuple(dict.fromkeys(sources))
        for evidence_id in result:
            if evidence_id not in self.evidences:
                raise ValueError(f"unresolved evidence: {evidence_id}")
        self._metric_sources[metric_id] = result
        return result

    def markers(self, evidence_ids=(), metric_ids=()) -> str:
        sources = list(evidence_ids)
        for metric in metric_ids:
            sources.extend(self._metric_sources[metric])
        return "".join(f"[{self.numbers.setdefault(e,len(self.numbers)+1)}]" for e in dict.fromkeys(sources))

    def entries(self):
        for evidence_id, number in self.numbers.items():
            evidence = self.evidences[evidence_id]
            locator = evidence.locator
            if isinstance(locator,m.Pdf):
                place = f"PDF 第 {locator.page} 页"
            elif isinstance(locator,m.Word):
                place = f"{' / '.join(locator.section_path)}，第 {locator.paragraph} 段"
            elif isinstance(locator,m.Sheet):
                place = f"{locator.sheet}!{locator.cells}"
            else:
                place = f"CSV 第 {locator.row} 行，{locator.column} 列"
            text = f"[{number}] {evidence.publisher}．{evidence.title}．{evidence.published_at.isoformat()}．{place}"
            if evidence.source_url:
                text += f"\n{evidence.source_url}"
            yield text
