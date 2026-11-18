"""Public wire types. Python 3.11+, Pydantic 2. IDs are opaque strings."""
from datetime import date
from typing import Annotated, Literal, Generic, TypeVar
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, AwareDatetime, model_validator

class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

ID = Annotated[str, StringConstraints(min_length=1)]
Year = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}$")]
Number = Annotated[str, StringConstraints(pattern=r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")]
Format = Literal["docx","pdf"]
Kind = Literal["industry_analysis","enterprise_comparison","industry_prosperity","investment_attraction","overseas_assessment"]

class Period(Model):
    from_: Year = Field(alias="from")
    to: Year

    @model_validator(mode="after")
    def check_order(self):
        if self.from_ > self.to:
            raise ValueError("period.from exceeds period.to")
        return self

class BaseSpec(Model):
    industry: ID
    region: ID
    period: Period
    template_id: ID
    knowledge_base_id: ID = "kb_demo"
    focus: list[ID] = Field(default_factory=list)
    style: Literal["official"] = "official"
    length: Literal["standard","brief"] = "standard"
    as_of: date | None = None

class Industry(BaseSpec):
    report_type: Literal["industry_analysis"]

class Comparison(BaseSpec):
    report_type: Literal["enterprise_comparison"]
    companies: list[ID] = Field(min_length=2, json_schema_extra={"uniqueItems":True})

    @model_validator(mode="after")
    def unique_companies(self):
        if len(set(self.companies)) != len(self.companies):
            raise ValueError("companies must be distinct")
        return self

class Prosperity(BaseSpec):
    report_type: Literal["industry_prosperity"]
    indicator: ID
    granularity: Literal["quarter"]

class Investment(BaseSpec):
    report_type: Literal["investment_attraction"]
    target_segment: ID

class Overseas(BaseSpec):
    report_type: Literal["overseas_assessment"]
    origin: ID
    target_market: ID
    product: ID
    as_of: date

    @model_validator(mode="after")
    def matching_region(self):
        if self.region != self.target_market:
            raise ValueError("region must equal target_market")
        return self

Spec = Annotated[Industry | Comparison | Prosperity | Investment | Overseas, Field(discriminator="report_type")]

class Task(Model):
    chapter_id: ID
    section_key: ID
    title: ID
    order: int = Field(ge=0)
    required: bool
    depends_on: list[ID]
    research_questions: list[ID]
    evidence_requirements: list[ID]
    required_metrics: list[ID]
    desired_chart: Literal["line","bar","table"] | None

class Outline(Model):
    chapters: list[Task] = Field(min_length=1)

class TemplateRef(Model):
    template_id: ID
    version: int = Field(ge=1)

class Template(TemplateRef):
    title: ID
    report_type: Kind
    sections: list[Task]
    layout_version: ID
    input_schema: dict  # standalone JSON Schema for this report type

class Metadata(Model):
    title: ID
    publisher: ID
    published_at: date
    source_url: str | None = None

class Pdf(Model):
    kind: Literal["pdf"]
    page: int = Field(ge=1)

class Word(Model):
    kind: Literal["docx"]
    section_path: list[str]
    paragraph: int = Field(ge=1)

class Sheet(Model):
    kind: Literal["xlsx"]
    sheet: ID
    cells: ID

class Csv(Model):
    kind: Literal["csv"]
    row: int = Field(ge=1)
    column: ID

class Evidence(Metadata):
    evidence_id: ID
    document_id: ID
    document_version_id: ID
    chunk_id: ID
    locator: Pdf | Word | Sheet | Csv
    excerpt: ID
    score: float | None = None

class Metric(Model):
    metric_id: ID
    indicator: ID
    name: ID
    entity_id: ID | None
    period: ID
    granularity: Literal["year","quarter","month"]
    region: ID
    scope: ID
    raw_value: Number | None
    raw_unit: ID | None
    multiplier: Number
    value: Number | None
    unit: ID
    currency: ID | None
    source_evidence_ids: list[ID]
    formula_id: ID | None
    input_metric_ids: list[ID]
    missing_reason: str | None

class Filters(Model):
    entity_ids: list[ID] = Field(default_factory=list)
    indicators: list[ID] = Field(default_factory=list)
    periods: list[ID] = Field(default_factory=list)
    regions: list[ID] = Field(default_factory=list)
    industries: list[ID] = Field(default_factory=list)
    as_of: date | None = None

class Gap(Model):
    question: ID
    code: ID
    reason: ID
    required: bool

class Coverage(Model):
    status: Literal["SUFFICIENT","PARTIAL","INSUFFICIENT"]
    gaps: list[Gap]

class Fact(Model):
    text: ID
    evidence_ids: list[ID]
    metric_ids: list[ID]

class Summary(Fact):
    chapter_id: ID

class FrozenRun(Model):
    run_id: ID
    spec: Spec
    template_ref: TemplateRef
    source_version_ids: list[ID]
    facts_version: ID
    facts: list[Fact]

class ChapterInput(FrozenRun):
    task: Task
    predecessors: list[Summary]
    evidences: list[Evidence]
    metrics: list[Metric]
    gaps: list[Gap]

class Paragraph(Fact):
    type: Literal["paragraph"]
    block_id: ID

class Cell(Fact):
    pass

class Table(Model):
    type: Literal["table"]
    block_id: ID
    columns: list[ID]
    rows: list[list[Cell]]

class ChartRef(Model):
    type: Literal["chart_ref"]
    block_id: ID
    chart_id: ID

Block = Annotated[Paragraph | Table | ChartRef,Field(discriminator="type")]

class ChartRequest(Model):
    block_id: ID
    chart_id: ID
    title: ID
    type: Literal["bar","line","table"]
    source_metric_ids: list[ID]

class Point(Model):
    label: ID
    series: ID
    value: Number | None
    metric_id: ID

class Chart(ChartRequest):
    chapter_id: ID
    data: list[Point]
    unit: ID
    asset_id: ID

class Chapter(Model):
    chapter_id: ID
    title: ID
    blocks: list[Block]
    summary: str
    fact_candidates: list[Fact]
    chart_requests: list[ChartRequest]
    missing_evidence: list[Gap]

class Location(Model):
    chapter_id: ID | None
    block_id: ID | None

class Issue(Model):
    issue_id: ID
    severity: Literal["WARN","BLOCK"]
    code: ID
    location: Location
    description: ID
    related_ids: list[ID]

class Rule(Model):
    rule_id: ID
    result: Literal["PASS","FAIL","NOT_CHECKED"]
    reason: str
    required: bool

class QualitySummary(Model):
    rules: list[Rule]
    issues: list[Issue]

class Version(Model):
    version_id: ID
    parent_version_id: ID | None
    created_at: AwareDatetime
    stale_chapter_ids: list[ID]
    exportable: bool

class ReportDocument(Version,FrozenRun):
    report_id: ID
    schema_version: Literal["1.0"]
    layout_version: ID
    model_id: ID
    outline: Outline
    chapters: list[Chapter]
    evidences: list[Evidence]
    metrics: list[Metric]
    charts: list[Chart]
    quality: QualitySummary

class ErrorInfo(Model):
    code: ID
    message: ID
    retryable: bool
    details: dict

class Error(Model):
    error: ErrorInfo

JobStatus = Literal["QUEUED","RUNNING","COMPLETED","COMPLETED_WITH_WARNINGS","FAILED","CANCELLED"]
ChapterStatus = Literal["PENDING","WAITING_DEPENDENCY","RUNNING","SUCCEEDED","FAILED","SKIPPED"]
class ChapterRun(Model):
    chapter_id: ID
    status: ChapterStatus
    attempt_id: ID | None
    retries: int = Field(ge=0)
    error: ErrorInfo | None

class JobDetail(Model):
    job_id: ID
    report_id: ID
    run_id: ID
    status: JobStatus
    stage: Literal["PLANNING","GENERATING","VALIDATING"] | None
    chapters: list[ChapterRun]
    result_version_id: ID | None
    updated_at: AwareDatetime
    estimated_cost_cny: Number
    error: ErrorInfo | None

class JobAccepted(Model):
    job_id: ID
    report_id: ID
    previous_job_id: ID | None = None
    status_url: ID

class Preview(Model):
    status: ChapterStatus
    content: Chapter | None

class ReportSummary(Model):
    report_id: ID
    title: ID
    report_type: Kind
    current_version_id: ID | None
    latest_job_id: ID
    status: JobStatus

class Edit(Model):
    base_version_id: ID
    blocks: list[Block]

class Edited(Model):
    new_version_id: ID
    stale_chapter_ids: list[ID]
    exportable: bool

class Rewrite(Model):
    base_version_id: ID
    scope: Literal["chapter","dependents"]

class ExportRequest(Model):
    format: Format

class ExportAccepted(Model):
    export_id: ID
    status_url: ID

class ExportSummary(Model):
    export_id: ID
    version_id: ID
    format: Format
    status: Literal["QUEUED","RUNNING","READY","FAILED"]
    checksum: str | None
    download_url: str | None
    error: ErrorInfo | None

IngestStatus = Literal["QUEUED","PROCESSING","READY","FAILED"]
class IngestAccepted(Model):
    document_id: ID
    document_version_id: ID
    ingest_status: IngestStatus
    status_url: ID

class DocumentRecord(Metadata):
    document_id: ID
    document_version_id: ID
    ingest_status: IngestStatus
    error: ErrorInfo | None

class Login(Model):
    credential: ID

T = TypeVar("T")
class Page(Model,Generic[T]):
    page: int
    limit: int
    total: int
    items: list[T]
