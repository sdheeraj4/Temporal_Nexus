"""Prepare deterministic literal queries locally; never execute searches or infer identities."""

import unicodedata

from backend.models import PreparedQuery, QueryReference, SearchPlanRequest, SearchPlanResponse


def _literal(value: str | None) -> str:
    """Remove operator punctuation before application-owned phrase quoting."""
    normalized = unicodedata.normalize("NFKC", value or "")
    safe = "".join(char if char.isalnum() or char == "_" else " " for char in normalized)
    return " ".join(safe.split())


def _comparison_key(value: str) -> str:
    """Canonical comparison only; do not case-fold readable display text."""
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def build_search_plan(request: SearchPlanRequest) -> SearchPlanResponse:
    """Use each candidate name/username independently; merge equivalent evidence references."""
    anchors, qualifiers = {}, {}

    def add(bucket, text, category, reference):
        text = _literal(text)
        if not text:
            return
        key = _comparison_key(text)
        if key not in bucket:
            bucket[key] = {"text": text, "evidence": []}
        evidence = (category, text, reference)
        if evidence not in bucket[key]["evidence"]:
            bucket[key]["evidence"].append(evidence)

    for field in ("name", "username", "organization", "role", "department"):
        add(qualifiers if field not in ("name", "username") else anchors,
            getattr(request.supplied_context, field), field,
            QueryReference(source="supplied_context", field=field))
    for clue in request.clues:
        if not clue.selected:
            continue
        bucket = anchors if clue.category in ("name", "username") else qualifiers
        if clue.category not in ("name", "username", "event", "organization"):
            continue
        add(bucket, clue.corrected_text, clue.category,
            QueryReference(source="clue", field="corrected_text", clue_id=clue.clue_id))

    if not anchors:
        return SearchPlanResponse(status="needs_context", queries=[], message=(
            "Supply a name or username, or select a reviewed clue labeled name or username."
        ))
    candidates = {}

    def emit(anchor, qualifier=None):
        terms = [anchor["text"]]
        evidence = list(anchor["evidence"])
        if qualifier:
            if _comparison_key(qualifier["text"]) == _comparison_key(anchor["text"]):
                return
            terms.append(qualifier["text"])
            evidence.extend(qualifier["evidence"])
        query = " ".join(f'"{term}"' for term in terms)
        # Quoted AND phrases are equivalent regardless of their order.
        key = tuple(sorted(_comparison_key(term) for term in terms))
        candidate = candidates.setdefault(key, {"query": query, "evidence": [], "basic": qualifier is None})
        for item in evidence:
            if item not in candidate["evidence"]:
                candidate["evidence"].append(item)

    # One primary candidate's focused queries first, then separate alternatives.
    for anchor in anchors.values():
        emit(anchor)
        for qualifier in qualifiers.values():
            emit(anchor, qualifier)
    queries = []
    # Merge all contributors before applying the limit, including late duplicates.
    for candidate in list(candidates.values())[:6]:
        references, descriptions = [], []
        for category, value, reference in candidate["evidence"]:
            if reference not in references:
                references.append(reference)
            description = f'{category} "{value}"'
            if description not in descriptions:
                descriptions.append(description)
        prefix = "Basic identity query using " if candidate["basic"] else "Focus a future search using "
        queries.append(PreparedQuery(query=candidate["query"], reason=prefix + "; ".join(descriptions) + ".",
                                     references=references))
    return SearchPlanResponse(status="prepared", queries=queries, platform_queries=platform_queries(request), message=(
        "Queries use supplied context and selected corrections only. Candidates remain separate; "
        "no identity, attendance, or employment has been verified. Other-category clues are not used."
    ))


def platform_queries(request):
    """Three grouped site filters; canonical values always precede reviewed clues."""
    context = request.supplied_context
    anchor_field = "name" if _literal(context.name) else "username"
    anchor = _literal(getattr(context, anchor_field))
    refs = [QueryReference(source="supplied_context", field=anchor_field)]
    if not anchor:
        clue = next((c for c in request.clues if c.selected and c.category in {"name", "username"} and _literal(c.corrected_text)), None)
        if not clue:
            return []
        anchor = _literal(clue.corrected_text)
        refs = [QueryReference(source="clue", field="corrected_text", clue_id=clue.clue_id)]
    organization = _literal(context.organization)
    result = []
    for sites, academic in [("site:linkedin.com/in OR site:scholar.google.com", True),
                            ("site:github.com", False),
                            ("site:instagram.com OR site:youtube.com OR site:x.com OR site:twitter.com", False)]:
        terms, references = [anchor], list(refs)
        if not academic and _literal(context.username):
            terms = [_literal(context.username)]
            references = [QueryReference(source="supplied_context", field="username")]
        if academic and organization:
            terms.append(organization)
            references.append(QueryReference(source="supplied_context", field="organization"))
        query = " ".join(f'"{term}"' for term in terms) + " (" + sites + ")"
        result.append(PreparedQuery(query=query, references=references,
            reason="Find unverified platform candidates using " + "; ".join(terms) + ". Site filters are application-owned; no profile ownership is implied."))
    return result
