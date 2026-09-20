"""Bounded, weak observations from identity-oriented indexed snippets only.

Search snippets are useful when a public profile page is inaccessible, but they are
never treated as equivalent to a directly analyzed page.  This module only promotes
snippet text when the URL is identity-oriented and the snippet/title is clearly
anchored to the supplied person (or to the supplied account handle).
"""
import re
from urllib.parse import urlsplit

from backend.models import SourceObservation
from backend.services.platforms import canonical_url, profile_handle
from backend.services.profile_records import record_identity


def _norm(value: str | None) -> str:
    return " ".join(re.findall(r"\w+", (value or "").casefold()))


def _anchored_to_subject(candidate, context, snippets: list[str]) -> bool:
    """Return True only for an identity-oriented candidate clearly anchored to seed."""
    name = context.name or ""
    username = context.username or ""
    name_n = _norm(name)
    username_n = _norm(username)
    handle = profile_handle(candidate.url)

    if username_n and handle and _norm(handle) == username_n:
        return True

    title_n = _norm(candidate.title)
    if name_n and name_n in title_n:
        return True

    # Identity pages frequently begin snippets with a name, honorific, or short
    # self-introduction.  We only inspect the beginning so an arbitrary mention in
    # an article/post cannot become subject evidence.
    for text in snippets:
        start = _norm(text[:320])
        if not name_n or not start:
            continue
        if start.startswith(name_n) or start.startswith("dr " + name_n) or start.startswith("prof " + name_n) or start.startswith("professor " + name_n):
            return True
        if name_n in start[:140]:
            prefix = start[: start.find(name_n)].strip()
            if prefix in {"", "hello i m", "hello im", "hi i m", "hi im", "about", "profile"}:
                return True
    return False


def indexed_observations(candidate, context):
    """Extract conservative subject observations from indexed metadata/snippets.

    These observations remain labelled ``search_snippet`` so downstream reporting can
    keep them visibly weaker than direct-page evidence.
    """
    if not context.name and not context.username:
        return []

    host = urlsplit(candidate.url).hostname or ""
    personal = set()
    if context.name and _norm(host.split(".")[0]) == _norm(context.name).replace(" ", ""):
        personal.add(host)
    kind, _, _ = record_identity(candidate.url, personal)
    if kind not in {"profile", "personal_site", "institution_page"}:
        return []

    snippets = [s for s in [candidate.snippet, *[p.snippet for p in candidate.provenance]] if s]
    snippets = list(dict.fromkeys(snippets))[:9]
    if not snippets and not candidate.title:
        return []
    if not _anchored_to_subject(candidate, context, snippets):
        return []

    result: list[SourceObservation] = []

    def add(field: str, value: str, evidence: str):
        if not value:
            return
        item = SourceObservation(
            field=field,
            value=value,
            evidence=(evidence or value)[:800],
            extraction_method="search_snippet",
            subject_name=context.name,
            scope_id="indexed-profile",
            source_url=candidate.url,
        )
        if item not in result:
            result.append(item)

    title_n = _norm(candidate.title)
    if context.name and _norm(context.name) in title_n:
        add("name", context.name, candidate.title)

    handle = profile_handle(candidate.url)
    if context.username and handle and _norm(handle) == _norm(context.username):
        add("username", context.username, "Public profile handle in URL: " + candidate.url)

    for text in snippets:
        plain = _norm(text)
        if context.name and _norm(context.name) in plain[:260]:
            add("name", context.name, text)

        # Once the candidate is subject-anchored, exact canonical seed phrases in
        # the indexed profile snippet may support contextual fields.  They stay
        # explicitly weaker search-snippet evidence downstream.
        for field in ("role", "organization", "department"):
            value = getattr(context, field, None)
            value_n = _norm(value)
            if value_n and (" " + value_n + " ") in (" " + plain + " "):
                add(field, value, text)

        # Explicit labelled links only; arbitrary domains mentioned in prose are not
        # interpreted as ownership edges.
        for match in re.finditer(
            r"\b(Website|Personal website|Homepage|Twitter/X|Twitter|X|LinkedIn|GitHub)\s*:\s*([^\s,;|]+)",
            text,
            re.I,
        ):
            label, value = match.groups()
            value = value.rstrip(".)]")
            low = label.lower()
            if low in {"twitter/x", "twitter", "x"} and value.startswith("@"):
                value = "https://x.com/" + value[1:]
            elif low == "linkedin" and value.startswith("in/"):
                value = "https://linkedin.com/" + value
            elif low == "github" and value.startswith("@"):
                value = "https://github.com/" + value[1:]
            elif not value.startswith(("http://", "https://")) and "." in value:
                value = "https://" + value
            target = canonical_url(value)
            if target:
                add("profile_link", target, match.group(0))

    return result
