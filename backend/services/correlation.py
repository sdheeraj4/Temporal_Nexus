"""Deterministic identity-correlation baseline with explicit evidence and conflicts."""

from __future__ import annotations

from collections import defaultdict
import re
import unicodedata

from backend.models import (
    CandidateAssessment,
    CorrelationEvidence,
    CorrelationRequest,
    CorrelationResponse,
    CrossSourceSupport,
    SeedReference,
    SeedSignal,
    SignalComparison,
)

IDENTITY_FIELDS = {"name", "username", "organization", "role", "department", "event"}
CROSS_SOURCE_FIELDS = {"name", "username", "organization", "role", "event", "project", "publication", "date", "location"}


def _normalize(value: str, field: str | None = None) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    if field == "username":
        value = value.lstrip("@").strip()
    return " ".join(re.findall(r"[\w]+", value, flags=re.UNICODE))


def _seed_profile(request: CorrelationRequest) -> list[SeedSignal]:
    grouped: dict[tuple[str, str], dict] = {}

    def add(field: str, value: str | None, reference: SeedReference):
        if field not in IDENTITY_FIELDS or not value or not str(value).strip():
            return
        cleaned = str(value).strip().lstrip("@") if field == "username" else str(value).strip()
        normalized = _normalize(cleaned, field)
        if not normalized:
            return
        key = (field, normalized)
        entry = grouped.setdefault(key, {"field": field, "value": cleaned, "references": []})
        if reference not in entry["references"]:
            entry["references"].append(reference)

    for field in ("name", "username", "organization", "role", "department"):
        add(field, getattr(request.supplied_context, field, None), SeedReference(source="supplied_context"))
    for clue in request.clues:
        if clue.selected and clue.category in IDENTITY_FIELDS:
            supplied = getattr(request.supplied_context, clue.category, None)
            if supplied and _normalize(supplied, clue.category) != _normalize(clue.corrected_text, clue.category):
                continue  # Preserve canonical supplied value; retain OCR separately in the report.
            add(clue.category, clue.corrected_text, SeedReference(source="image_clue", clue_id=clue.clue_id))

    order = {"name": 0, "username": 1, "organization": 2, "role": 3, "department": 4, "event": 5}
    return [SeedSignal(**item) for item in sorted(grouped.values(), key=lambda item: (order[item["field"]], item["value"].casefold()))]


def _matches(field: str, seed_value: str, source_value: str) -> bool:
    seed = _normalize(seed_value, field)
    source = _normalize(source_value, field)
    if not seed or not source:
        return False
    if field == "username":
        return seed == source
    if seed == source:
        return True
    # Organization/event names often include harmless suffixes (e.g. "ABC College of Engineering").
    if field in {"organization", "event"} and min(len(seed), len(source)) >= 6:
        return seed in source or source in seed
    # Names remain conservative: exact normalized token sequence only.
    return False


def _comparison(source, signal: SeedSignal) -> SignalComparison:
    relevant = [item for item in source.observations if item.field == signal.field]
    matching = [item for item in relevant if _matches(signal.field, signal.value, item.value)]

    source_url = source.final_url or source.candidate.url
    if matching:
        evidence = [CorrelationEvidence(
            value=item.value,
            evidence=item.evidence,
            extraction_method=item.extraction_method,
            source_url=source_url,
        ) for item in matching]
        return SignalComparison(
            field=signal.field,
            seed_value=signal.value,
            status="match",
            source_values=[item.value for item in relevant],
            evidence=evidence,
            explanation=f'The source contains evidence matching the seed {signal.field} "{signal.value}".',
        )

    if not relevant:
        return SignalComparison(
            field=signal.field,
            seed_value=signal.value,
            status="missing",
            source_values=[],
            evidence=[],
            explanation=f'This source does not provide a usable {signal.field} observation.',
        )

    # Different explicit names/usernames are meaningful conflicts. Different organizations/events
    # can reflect historical or multiple affiliations, so they are "no_support" rather than a hard conflict.
    status = "conflict" if signal.field in {"name", "username"} else "no_support"
    evidence = [CorrelationEvidence(
        value=item.value,
        evidence=item.evidence,
        extraction_method=item.extraction_method,
        source_url=source_url,
    ) for item in relevant[:5]]
    explanation = (
        f'The source provides a different explicit {signal.field} from the seed value "{signal.value}".'
        if status == "conflict" else
        f'The source has {signal.field} information, but it does not support the seed value "{signal.value}".'
    )
    return SignalComparison(
        field=signal.field,
        seed_value=signal.value,
        status=status,
        source_values=[item.value for item in relevant],
        evidence=evidence,
        explanation=explanation,
    )


def _assessment(source, seed: list[SeedSignal]) -> CandidateAssessment:
    comparisons = [_comparison(source, signal) for signal in seed]
    matched = sorted({item.field for item in comparisons if item.status == "match"})
    conflicts = sorted({item.field for item in comparisons if item.status == "conflict"})

    if conflicts:
        status = "conflicting"
        rationale = (
            "At least one explicit identity field conflicts with the seed profile. Keep this record separate "
            "unless stronger independent evidence resolves the conflict."
        )
    elif len(matched) >= 2 and ("name" in matched or "username" in matched):
        status = "supported"
        rationale = (
            f"The candidate is supported by multiple seed fields ({', '.join(matched)}). "
            "This is an evidence-backed association, not absolute proof of identity."
        )
    elif len(matched) == 1:
        status = "uncertain"
        rationale = (
            f"Only one seed field ({matched[0]}) is supported. Additional independent evidence is needed "
            "before associating this source with the identity."
        )
    else:
        status = "insufficient"
        rationale = "No seed identity field is supported strongly enough by this source."

    return CandidateAssessment(
        source_url=source.final_url or source.candidate.url,
        page_title=source.page_title or source.candidate.title,
        status=status,
        matched_fields=matched,
        conflicting_fields=conflicts,
        comparisons=comparisons,
        rationale=rationale,
    )


def _cross_source_support(request: CorrelationRequest) -> list[CrossSourceSupport]:
    buckets: dict[tuple[str, str], dict] = {}
    for source in request.sources:
        if source.status != "analyzed":
            continue
        source_url = source.final_url or source.candidate.url
        for observation in source.observations:
            if observation.field not in CROSS_SOURCE_FIELDS:
                continue
            key = (observation.field, _normalize(observation.value, observation.field))
            if not key[1]:
                continue
            bucket = buckets.setdefault(key, {
                "field": observation.field,
                "value": observation.value,
                "source_urls": [],
            })
            if source_url not in bucket["source_urls"]:
                bucket["source_urls"].append(source_url)

    results = [CrossSourceSupport(**item) for item in buckets.values() if len(item["source_urls"]) >= 2]
    return sorted(results, key=lambda item: (item.field, item.value.casefold()))


def correlate(request: CorrelationRequest) -> CorrelationResponse:
    """Compare analyzed candidate records with the seed identity using transparent rules."""
    from backend.services.identity_report import build_report
    report = build_report(request)
    seed = _seed_profile(request)
    if not seed:
        return CorrelationResponse(
            status="needs_seed",
            report=report,
            seed_profile=[],
            assessments=[],
            cross_source_support=[],
            message="Add at least one supplied or selected identity clue before correlation.",
        )

    analyzed = [source for source in request.sources if source.status == "analyzed"]
    assessments = [_assessment(source, seed) for source in analyzed]
    return CorrelationResponse(
        status="completed",
        report=report,
        seed_profile=seed,
        assessments=assessments,
        cross_source_support=_cross_source_support(request),
    )
