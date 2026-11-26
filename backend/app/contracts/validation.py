"""Cross-object checks for the producer and consumers of report snapshots."""
from .models import ChapterInput, Chapter, Paragraph, Table, ReportDocument

def indexed(items, field):
    result = {getattr(item, field):item for item in items}
    if len(result) != len(items):
        raise ValueError(f"duplicate {field}")
    return result

def refs(ids, allowed, label):
    if set(ids).difference(allowed):
        raise ValueError(f"unresolved {label}")

def check_chapter(chapter: Chapter, evidence, metrics):
    indexed(chapter.blocks,"block_id")
    facts = list(chapter.fact_candidates)
    for block in chapter.blocks:
        if isinstance(block, Paragraph):
            facts.append(block)
        if isinstance(block, Table):
            for row in block.rows:
                if len(row) != len(block.columns):
                    raise ValueError("table width")
                facts.extend(row)
    for fact in facts:
        refs(fact.evidence_ids,evidence,"evidence")
        refs(fact.metric_ids,metrics,"metric")
    for request in chapter.chart_requests:
        refs(request.source_metric_ids,metrics,"chart source metric")

def check_generation(input: ChapterInput, chapter: Chapter):
    if chapter.chapter_id != input.task.chapter_id:
        raise ValueError("chapter_id mismatch")
    refs([e.document_version_id for e in input.evidences],input.source_version_ids,"document version")
    evidence = indexed(input.evidences,"evidence_id")
    metrics = indexed(input.metrics,"metric_id")
    for fact in [*input.predecessors,*input.facts]:
        refs(fact.evidence_ids,evidence,"inherited evidence")
        refs(fact.metric_ids,metrics,"inherited metric")
    check_chapter(chapter,evidence,metrics)

def check_snapshot(doc: ReportDocument):
    tasks = indexed(doc.outline.chapters,"chapter_id")
    chapters = indexed(doc.chapters,"chapter_id")
    evidence = indexed(doc.evidences,"evidence_id")
    metrics = indexed(doc.metrics,"metric_id")
    charts = indexed(doc.charts,"chart_id")
    refs(chapters,tasks,"chapter")
    refs(doc.stale_chapter_ids,tasks,"stale chapter")
    refs([e.document_version_id for e in evidence.values()],doc.source_version_ids,"document version")
    done = set()
    while len(done) != len(tasks):
        ready = {k for k,t in tasks.items() if k not in done and set(t.depends_on) <= done}
        if not ready:
            raise ValueError("cyclic or missing dependency")
        done.update(ready)
    for fact in doc.facts:
        refs(fact.evidence_ids,evidence,"frozen fact evidence")
        refs(fact.metric_ids,metrics,"frozen fact metric")
    for metric in metrics.values():
        refs(metric.source_evidence_ids,evidence,"metric evidence")
        refs(metric.input_metric_ids,metrics,"formula input")
    for chart in charts.values():
        refs([chart.chapter_id],chapters,"chart chapter")
        refs(chart.source_metric_ids,metrics,"chart source")
        refs([point.metric_id for point in chart.data],chart.source_metric_ids,"point metric")
    for chapter in chapters.values():
        check_chapter(chapter,evidence,metrics)
        for block in chapter.blocks:
            if block.type=="chart_ref" and (block.chart_id not in charts or charts[block.chart_id].chapter_id!=chapter.chapter_id):
                raise ValueError("unresolved chapter chart")
    if doc.exportable and (doc.stale_chapter_ids or any(t.required and t.chapter_id not in chapters for t in tasks.values()) or any(i.severity=="BLOCK" for i in doc.quality.issues) or not doc.quality.rules or any(r.required and r.result=="NOT_CHECKED" for r in doc.quality.rules)):
        raise ValueError("report not exportable")
