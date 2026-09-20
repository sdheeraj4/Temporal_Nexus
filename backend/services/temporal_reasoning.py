"""Conservative temporal reasoning for public identity observations.

This module does not invent employment/attendance histories. It only reasons over
explicit dates or temporal labels already present in extracted evidence.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from backend.models import CorrelationRequest, TemporalAssessment, TemporalClaim, TemporalIssue, TimelineEvent
from backend.services.profiles import matches, normalized, subject_observations

YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
RANGE_RE = re.compile(r"\b((?:19|20)\d{2})\s*(?:-|–|—|to)\s*((?:19|20)\d{2}|present|current)\b", re.I)
TEMPORAL_FIELDS = {"organization", "role", "department", "event", "project", "publication", "education", "location"}


def _years(text: str | None) -> tuple[int | None, int | None, int | None]:
    text = str(text or "")
    match = RANGE_RE.search(text)
    if match:
        start = int(match.group(1))
        end_raw = match.group(2).lower()
        end = None if end_raw in {"present", "current"} else int(end_raw)
        return None, start, end
    years = [int(v) for v in YEAR_RE.findall(text)]
    if not years:
        return None, None, None
    return years[0], None, None


def _label(year: int | None, start: int | None, end: int | None, context: str) -> tuple[str, int | None]:
    if start:
        return (f"{start}–{'Present' if end is None and context == 'current' else end or '?'}", start)
    if year:
        return (str(year), year)
    return ("Current" if context == "current" else "Historical" if context == "historical" else "Undated", None)


def _claim_from_observation(observation, source_url: str) -> TemporalClaim:
    year, start, end = _years(observation.observed_date or observation.evidence or observation.value)
    return TemporalClaim(
        field=observation.field,
        value=observation.value,
        source_url=source_url,
        context=observation.temporal_context,
        year=year,
        start_year=start,
        end_year=end,
        evidence=observation.evidence,
    )


def analyze_temporal(request: CorrelationRequest) -> TemporalAssessment:
    claims: list[TemporalClaim] = []
    timeline: list[TimelineEvent] = []
    issues: list[TemporalIssue] = []
    seen_claims: set[tuple] = set()
    seen_events: set[tuple] = set()

    for source in request.sources:
        if source.status != "analyzed":
            continue
        url = source.final_url or source.candidate.url
        observations = subject_observations(source, request.supplied_context)
        # If the page has scoped subject observations, use them. For legacy pages,
        # include temporal fields only when an explicit subject name is present.
        subject_named = any(o.field == "name" and o.subject_name for o in source.observations)
        for observation in observations:
            if observation.field not in TEMPORAL_FIELDS and observation.field != "date":
                continue
            if observation.field in TEMPORAL_FIELDS and not (observation.subject_name or subject_named):
                continue
            claim = _claim_from_observation(observation, url)
            key = (claim.field, normalized(claim.value), claim.source_url, claim.year, claim.start_year, claim.end_year, claim.context)
            if key not in seen_claims:
                seen_claims.add(key)
                claims.append(claim)

            # Timeline entries require an explicit date/range or temporal label. A generic
            # undated mention is not promoted to a timeline event.
            if claim.year or claim.start_year or claim.context in {"current", "historical"}:
                label, sort_year = _label(claim.year, claim.start_year, claim.end_year, claim.context)
                title = f"{claim.field.replace('_',' ').title()}: {claim.value}"
                event_key = (label, title, url)
                if event_key not in seen_events:
                    seen_events.add(event_key)
                    timeline.append(TimelineEvent(
                        year_label=label,
                        sort_year=sort_year,
                        category=claim.field,
                        title=title,
                        source_url=url,
                        evidence=claim.evidence,
                        context=claim.context,
                    ))

            seed = getattr(request.supplied_context, observation.field, None) if observation.field in {"organization", "role", "department"} else None
            if seed and observation.temporal_context == "current" and not matches(observation.field, seed, observation.value):
                issues.append(TemporalIssue(
                    field=observation.field,
                    seed_value=seed,
                    observed_value=observation.value,
                    source_url=url,
                    reason="A source explicitly presents a different current value. Historical affiliations are not treated as this conflict.",
                    severity="conflict",
                ))
            elif seed and observation.temporal_context == "historical" and not matches(observation.field, seed, observation.value):
                issues.append(TemporalIssue(
                    field=observation.field,
                    seed_value=seed,
                    observed_value=observation.value,
                    source_url=url,
                    reason="Different historical context retained as a timeline observation; it does not contradict the current seed by itself.",
                    severity="review",
                ))

    # Explicitly dated observations that appear on multiple sources are ordered, but we
    # do not convert them into employment/attendance claims unless the source said so.
    timeline.sort(key=lambda event: (event.sort_year is None, event.sort_year or 9999, event.title.casefold()))
    conflict_count = sum(issue.severity == "conflict" for issue in issues)
    review_count = sum(issue.severity == "review" for issue in issues)
    dated_count = sum(event.sort_year is not None for event in timeline)

    if conflict_count:
        status = "conflicting"
        summary = f"{conflict_count} explicit current temporal contradiction(s) require review. Historical differences are kept separate."
    elif review_count and timeline:
        status = "mixed"
        summary = "Current and historical observations coexist. Historical differences are retained without being treated as automatic contradictions."
    elif timeline:
        status = "consistent"
        summary = f"{len(timeline)} explicit temporal observation(s) were ordered with no direct current contradiction detected."
    else:
        status = "insufficient"
        summary = "No sufficiently explicit dated/temporal observations were available for a reliable timeline consistency check."

    return TemporalAssessment(status=status, claims=claims[:60], timeline=timeline[:30], issues=issues[:30], summary=summary)
