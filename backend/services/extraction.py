"""Extract conservative, subject-focused observations from retrieved public pages."""

from __future__ import annotations

from html.parser import HTMLParser
from datetime import date as calendar_date
import json
import re
import unicodedata
from urllib.parse import urljoin, urlsplit

from backend.models import PageAttribution, ReviewedClue, SearchContext, SourceObservation
from backend.services.source_reader import SourceDocument

MAX_VISIBLE_TEXT = 80_000
MAX_EXCERPT = 2_400
SOCIAL_DOMAINS = {
    "github.com", "www.github.com", "linkedin.com", "www.linkedin.com",
    "youtube.com", "www.youtube.com", "youtu.be", "x.com", "www.x.com",
    "twitter.com", "www.twitter.com", "instagram.com", "www.instagram.com",
}
ARTICLE_TYPES = {"article", "scholarlyarticle", "techarticle", "newsarticle", "blogposting"}


class _PageParser(HTMLParser):
    BLOCK_TAGS = {
        "p", "div", "section", "article", "main", "header", "li", "br",
        "h1", "h2", "h3", "h4", "h5", "h6", "table", "tr", "td", "th",
    }
    IGNORED_TAGS = {"style", "noscript", "svg", "canvas", "template", "nav", "footer", "aside"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.h1_parts: list[str] = []
        self.visible_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.links: list[tuple[str, str]] = []
        self.json_ld_raw: list[str] = []
        self._title_depth = 0
        self._h1_depth = 0
        self._ignored_depth = 0
        self._json_ld = False
        self._json_buffer: list[str] = []
        self._link_href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attributes = {str(key).lower(): value for key, value in attrs if key}
        if tag in self.IGNORED_TAGS:
            self._ignored_depth += 1
        if tag == "script":
            script_type = (attributes.get("type") or "").lower().split(";", 1)[0].strip()
            if script_type == "application/ld+json":
                self._json_ld = True
                self._json_buffer = []
            else:
                self._ignored_depth += 1
        if tag == "title":
            self._title_depth += 1
        if tag == "h1":
            self._h1_depth += 1
        if tag == "meta":
            key = (attributes.get("name") or attributes.get("property") or "").strip().lower()
            value = (attributes.get("content") or "").strip()
            if key and value and key not in self.meta:
                self.meta[key] = value
        if tag == "a" and attributes.get("href"):
            self._link_href = attributes["href"].strip()
            self._link_text = []
        if tag in self.BLOCK_TAGS and self._ignored_depth == 0:
            self.visible_parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "script":
            if self._json_ld:
                raw = "".join(self._json_buffer).strip()
                if raw:
                    self.json_ld_raw.append(raw)
                self._json_ld = False
                self._json_buffer = []
            elif self._ignored_depth:
                self._ignored_depth -= 1
        elif tag in self.IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag == "h1" and self._h1_depth:
            self._h1_depth -= 1
        if tag == "a" and self._link_href is not None:
            text = _clean(" ".join(self._link_text), 300)
            self.links.append((self._link_href, text))
            self._link_href = None
            self._link_text = []
        if tag in self.BLOCK_TAGS and self._ignored_depth == 0:
            self.visible_parts.append("\n")

    def handle_data(self, data):
        if self._json_ld:
            self._json_buffer.append(data)
            return
        if self._ignored_depth:
            return
        if self._title_depth:
            self.title_parts.append(data)
        if self._h1_depth:
            self.h1_parts.append(data)
        self.visible_parts.append(data)
        if self._link_href is not None:
            self._link_text.append(data)


def _clean(value, limit=500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _evidence(label: str, value: str) -> str:
    return _clean(f'{label}: "{value}"', 800)


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = value.replace("@", " ")
    return " ".join(re.findall(r"[\w]+", value, flags=re.UNICODE))


def _names(value) -> list[str]:
    if isinstance(value, str):
        return [_clean(value)] if _clean(value) else []
    if isinstance(value, dict):
        name = _clean(value.get("name"))
        return [name] if name else []
    if isinstance(value, list):
        output = []
        for item in value:
            output.extend(_names(item))
        return output
    return []


def _types(node: dict) -> set[str]:
    value = node.get("@type")
    if isinstance(value, str):
        return {value.casefold()}
    if isinstance(value, list):
        return {item.casefold() for item in value if isinstance(item, str)}
    return set()


def _walk_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _json_roots(raw_items: list[str]):
    for raw in raw_items:
        try:
            yield json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue


def _resolve_entity(value, by_id: dict[str, dict]) -> list[dict]:
    output: list[dict] = []
    if isinstance(value, dict):
        if set(value) == {"@id"} and value.get("@id") in by_id:
            output.append(by_id[value["@id"]])
        else:
            output.append(value)
    elif isinstance(value, str) and value in by_id:
        output.append(by_id[value])
    elif isinstance(value, list):
        for item in value:
            output.extend(_resolve_entity(item, by_id))
    return output


def _subject_person_nodes(roots: list[object]) -> list[dict]:
    """Select JSON-LD Person nodes that are page subjects, not article authors."""
    all_nodes = [node for root in roots for node in _walk_json(root)]
    by_id = {node.get("@id"): node for node in all_nodes if isinstance(node.get("@id"), str)}
    candidates: list[dict] = []

    def add_person(node):
        if isinstance(node, dict) and "person" in _types(node) and node not in candidates:
            candidates.append(node)

    for root in roots:
        if isinstance(root, dict):
            add_person(root)
        elif isinstance(root, list):
            for item in root:
                # A top-level Person in a simple array is commonly a subject entity.
                add_person(item)

    for node in all_nodes:
        types = _types(node)
        if types & {"profilepage", "webpage", "aboutpage", "person"} or types & ARTICLE_TYPES:
            for key in ("mainEntity", "about"):
                for entity in _resolve_entity(node.get(key), by_id):
                    add_person(entity)

    # In @graph documents, a ProfilePage/WebPage may reference its subject by @id.
    return candidates


def _parse_html(document: SourceDocument) -> _PageParser:
    parser = _PageParser()
    if document.content_type in ("text/html", "application/xhtml+xml"):
        try:
            parser.feed(document.text)
            parser.close()
        except Exception:
            pass
    else:
        parser.visible_parts.append(document.text)
    return parser


def _seed_hints(context: SearchContext | None, clues: list[ReviewedClue] | None) -> list[tuple[str, str]]:
    hints: list[tuple[str, str]] = []
    if context:
        for field in ("name", "username", "organization"):
            value = _clean(getattr(context, field, None), 200)
            if value:
                hints.append((field, value))
    for clue in clues or []:
        if clue.selected and clue.category in {"name", "username", "organization", "event"}:
            value = _clean(clue.corrected_text, 200)
            if value:
                hints.append((clue.category, value))
    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for field, value in hints:
        key = (field, _normalize(value))
        if key[1] and key not in seen:
            seen.add(key)
            deduped.append((field, value))
    return deduped


def _candidate_profile_observations(candidate_url: str | None) -> list[tuple[str, str, str]]:
    from backend.services.platforms import profile_handle
    handle = profile_handle(candidate_url or "")
    if not handle:
        return []
    return [("username", handle, f"Username in candidate profile URL: {candidate_url}")]


def _extract_page_attribution(parser: _PageParser, roots: list[object], subject_links: set[str], base_url: str) -> PageAttribution:
    authors: list[str] = []
    publishers: list[str] = []
    site_social_links: list[str] = []

    def add_unique(target: list[str], value: str):
        value = _clean(value, 500)
        if value and value.casefold() not in {item.casefold() for item in target}:
            target.append(value)

    if parser.meta.get("author"):
        add_unique(authors, parser.meta["author"])

    for root in roots:
        for node in _walk_json(root):
            if _types(node) & ARTICLE_TYPES or _types(node) & {"webpage", "profilepage"}:
                for name in _names(node.get("author")):
                    add_unique(authors, name)
                for name in _names(node.get("publisher")):
                    add_unique(publishers, name)

    for href, _ in parser.links:
        try:
            absolute = urljoin(base_url, href)
            parts = urlsplit(absolute)
        except ValueError:
            continue
        host = (parts.hostname or "").lower()
        if parts.scheme in ("http", "https") and host in SOCIAL_DOMAINS and parts.path.strip("/"):
            if absolute not in subject_links:
                add_unique(site_social_links, absolute)

    return PageAttribution(authors=authors[:10], publishers=publishers[:10], site_social_links=site_social_links[:20])


def _extract_details(
    document: SourceDocument,
    context: SearchContext | None = None,
    clues: list[ReviewedClue] | None = None,
    candidate_url: str | None = None,
):
    parser = _parse_html(document)
    page_title = _clean(" ".join(parser.title_parts), 1000) or _clean(parser.meta.get("og:title"), 1000) or None
    description = _clean(parser.meta.get("description") or parser.meta.get("og:description"), 2000) or None
    h1 = _clean(" ".join(parser.h1_parts), 500)
    visible = re.sub(r"[ \t\f\v]+", " ", "".join(parser.visible_parts))
    lines = [_clean(line, 1200) for line in re.split(r"[\r\n]+", visible) if _clean(line, 1200)]
    visible_text = "\n".join(lines)[:MAX_VISIBLE_TEXT]
    excerpt = visible_text[:MAX_EXCERPT]

    observations: list[SourceObservation] = []
    seen: set[tuple[str, str]] = set()

    subject_name = scope_id = observed_date = None
    temporal_context = "unknown"

    def add(field: str, value, evidence: str, method: str):
        clean = _clean(value, 500)
        if not clean:
            return
        key = (field, _normalize(clean), scope_id)
        if not key[1] or key in seen:
            return
        seen.add(key)
        observations.append(SourceObservation(
            field=field,
            value=clean,
            evidence=_clean(evidence, 800),
            extraction_method=method,
            subject_name=subject_name, scope_id=scope_id, observed_date=observed_date, temporal_context=temporal_context,
        ))

    roots = list(_json_roots(parser.json_ld_raw))
    subject_people = _subject_person_nodes(roots)
    subject_links: set[str] = set()

    # Only JSON-LD Person nodes identified as the page subject can populate identity fields.
    for index, node in enumerate(subject_people):
        subject_name = next(iter(_names(node.get("name"))), None)
        scope_id = f"json-person-{index}"
        observed_date = _clean(node.get("endDate") or node.get("dateModified"), 100) or None
        temporal_context = "historical" if node.get("endDate") else "unknown"
        if observed_date and re.match(r"^(?:19|20)\d{2}", observed_date) and int(observed_date[:4]) < calendar_date.today().year:
            temporal_context = "historical"
        for name in _names(node.get("name")):
            add("name", name, _evidence("JSON-LD subject Person.name", name), "json_ld")
        for alias in _names(node.get("alternateName")):
            field = "username" if alias.startswith("@") or " " not in alias else "name"
            add(field, alias.lstrip("@") if field == "username" else alias,
                _evidence("JSON-LD subject Person.alternateName", alias), "json_ld")
        role = _clean(node.get("jobTitle"))
        if role:
            add("role", role, _evidence("JSON-LD subject Person.jobTitle", role), "json_ld")
        for key in ("worksFor", "affiliation", "memberOf", "alumniOf"):
            for organization in _names(node.get(key)):
                add("education" if key == "alumniOf" else "organization", organization, _evidence(f"JSON-LD subject Person.{key}", organization), "json_ld")
        for field, key in (("bio", "description"), ("department", "department"), ("education", "hasCredential"), ("location", "homeLocation"), ("project", "owns"), ("publication", "subjectOf")):
            for value in _names(node.get(key)):
                add(field, value, _evidence(f"JSON-LD subject Person.{key}", value), "json_ld")
        for link in _names(node.get("sameAs")) + _names(node.get("url")):
            if link.startswith(("http://", "https://")):
                subject_links.add(link)
                add("profile_link", link, _evidence("JSON-LD subject Person link", link), "json_ld")

    subject_name = scope_id = observed_date = None
    temporal_context = "unknown"

    # Event/project entities describe candidate records but are not silently treated as person identity.
    for root in roots:
        for node in _walk_json(root):
            types = _types(node)
            if "event" in types:
                for name in _names(node.get("name")):
                    add("event", name, _evidence("JSON-LD Event.name", name), "json_ld")
                date = _clean(node.get("startDate"))
                if date:
                    add("date", date, _evidence("JSON-LD Event.startDate", date), "json_ld")
                for location in _names(node.get("location")):
                    add("location", location, _evidence("JSON-LD Event.location", location), "json_ld")
            if types & {"softwaresourcecode", "softwareapplication", "product"}:
                for name in _names(node.get("name")):
                    add("project", name, _evidence("JSON-LD project/product name", name), "json_ld")

    # Explicit profile metadata is subject-oriented; generic author/publisher metadata is kept separate.
    username = _clean(parser.meta.get("profile:username"))
    if username:
        add("username", username.lstrip("@"), _evidence("profile metadata username", username), "metadata")

    # Explicit labeled lines are deterministic source observations.
    label_map = {
        "name": "name", "username": "username", "handle": "username",
        "organization": "organization", "company": "organization", "college": "organization",
        "institution": "organization", "role": "role", "job title": "role", "department": "department",
        "event": "event", "hackathon": "event", "conference": "event",
        "project": "project", "product": "project", "publication": "publication",
        "date": "date", "year": "date", "location": "location",
    }
    label_pattern = re.compile(
        r"^(name|username|handle|organization|company|college|institution|role|job title|department|event|hackathon|conference|project|product|publication|date|year|location)\s*[:\-]\s*(.{1,500})$",
        re.IGNORECASE,
    )
    for line in lines:
        match = label_pattern.match(line)
        if not match:
            continue
        label = match.group(1).casefold()
        value = _clean(match.group(2), 500)
        if value:
            field = label_map[label]
            add(field, value.lstrip("@") if field == "username" else value, line, "text_pattern")

    # Confirm known seed clues only when the actual source text/title contains them.
    searchable_lines = [value for value in [h1, page_title, description, *lines] if value]
    normalized_lines = [(_normalize(line), line) for line in searchable_lines]
    for field, value in _seed_hints(context, clues):
        target = _normalize(value)
        if not target:
            continue
        for normalized_line, original_line in normalized_lines:
            if target and target in normalized_line:
                add(field, value.lstrip("@") if field == "username" else value,
                    f'Source text contains seed {field}: "{_clean(original_line, 650)}"', "text_match")
                break

    # Explicit candidate-led sentences only, not co-occurring page fragments.
    if context and context.name:
        name_pattern = re.escape(context.name)
        for index, line in enumerate(lines):
            if not re.match(name_pattern + r"\s+(?:(?:currently|formerly)\s+)?(?:is|was|works|worked|serves|served)\b", line, re.I):
                continue
            subject_name, scope_id = context.name, f"sentence-{index}"
            temporal_context = "current" if re.search(r"\bcurrently\b", line, re.I) else "historical" if re.search(r"\b(was|worked|formerly|served)\b", line, re.I) else "unknown"
            observed_date = next(iter(re.findall(r"\b(?:19|20)\d{2}\b", line)), None)
            if observed_date and int(observed_date) < calendar_date.today().year:
                temporal_context = "historical"
            add("name", context.name, line, "text_pattern")
            for field in ("role", "department", "organization"):
                value = getattr(context, field)
                if value and re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", line, re.I):
                    add(field, value, line, "text_pattern")
            affiliation = re.search(r"\b(?:works? for|worked for|employed by)\s+([^.;]{1,200})", line, re.I)
            if affiliation:
                add("organization", affiliation.group(1).strip(), line, "text_pattern")
            for role in re.findall(r"\b(?:Head of Department|HoD|Professor|Lecturer|Engineer|Director|Manager)\b", line, re.I):
                add("role", role, line, "text_pattern")
        subject_name = scope_id = observed_date = None
        temporal_context = "unknown"

    # Standards-based public profile markup only; no platform scraping or footer inference.
    from backend.services.profile_markup import read_profile_markup
    for index, record in enumerate(read_profile_markup(document.text)):
        subject_name = record.get("name")
        if not subject_name:
            continue
        scope_id = f"html-person-{index}"
        for field, value, evidence in record["observations"]:
            add(field, value, evidence, "metadata")
    subject_name = scope_id = observed_date = None
    temporal_context = "unknown"

    # The candidate URL itself can support a public profile handle; arbitrary footer social links cannot.
    for field, value, evidence in _candidate_profile_observations(candidate_url or document.final_url):
        add(field, value, evidence, "url")

    attribution = _extract_page_attribution(parser, roots, subject_links, document.final_url)
    return page_title, description, excerpt, observations, attribution


def extract_document(
    document: SourceDocument,
    context: SearchContext | None = None,
    clues: list[ReviewedClue] | None = None,
    candidate_url: str | None = None,
) -> tuple[str | None, str | None, str, list[SourceObservation]]:
    """Backward-compatible public extractor returning subject observations only."""
    page_title, description, excerpt, observations, _ = _extract_details(document, context, clues, candidate_url)
    return page_title, description, excerpt, observations


def analyze_candidates(request):
    """Read up to three selected candidates independently and extract subject observations."""
    from backend.models import AnalyzedSource, SourceAnalysisResponse, SourceReadIssue
    from backend.services.source_reader import SourceReadError, read_source

    results = []
    analyzed_count = 0
    for candidate in request.candidates:
        try:
            document = read_source(candidate.url)
            page_title, description, excerpt, observations, attribution = _extract_details(
                document,
                context=request.supplied_context,
                clues=request.clues,
                candidate_url=candidate.url,
            )
            results.append(AnalyzedSource(
                candidate=candidate,
                status="analyzed",
                final_url=document.final_url,
                page_title=page_title,
                meta_description=description,
                content_excerpt=excerpt or None,
                observations=observations,
                page_attribution=attribution,
            ))
            analyzed_count += 1
        except SourceReadError as error:
            if error.code in {"blocked_address", "invalid_url", "unsupported_scheme"}:
                status = "blocked"
            elif error.code == "restricted":
                status = "restricted"
            elif error.code in {"unsupported_content_type", "response_too_large", "empty_content"}:
                status = "unreadable"
            else:
                status = "failed"
            results.append(AnalyzedSource(
                candidate=candidate,
                status=status,
                observations=[],
                issue=SourceReadIssue(code=error.code, message=str(error), http_status=error.http_status),
            ))

    from backend.services.profiles import normalize_profile
    for source in results:
        source.profile = normalize_profile(source, request.supplied_context)

    overall = "completed" if analyzed_count == len(results) else "partial" if analyzed_count else "failed"
    return SourceAnalysisResponse(status=overall, sources=results)
