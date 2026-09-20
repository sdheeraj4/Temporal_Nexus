"""Conservative, source-scoped report. Coverage is never identity probability."""
from urllib.parse import urlsplit, urlunsplit
import re
import unicodedata
from backend.models import IdentityReport, ReportField, CorrelationEvidence, ResolvedIdentitySummary

FIELDS = ("name", "username", "organization", "role", "department")


def normalized(value):
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value or "").casefold()))


def source_key(url):
    from backend.services.platforms import canonical_url
    return canonical_url(url) or url


def build_report(request):
    from backend.services.profiles import subject_observations, matches, connected_profiles
    records = {}
    for source in request.sources:
        key = source_key(source.final_url or source.candidate.url)
        if key not in records:
            records[key] = source.model_copy(deep=True)
        elif source.status == "analyzed":
            records[key].status = "analyzed"
            records[key].observations.extend(o for o in source.observations if o not in records[key].observations)
    sources = list(records.values())
    analyzed = [s for s in sources if s.status == "analyzed"]
    context = request.supplied_context
    fields = []
    for field in FIELDS:
        supplied = getattr(context, field, None)
        if not supplied or not supplied.strip():
            continue
        evidence, supported, partial, conflicting = [], False, False, False
        for source in analyzed:
            # A page-wide mention cannot establish that an affiliation belongs to the candidate.
            observations = subject_observations(source, context) if any(o.scope_id for o in source.observations) else source.observations
            explicit_identity = [o for o in observations if o.field in {"name", "username"}
                                 and o.extraction_method != "text_match"]
            comparable = [o for o in explicit_identity if getattr(context, o.field, None)]
            anchored = any(normalized(o.value) == normalized(getattr(context, o.field)) for o in comparable)
            ambiguous = any(normalized(o.value) != normalized(getattr(context, o.field)) for o in comparable)
            for observation in observations:
                if observation.field != field:
                    continue
                exact = matches(field, supplied, observation.value)
                explicit = observation.extraction_method != "text_match"
                linked = anchored and not ambiguous
                strong = exact and explicit and linked and observation.temporal_context != "historical"
                supported |= strong
                partial |= (exact and not strong) or field not in {"name", "username"}
                conflicting |= field in {"name", "username"} and explicit and not exact
                evidence.append(CorrelationEvidence(value=observation.value, evidence=observation.evidence,
                    extraction_method=observation.extraction_method, source_url=source.final_url or source.candidate.url))
        if conflicting:
            status, reason = "conflicting", "A candidate has a different explicit identity value. Keep conflicting records separate and review the evidence."
        elif supported:
            status, reason = "supported", "An exact normalized value occurs in a record anchored by a matching explicit name or username. This is record support, not identity proof."
        elif partial:
            status, reason = "partial", "Related or page-level evidence exists, but the supplied value or its link to this person needs review. Different affiliations can be historical."
        elif not analyzed:
            status, reason = "not_assessed", "No readable source was available for comparison."
        else:
            status, reason = "missing", "No usable observation supports this field. Absence is not a contradiction."
        fields.append(ReportField(field=field, supplied_value=supplied, status=status, explanation=reason, evidence=evidence))
    count = sum(f.status == "supported" for f in fields)
    conflict = any(f.status == "conflicting" for f in fields)
    # Require multiple supported fields within ONE candidate, not accidental matches across strangers.
    coherent = False
    for source in analyzed:
        supported_here = {f.field for f in fields if f.status == "supported" and any(
            e.source_url == (source.final_url or source.candidate.url) and normalized(e.value) == normalized(f.supplied_value)
            and e.extraction_method != "text_match" for e in f.evidence)}
        coherent |= len(supported_here) >= 2 and bool(supported_here & {"name", "username"})
    status = "review_required" if conflict else "supported" if coherent else "insufficient"
    summary = {
        "supported": "Multiple supplied fields are supported within a candidate record. Review the linked evidence before accepting the association.",
        "review_required": "Explicit identity differences need review. These candidate records have not been merged into one person.",
        "insufficient": "The available evidence is not sufficient to associate a candidate with the supplied identity."
    }[status]
    from backend.services.profile_consolidation import consolidate
    from backend.services.temporal_reasoning import analyze_temporal
    from backend.services.ml_correlation import score_profiles, overall_assessment
    from backend.services.relationship_graph import build_relationship_graph

    temporal = analyze_temporal(request)
    connected, other_records, findings = consolidate(request, connected_profiles(request))
    score_profiles(connected, context, temporal)
    connected.sort(key=lambda item: (item.status == "conflicting", -(item.ml_score or 0.0), item.profile.platform, item.profile.url))
    # Snippets support a field only when explicitly linked to the candidate;
    # source counts distinguish indexed observations from directly analyzed pages.
    for field in fields:
        for profile in connected:
            for evidence in profile.evidence:
                if evidence.field == field.field and matches(field.field, field.supplied_value, evidence.value):
                    if evidence not in field.evidence: field.evidence.append(evidence)
            if field.field in profile.supporting and field.status != "conflicting":
                field.status = "supported"
        if any(c.startswith(field.field+":") for p in connected for c in p.conflicts):
            field.status="conflicting"
            field.explanation="Explicit current differences require review; supporting evidence is retained."
        matching=[e for e in field.evidence if matches(field.field,field.supplied_value,e.value)]
        field.direct_sources=len({source_key(e.source_url) for e in matching if e.extraction_method not in {"search_snippet","text_match"}})
        field.indexed_sources=len({source_key(e.source_url) for e in matching if e.extraction_method=="search_snippet"})
        if field.status=="supported":
            field.explanation=f"Supported by {field.direct_sources} direct/URL source(s) and {field.indexed_sources} indexed source(s). Indexed text remains weaker, unverified evidence."
    count=sum(f.status=="supported" for f in fields)
    conflicts={f.field for f in fields if f.status=="conflicting"}
    conflicts.update(c.split(':',1)[0] for p in connected for c in p.conflicts)
    coherent_indexed=any({'name','username'} <= set(p.supporting) and bool(set(p.supporting)&{'role','organization','department'}) for p in connected)
    if conflicts:
        status,summary="review_required","Direct differences require review; supporting evidence is retained."
    elif any(p.status=="supported" for p in connected) or coherent_indexed:
        status,summary="supported","A candidate record links the name to contextual evidence or an explicit supported-profile link."
    elif any(p.status=="partially_supported" for p in connected):
        status,summary="partially_supported","Candidate-specific signals align, but access or linkage evidence remains incomplete."
    elif not coherent:
        status,summary="insufficient","Only isolated or unlinked evidence is available."
    all_urls={source_key(s.final_url or s.candidate.url) for s in sources}
    all_urls.update(source_key(c.url) for c in request.discovered_candidates)

    corroborated = [field.field for field in fields if field.status == "supported"]
    unresolved = [field.field for field in fields if field.status in {"partial", "missing", "not_assessed"}]
    conflicting_fields = [field.field for field in fields if field.status == "conflicting"]
    footprint_count = sum(profile.status in {"supported", "partially_supported"} for profile in connected)
    if status == "supported":
        if fields and len(corroborated) == len(fields):
            rationale = "All supplied comparable identity signals are corroborated by the available public evidence, with no direct contradiction detected."
        else:
            rationale = "Multiple supplied identity signals are corroborated by public records, with no direct contradiction detected in the assessed evidence."
    elif status == "partially_supported":
        rationale = "Some identity signals and public records align, but important evidence remains missing or only partially accessible."
    elif status == "review_required":
        rationale = "Supporting evidence exists, but one or more direct differences must be reviewed before associating the records."
    else:
        rationale = "The available evidence does not yet connect enough identity-specific signals to resolve a supported public identity."

    ml_assessment = overall_assessment(connected, context, temporal)
    relationship_graph = build_relationship_graph(context, connected, fields)

    temporal_conflict_fields = {issue.field for issue in temporal.issues if issue.severity == "conflict"}
    if temporal_conflict_fields:
        conflicting_fields = sorted(set(conflicting_fields) | temporal_conflict_fields)

    if temporal.status == "conflicting" and status != "review_required":
        status = "review_required"
        summary = "Temporal evidence contains an explicit current contradiction that requires review; supporting evidence is retained."
        rationale = "The public footprint contains supporting identity signals, but an explicit current temporal contradiction prevents a stronger association."
    elif temporal.status == "mixed" and status == "supported":
        rationale += " Historical differences are retained separately and do not automatically contradict the current seed."

    if ml_assessment.band == "strong" and status in {"supported", "partially_supported"}:
        rationale += " The custom ML baseline independently assigns a strong association score; deterministic evidence remains authoritative."
    elif ml_assessment.band == "weak" and status == "supported":
        rationale += " The ML baseline is cautious because several machine-readable signals remain sparse; the deterministic evidence shown below drives the supported status."

    most_supported_identity = ResolvedIdentitySummary(
        label=context.name or context.username or "Identity under review",
        status=status,
        username=context.username,
        organization=context.organization,
        role=context.role,
        department=context.department,
        corroborated_fields=corroborated,
        unresolved_fields=unresolved,
        conflicting_fields=conflicting_fields,
        connected_footprint_count=footprint_count,
        rationale=rationale,
        ml_assessment=ml_assessment,
    )

    return IdentityReport(most_supported_identity=most_supported_identity, connected_profiles=connected, other_records=other_records, additional_observations=findings,
        temporal_assessment=temporal, relationship_graph=relationship_graph, ml_assessment=ml_assessment,
        profile_count=len(connected), conflict_count=len(set(conflicts) | temporal_conflict_fields), record_count=len(all_urls), identity_label=context.name or context.username or "Identity under review", status=status,
        summary=summary, supplied_context=context, reviewed_image_clues=request.clues, fields=fields,
        supported_fields=count, supplied_fields=len(fields), analyzed_sources=len(analyzed),
        source_domains=len({urlsplit(url).hostname for url in all_urls}), sources=sources,
        method="Deterministic evidence comparison assisted by a locally trained synthetic logistic-regression baseline; explicit conflicts always override model scoring.",
        limitations=["Field coverage is not a confidence percentage or a calibrated probability.",
                     "The ML association score is trained on controlled synthetic feature pairs and is assistive, not identity proof.",
                     "Different URLs or domains do not establish independent evidence.",
                     "OCR and reviewer corrections remain observations, not verified personal facts.",
                     "Timeline entries are created only from explicit dates/temporal labels; undated records are not converted into employment, attendance, or activity claims."])
