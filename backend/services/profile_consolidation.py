"""Consolidate public identity records while keeping content pages as evidence.

A profile/account/person page is a candidate identity record.  Repositories, posts,
articles and sub-pages are supporting records and must not inflate the list of
"Connected Public Profiles".
"""
import re
from urllib.parse import urlsplit

from backend.models import RelatedRecord, AdditionalFinding, CorrelationEvidence
from backend.services.platforms import candidate_id, profile_handle, classify_url, canonical_url
from backend.services.profile_records import record_identity
from backend.services.profiles import normalized, comparable

IDENTITY_TYPES = {"profile", "personal_site", "institution_page"}
IDENTITY_EVIDENCE_TYPES = IDENTITY_TYPES | {"profile_evidence"}


def _append_unique(target, values):
    for value in values:
        if value not in target:
            target.append(value)


def _generic_identity_page(profile) -> bool:
    """Conservatively recognize a directly analyzed generic person/profile page."""
    if not profile.display_name:
        return False
    direct_scoped = any(
        obs.scope_id and obs.extraction_method not in {"search_snippet", "text_match"}
        for obs in profile.source_evidence
    )
    if not direct_scoped:
        return False
    try:
        parts = [v.casefold() for v in urlsplit(profile.url).path.split("/") if v]
    except ValueError:
        return False
    if not parts:
        return False
    identity_words = {"about", "bio", "profile", "person", "people", "faculty", "staff", "team", "author", "authors"}
    if any(part in identity_words for part in parts):
        return True
    # A route ending in the subject's canonicalized name/slug is also identity-like.
    slug = re.sub(r"\W+", "", normalized(profile.display_name))
    last = re.sub(r"\W+", "", parts[-1])
    return bool(slug and last and (slug == last or last.endswith(slug)))


def consolidate(request, candidates):
    context = request.supplied_context
    personal_hosts = set()

    # Discover likely personal domains only from strong structural clues; arbitrary
    # article domains are never promoted simply because they mention the person.
    for item in candidates:
        p = item.profile
        parts = urlsplit(p.url)
        host = parts.hostname or ""
        named_host = bool(context.name and normalized(host.split(".")[0]) == normalized(context.name).replace(" ", ""))
        explicit_personal = classify_url(p.url, p.page_title or "") == "personal_site"
        named_root = parts.path in ("", "/") and p.display_name and classify_url(p.url) == "other"
        if named_host or explicit_personal or named_root:
            personal_hosts.add(host)

    classified = []
    identity_targets = set()
    for item in candidates:
        p = item.profile
        kind, target, owner = record_identity(p.url, personal_hosts)
        if kind == "page_reference" and _generic_identity_page(p) and classify_url(p.url) not in {"youtube", "github", "linkedin", "x", "twitter", "instagram"}:
            kind, target = "profile", canonical_url(p.url) or p.url
        # A known personal site's /about style page is identity evidence for the
        # canonical site even if the root URL was not independently discovered.
        if kind == "profile_evidence" and target and (urlsplit(target).hostname or "") in personal_hosts:
            identity_targets.add(target)
        if kind in IDENTITY_TYPES and target:
            identity_targets.add(target)
        classified.append((item, kind, target, owner))

    groups = {}
    other_records = []
    seen_other = set()

    for item, kind, target, owner in classified:
        p = item.profile
        # LinkedIn post ownership is only attached if a canonical /in/ candidate is
        # already present. A third-party post mentioning the seed remains audit data.
        if p.platform == "linkedin" and kind == "post" and owner:
            candidate_target = canonical_url("https://linkedin.com/in/" + owner)
            target = candidate_target if candidate_target in identity_targets else None

        owner_matches_seed = bool(owner and context.username and normalized(owner) == normalized(context.username))
        can_attach = bool(
            target
            and (
                kind in IDENTITY_EVIDENCE_TYPES
                or target in identity_targets
                or (owner_matches_seed and kind in {"repository", "post"})
            )
        )

        canonical_identity = kind in IDENTITY_TYPES or (kind == "profile_evidence" and target in identity_targets)
        record = RelatedRecord(
            discovery_queries=p.discovery_queries,
            discovery_provenance=p.source_metadata.get("discovery_provenance", []),
            url=p.url,
            record_type=kind,
            title=p.page_title,
            snippet=p.source_metadata.get("indexed_snippet"),
            access_status=p.access_status,
            evidence=item.evidence,
            disposition=(
                "Canonical identity record"
                if canonical_identity and p.url == target
                else "Attached as supporting content evidence; not an independent identity profile"
                if can_attach
                else "Reviewed candidate/content record; not established as an identity profile"
            ),
        )

        if not can_attach:
            key = canonical_url(record.url) or record.url
            if key not in seen_other:
                other_records.append(record)
                seen_other.add(key)
            continue
        groups.setdefault(target, []).append((item, record, kind, owner))

    connected = []
    for target, rows in groups.items():
        # Prefer an actual profile/root record; otherwise retain a namespace candidate
        # (e.g. only an authored X status or GitHub repository was indexed) but keep it
        # conservative and visibly insufficient.
        canonical_rows = [row for row in rows if row[2] in IDENTITY_TYPES and (canonical_url(row[0].profile.url) or row[0].profile.url) == target]
        identity_rows = [row for row in rows if row[2] in IDENTITY_EVIDENCE_TYPES]
        ranked_pool = canonical_rows or identity_rows or rows
        ranked = sorted(
            ranked_pool,
            key=lambda row: (
                {"supported": 0, "partially_supported": 1, "insufficient_evidence": 2, "conflicting": 3}[row[0].status],
                (canonical_url(row[0].profile.url) or row[0].profile.url) != target,
            ),
        )
        result = ranked[0][0].model_copy(deep=True)
        p = result.profile
        p.url = target
        p.candidate_id = candidate_id(target)
        p.domain = urlsplit(target).hostname or p.domain
        p.related_records = []
        p.record_type = "personal_site" if p.domain in personal_hosts else "profile"
        p.relationship = "profile_candidate"
        if p.domain in personal_hosts:
            p.platform = "personal_site"
        p.username = profile_handle(target)
        if p.platform == "linkedin":
            p.profile_slug = target.rstrip("/").split("/")[-1]
            p.username = None

        # Keep only supporting/content records in the profile drawer.  The canonical
        # profile itself is already represented by the card and should not be repeated.
        seen_related = set()
        for _, record, kind, _ in rows:
            if kind in IDENTITY_TYPES and (canonical_url(record.url) or record.url) == target:
                continue
            key = canonical_url(record.url) or record.url
            if key in seen_related:
                continue
            p.related_records.append(record)
            seen_related.add(key)

        if not identity_rows:
            # An owner namespace inferred only from a repository/post is not a verified
            # profile.  Preserve the handle signal when it exactly matches the seed.
            p.display_name = None
            p.source_evidence = []
            p.external_profile_links = []
            result.supporting = ["username"] if context.username and p.username and normalized(context.username) == normalized(p.username) else []
            result.missing = [
                field
                for field in ("name", "username", "organization", "role", "department")
                if getattr(context, field) and field not in result.supporting
            ]
            result.status = "insufficient_evidence"
            result.conflicts = []
            result.evidence = []
        else:
            # Merge only identity-oriented pages/subpages into the canonical profile.
            # Repositories and posts remain related evidence; they do not independently
            # supply the person's name/role/organization.
            for item, _, kind, _ in identity_rows:
                _append_unique(result.supporting, item.supporting)
                _append_unique(result.conflicts, item.conflicts)
                _append_unique(result.evidence, item.evidence)
                for field in (
                    "source_evidence",
                    "external_profile_links",
                    "discovery_queries",
                    "organization",
                    "role",
                    "department",
                    "education",
                    "location",
                    "projects",
                    "publications",
                    "events",
                    "personal_domains",
                ):
                    _append_unique(getattr(p, field), getattr(item.profile, field))
                if not p.display_name and item.profile.display_name:
                    p.display_name = item.profile.display_name
                if not p.bio_or_summary and item.profile.bio_or_summary:
                    p.bio_or_summary = item.profile.bio_or_summary
            result.missing = [field for field in result.missing if field not in result.supporting]
            if result.conflicts:
                result.status = "conflicting"

        # Attach repository names as project evidence only; they are not profiles.
        for _, record, kind, _ in rows:
            if kind == "repository":
                parts = [v for v in urlsplit(record.url).path.split("/") if v]
                if len(parts) > 1 and parts[1] not in p.projects:
                    p.projects.append(parts[1])

        result.rationale = (
            "Current differences require review."
            if result.conflicts
            else "Multiple identity signals align in this canonical public record."
            if result.status == "supported"
            else "Some candidate-specific signals align; direct access or corroboration remains limited."
            if result.status == "partially_supported"
            else "Not enough identity-specific evidence to associate this record."
        )
        connected.append(result)

    # A snippet-only canonical profile can still be supported when name + username +
    # contextual seed data align.  Indexed evidence stays explicitly labelled weaker.
    for item in connected:
        if not item.conflicts and {"name", "username"} <= set(item.supporting) and set(item.supporting) & {"role", "organization", "department"}:
            item.status = "supported"
            item.rationale = "Name, account handle and contextual seed evidence align; indexed evidence remains labelled."

    # One-hop explicit links from an already supported identity record can support a
    # target candidate. Never propagate recursively or through project/event references.
    origins = [item for item in connected if item.status == "supported"]
    for target in connected:
        for origin in origins:
            if target is origin:
                continue
            for edge in origin.profile.external_profile_links:
                if edge.relation_type not in {"explicit_profile_link", "personal_domain_link", "institutional_profile_link"}:
                    continue
                _, destination, _ = record_identity(edge.target_url, personal_hosts)
                if (destination or canonical_url(edge.target_url) or edge.target_url) != target.profile.url:
                    continue
                signal = "explicit profile cross-link from " + origin.profile.url
                if signal not in target.supporting:
                    target.supporting.append(signal)
                ref = CorrelationEvidence(
                    value=edge.target_url,
                    evidence=edge.evidence,
                    source_url=edge.evidence_origin,
                    extraction_method="indexed_link" if edge.evidence_type == "search_snippet" else "explicit_link",
                )
                if ref not in target.evidence:
                    target.evidence.append(ref)
                if "explicit_profile_link" in target.missing:
                    target.missing.remove("explicit_profile_link")
                if not target.conflicts:
                    if "name" in target.supporting and (
                        edge.evidence_type == "direct_page"
                        or bool(set(target.supporting) & {"username", "role", "organization", "department"})
                    ):
                        target.status = "supported"
                    else:
                        target.status = "partially_supported"
                    target.rationale = "A supported identity record explicitly links this candidate; remaining gaps stay visible."

    connected.sort(
        key=lambda r: (
            {"supported": 0, "partially_supported": 1, "conflicting": 2, "insufficient_evidence": 3}[r.status],
            r.profile.platform,
            r.profile.url,
        )
    )

    # New public observations are deduplicated and remain distinct from seed coverage.
    additions = {}
    for item in connected:
        for obs in item.profile.source_evidence:
            if obs.field not in {"role", "education", "project", "publication", "event", "location", "department"}:
                continue
            seed = getattr(context, obs.field, None)
            if seed and comparable(obs.field, seed) == comparable(obs.field, obs.value):
                continue
            if len(obs.value) > 200:
                continue
            key = (obs.field, comparable(obs.field, obs.value))
            entry = additions.setdefault(key, dict(field=obs.field, value=obs.value, evidence=[]))
            evidence = CorrelationEvidence(
                field=obs.field,
                value=obs.value,
                evidence=obs.evidence,
                source_url=obs.source_url or item.profile.url,
                extraction_method=obs.extraction_method,
            )
            if evidence not in entry["evidence"]:
                entry["evidence"].append(evidence)

    findings = [
        AdditionalFinding(
            **entry,
            source_count=len({canonical_url(e.source_url) or e.source_url for e in entry["evidence"]}),
        )
        for entry in additions.values()
    ]
    findings.sort(key=lambda f: (-f.source_count, f.field, f.value.casefold()))
    return connected, other_records, findings
