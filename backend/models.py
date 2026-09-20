"""Shared data structures for inputs, discovery, source analysis, and correlation."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StrictBool, StringConstraints, model_validator


class TextContext(BaseModel):
    """User-supplied context only, not OCR observations; blank values become null."""

    name: str | None = None
    username: str | None = None
    organization: str | None = None
    role: str | None = None
    department: str | None = None


class ImageMetadata(BaseModel):
    """Detected image properties without image contents or filesystem paths."""

    format: Literal["JPEG", "PNG"]
    width: int
    height: int
    byte_size: int


class InputResponse(BaseModel):
    """Validation result; does not indicate that image analysis has run."""

    status: Literal["validated"] = "validated"
    context: TextContext = Field(
        description="Normalized user-supplied context, not OCR observations or verified identity claims."
    )
    image: ImageMetadata
    message: str = "Input validated. OCR and identity correlation have not run."


class OCRBoundingBox(BaseModel):
    """Pixel coordinates measured from the processed image's top-left corner."""

    left: int = Field(ge=0)
    top: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class OCRToken(BaseModel):
    """An unverified word observation with recognition confidence only."""

    text: str
    ocr_confidence: float = Field(
        ge=0, le=100, description="Tesseract OCR recognition confidence (0–100), not identity confidence."
    )
    bounding_box: OCRBoundingBox


class ProcessedImage(BaseModel):
    """Dimensions after EXIF orientation correction and RGB conversion."""

    width: int = Field(gt=0)
    height: int = Field(gt=0)
    coordinate_space: str = "Pixels in the EXIF-corrected RGB image; origin at top-left."


class ImageCluesResponse(BaseModel):
    """Local OCR observations, kept separate from supplied context."""

    supplied_context: TextContext
    ocr_status: Literal["completed", "no_text"]
    extracted_text: str
    tokens: list[OCRToken]
    processed_image: ProcessedImage
    message: str = (
        "OCR text is an unverified observation. Recognition confidence is not identity confidence. "
        "Text does not establish identity, attendance, or employment. Identity correlation has not run."
    )


PlanText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


class SearchContext(BaseModel):
    """Bounded supplied context, separate from reviewed OCR observations."""

    name: PlanText | None = None
    username: PlanText | None = None
    organization: PlanText | None = None
    role: PlanText | None = None
    department: PlanText | None = None


class ReviewedClue(BaseModel):
    """Client-reviewed observation; the original is preserved separately."""

    clue_id: str = Field(pattern=r"^clue-[0-9]{4}$")
    original_text: str = Field(max_length=2000)
    corrected_text: PlanText
    selected: StrictBool = False
    category: Literal["name", "username", "event", "organization", "other"] = "other"


class SearchPlanRequest(BaseModel):
    supplied_context: SearchContext = Field(default_factory=SearchContext)
    clues: list[ReviewedClue] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def unique_clue_ids(self):
        if len({clue.clue_id for clue in self.clues}) != len(self.clues):
            raise ValueError("Clue IDs must be unique.")
        return self


class QueryReference(BaseModel):
    source: Literal["supplied_context", "clue"]
    field: Literal["name", "username", "organization", "role", "department", "corrected_text"]
    clue_id: str | None = None


class PreparedQuery(BaseModel):
    query: str
    reason: str
    references: list[QueryReference]


class SearchPlanResponse(BaseModel):
    status: Literal["prepared", "needs_context"]
    queries: list[PreparedQuery]
    platform_queries: list[PreparedQuery] = Field(default_factory=list, max_length=3)
    message: str
    label: str = "Prepared queries — no search performed."


class DiscoveryQuery(PreparedQuery):
    query: str = Field(min_length=1, max_length=500)
    reason: str = Field(max_length=20000)
    references: list[QueryReference] = Field(max_length=100)


class DiscoveryRequest(BaseModel):
    """The prepared query objects returned by search-plan, without image contents."""

    queries: list[DiscoveryQuery] = Field(min_length=1, max_length=6)
    platform_queries: list[DiscoveryQuery] = Field(default_factory=list, max_length=3)


Platform = Literal["linkedin", "github", "instagram", "youtube", "x", "twitter", "google_scholar", "institution", "faculty", "conference", "event", "publication", "personal_site", "huggingface", "substack", "other"]

class DiscoveryProvenance(BaseModel):
    query: str
    url: str
    rank: int
    snippet: str
    provider: str = "tavily"

class CandidateSource(BaseModel):
    candidate_id: str | None = None
    platform: Platform = "other"
    provenance: list[DiscoveryProvenance] = Field(default_factory=list)
    verification: Literal["candidate_unverified"] = "candidate_unverified"

    title: str
    url: str
    snippet: str
    domain: str
    source_type: Literal["web_page"] = "web_page"
    provider: Literal["tavily"] = "tavily"
    rank: int = Field(ge=1, description="Best 1-based provider result position across originating queries; not confidence.")
    discovered_by: list[PreparedQuery]


class DiscoveryIssue(BaseModel):
    query: str
    code: str
    message: str


class DiscoveryResponse(BaseModel):
    status: Literal["completed", "no_results", "partial", "failed"]
    provider: Literal["tavily"] = "tavily"
    candidates: list[CandidateSource]
    queries_searched: list[str]
    issues: list[DiscoveryIssue]
    message: str = "Candidate public sources only, not verified identities. Result pages have not been fetched or read."


class SourceAnalysisRequest(BaseModel):
    """Explicitly selected discovery candidates and optional seed hints; no automatic crawling."""

    candidates: list[CandidateSource] = Field(min_length=1, max_length=3)
    supplied_context: SearchContext = Field(default_factory=SearchContext)
    clues: list[ReviewedClue] = Field(default_factory=list, max_length=50)


class SourceObservation(BaseModel):
    """A source-derived subject observation with local evidence, not an identity claim."""

    field: Literal[
        "name", "username", "organization", "role", "event", "project",
        "publication", "date", "location", "profile_link", "department", "education", "bio"
    ]
    value: str = Field(min_length=1, max_length=500)
    evidence: str = Field(min_length=1, max_length=800)
    extraction_method: Literal["json_ld", "metadata", "heading", "text_pattern", "text_match", "url", "search_snippet"]
    subject_name: str | None = None
    scope_id: str | None = None
    source_url: str | None = None
    observed_date: str | None = None
    temporal_context: Literal["current", "historical", "unknown"] = "unknown"


class ProfileRelation(BaseModel):
    source_candidate_id: str
    relation_type: Literal["explicit_profile_link", "personal_domain_link", "institutional_profile_link", "project_reference", "event_reference"]
    target_url: str
    evidence: str
    evidence_origin: str
    evidence_type: Literal["direct_page", "search_snippet"] = "direct_page"

class RelatedRecord(BaseModel):
    discovery_queries: list[PreparedQuery] = Field(default_factory=list)
    discovery_provenance: list[DiscoveryProvenance] = Field(default_factory=list)
    url: str
    record_type: str
    disposition: str
    title: str | None = None
    snippet: str | None = None
    access_status: str
    evidence: list[CorrelationEvidence] = Field(default_factory=list)

class AdditionalFinding(BaseModel):
    field: str
    value: str
    source_count: int
    evidence: list[CorrelationEvidence]

class CandidateProfile(BaseModel):
    profile_slug: str | None = None
    record_type: str = "profile"
    related_records: list[RelatedRecord] = Field(default_factory=list)

    candidate_id: str
    platform: Platform
    url: str
    domain: str
    page_title: str | None = None
    display_name: str | None = None
    username: str | None = None
    organization: list[str] = Field(default_factory=list)
    role: list[str] = Field(default_factory=list)
    department: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    location: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    publications: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    personal_domains: list[str] = Field(default_factory=list)
    external_profile_links: list[ProfileRelation] = Field(default_factory=list)
    bio_or_summary: str | None = None
    source_evidence: list[SourceObservation] = Field(default_factory=list)
    discovery_queries: list[PreparedQuery] = Field(default_factory=list)
    access_status: Literal["SEARCH_SNIPPET_ONLY", "SOURCE_INACCESSIBLE", "PUBLIC_PAGE_ANALYZED"]
    source_metadata: dict = Field(default_factory=dict)
    relationship: Literal["profile_candidate", "page_reference"] = "page_reference"

class ConnectedProfile(BaseModel):
    profile: CandidateProfile
    status: Literal["supported", "partially_supported", "insufficient_evidence", "conflicting"]
    supporting: list[str]
    missing: list[str]
    conflicts: list[str]
    rationale: str
    evidence: list[CorrelationEvidence] = Field(default_factory=list)
    ml_score: float | None = Field(default=None, ge=0, le=1, description="ML association score, not a calibrated identity probability.")
    ml_band: Literal["strong", "moderate", "weak"] | None = None

class PageAttribution(BaseModel):
    """Page/site ownership metadata kept separate from the candidate person's identity."""

    authors: list[str] = Field(default_factory=list)
    publishers: list[str] = Field(default_factory=list)
    site_social_links: list[str] = Field(default_factory=list)


class SourceReadIssue(BaseModel):
    code: str
    message: str
    http_status: int | None = None


class AnalyzedSource(BaseModel):
    """One selected candidate after safe source reading and conservative extraction."""

    candidate: CandidateSource
    status: Literal["analyzed", "blocked", "restricted", "unreadable", "failed"]
    final_url: str | None = None
    page_title: str | None = None
    meta_description: str | None = None
    content_excerpt: str | None = None
    observations: list[SourceObservation] = Field(default_factory=list)
    page_attribution: PageAttribution = Field(default_factory=PageAttribution)
    issue: SourceReadIssue | None = None
    profile: CandidateProfile | None = None


class SourceAnalysisResponse(BaseModel):
    status: Literal["completed", "partial", "failed"]
    sources: list[AnalyzedSource]
    message: str = (
        "Source observations only, not verified identity facts. Publisher/author metadata is separated. "
        "Identity correlation has not run."
    )


class SeedReference(BaseModel):
    source: Literal["supplied_context", "image_clue"]
    clue_id: str | None = None


class SeedSignal(BaseModel):
    field: Literal["name", "username", "organization", "role", "department", "event"]
    value: str
    references: list[SeedReference]


class CorrelationEvidence(BaseModel):
    value: str
    evidence: str
    extraction_method: str
    source_url: str
    field: str | None = None


class SignalComparison(BaseModel):
    field: Literal["name", "username", "organization", "role", "department", "event"]
    seed_value: str
    status: Literal["match", "conflict", "no_support", "missing"]
    source_values: list[str] = Field(default_factory=list)
    evidence: list[CorrelationEvidence] = Field(default_factory=list)
    explanation: str


class CandidateAssessment(BaseModel):
    source_url: str
    page_title: str | None = None
    status: Literal["supported", "uncertain", "conflicting", "insufficient"]
    matched_fields: list[str]
    conflicting_fields: list[str]
    comparisons: list[SignalComparison]
    rationale: str


class CrossSourceSupport(BaseModel):
    field: Literal["name", "username", "organization", "role", "event", "project", "publication", "date", "location"]
    value: str
    source_urls: list[str]


class CorrelationRequest(BaseModel):
    """Compare analyzed public records with supplied/reviewed seed identity signals."""

    supplied_context: SearchContext = Field(default_factory=SearchContext)
    clues: list[ReviewedClue] = Field(default_factory=list, max_length=50)
    sources: list[AnalyzedSource] = Field(min_length=1, max_length=3)
    discovered_candidates: list[CandidateSource] = Field(default_factory=list, max_length=45)


class ReportField(BaseModel):
    direct_sources: int = 0
    indexed_sources: int = 0
    field: str
    supplied_value: str
    status: Literal["supported", "partial", "conflicting", "missing", "not_assessed"]
    explanation: str
    evidence: list[CorrelationEvidence] = Field(default_factory=list)




class TemporalClaim(BaseModel):
    field: str
    value: str
    source_url: str
    context: Literal["current", "historical", "unknown"] = "unknown"
    year: int | None = None
    start_year: int | None = None
    end_year: int | None = None
    evidence: str


class TimelineEvent(BaseModel):
    year_label: str
    sort_year: int | None = None
    category: str
    title: str
    source_url: str
    evidence: str
    context: Literal["current", "historical", "unknown"] = "unknown"


class TemporalIssue(BaseModel):
    field: str
    seed_value: str | None = None
    observed_value: str
    source_url: str
    reason: str
    severity: Literal["review", "conflict"] = "review"


class TemporalAssessment(BaseModel):
    status: Literal["consistent", "mixed", "conflicting", "insufficient"]
    claims: list[TemporalClaim] = Field(default_factory=list)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    issues: list[TemporalIssue] = Field(default_factory=list)
    summary: str


class GraphNode(BaseModel):
    id: str
    label: str
    type: Literal["identity", "profile", "organization", "role", "department", "project", "event", "publication", "education", "location"]
    status: Literal["supported", "partial", "uncertain", "conflicting"] = "supported"
    url: str | None = None


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    relation: str
    status: Literal["supported", "partial", "uncertain", "conflicting"] = "supported"
    source_urls: list[str] = Field(default_factory=list)
    evidence_count: int = 0


class RelationshipGraph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    summary: str


class MLAssessment(BaseModel):
    score: float = Field(ge=0, le=1, description="Association score from a synthetic-trained baseline model; not a probability.")
    band: Literal["strong", "moderate", "weak"]
    features: dict[str, float] = Field(default_factory=dict)
    model_version: str
    training_basis: str
    holdout_accuracy: float | None = Field(default=None, ge=0, le=1)
    explanation: str
    disclaimer: str = "ML association score is assistive evidence, not identity proof or a calibrated probability."


class ResolvedIdentitySummary(BaseModel):
    """Top-level identity-resolution synthesis for the human-facing report."""

    label: str
    status: Literal["supported", "partially_supported", "review_required", "insufficient"]
    username: str | None = None
    organization: str | None = None
    role: str | None = None
    department: str | None = None
    corroborated_fields: list[str] = Field(default_factory=list)
    unresolved_fields: list[str] = Field(default_factory=list)
    conflicting_fields: list[str] = Field(default_factory=list)
    connected_footprint_count: int = 0
    rationale: str
    ml_assessment: MLAssessment | None = None

class IdentityReport(BaseModel):
    most_supported_identity: ResolvedIdentitySummary
    other_records: list[RelatedRecord] = Field(default_factory=list)
    additional_observations: list[AdditionalFinding] = Field(default_factory=list)
    temporal_assessment: TemporalAssessment
    relationship_graph: RelationshipGraph
    ml_assessment: MLAssessment
    profile_count: int = 0
    conflict_count: int = 0
    record_count: int = 0

    connected_profiles: list[ConnectedProfile] = Field(default_factory=list)
    identity_label: str
    status: Literal["supported", "partially_supported", "review_required", "insufficient"]
    summary: str
    supplied_context: SearchContext
    reviewed_image_clues: list[ReviewedClue]
    fields: list[ReportField]
    supported_fields: int
    supplied_fields: int
    analyzed_sources: int
    source_domains: int
    limitations: list[str]
    sources: list[AnalyzedSource]
    method: str = "Deterministic evidence comparison; not a trained identity model."


class CorrelationResponse(BaseModel):
    status: Literal["completed", "needs_seed"]
    report: IdentityReport | None = None
    seed_profile: list[SeedSignal]
    assessments: list[CandidateAssessment]
    cross_source_support: list[CrossSourceSupport]
    message: str = (
        "Evidence-backed candidate assessment only. A supported association is not absolute proof of identity."
    )

RelatedRecord.model_rebuild()
AdditionalFinding.model_rebuild()
ConnectedProfile.model_rebuild()
