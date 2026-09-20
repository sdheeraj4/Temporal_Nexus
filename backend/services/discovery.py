"""Search Tavily's general web index; normalize candidates without reading result pages."""

import json
import os
import re
import unicodedata
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from backend.services.platforms import canonical_url, candidate_id, classify_url
from backend.models import DiscoveryProvenance, CandidateSource, DiscoveryIssue, DiscoveryRequest, DiscoveryResponse

PROVIDER_URL = "https://api.tavily.com/search"
TIMEOUT_SECONDS = 8
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Do not forward the API token to a redirected destination.
urlopen = build_opener(_NoRedirect()).open


class DiscoveryError(RuntimeError):
    """Public error without credentials or raw provider diagnostics."""

    def __init__(self, code, message, status_code=502):
        super().__init__(message)
        self.code, self.status_code = code, status_code


def search_tavily(query: str, key: str) -> list[dict]:
    """One bounded provider request, no retries and no result-page requests."""
    body = {"query": query, "topic": "general", "search_depth": "basic", "max_results": 5,
            "auto_parameters": False, "include_answer": False,
            "include_raw_content": False, "include_images": False}
    # Enforce the generated platform groups with Tavily's domain filter too.
    allowed = {"linkedin.com", "github.com", "instagram.com", "youtube.com", "x.com", "twitter.com", "scholar.google.com"}
    domains = list(dict.fromkeys(re.findall(r"(?<![\w:])site:([a-z.]+)(?=[/\s)]|$)", query)))
    if domains and all(domain in allowed for domain in domains):
        body["include_domains"] = domains
    request = Request(PROVIDER_URL, data=json.dumps(body).encode("utf-8"), method="POST",
                      headers={"Accept": "application/json", "Content-Type": "application/json",
                               "Authorization": f"Bearer {key}"})
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise DiscoveryError("provider_failure", "The search provider response exceeded the size limit.")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or "error" in payload or "results" not in payload:
            raise ValueError("Invalid provider payload")
        results = payload["results"]
        if not isinstance(results, list) or any(
            not isinstance(item, dict) or any(not isinstance(item.get(field), str) for field in ("title", "url", "content"))
            for item in results
        ):
            raise ValueError("Invalid result list")
        return results[:5]
    except HTTPError as error:
        code = error.code
        error.close()
        if code in (401, 403):
            raise DiscoveryError("invalid_key", "Tavily rejected the API key or its search permissions.") from None
        if code == 429:
            raise DiscoveryError("rate_limited", "Tavily's rate limit was reached. Wait before retrying.", 429) from None
        if code in (432, 433):
            raise DiscoveryError("quota_limited", "Tavily's usage or credit limit was reached. Check your Tavily plan before retrying.", 429) from None
        raise DiscoveryError("provider_failure", "The search provider returned an error.") from None
    except TimeoutError:
        raise DiscoveryError("timeout", "The search provider timed out.", 504) from None
    except URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise DiscoveryError("timeout", "The search provider timed out.", 504) from None
        raise DiscoveryError("provider_unavailable", "Could not connect to the search provider.", 503) from None
    except (ValueError, UnicodeError):
        raise DiscoveryError("provider_failure", "The search provider returned an unreadable response.") from None
    except OSError:
        raise DiscoveryError("provider_unavailable", "The search provider connection failed.", 503) from None


def normalize_url(value: str) -> tuple[str, str] | None:
    """Remove fragments/tracking and normalize host, default ports and parameter order.

    Preserve scheme, path case, meaningful query parameters and non-root trailing
    slashes: these may identify different resources without a page fetch.
    """
    try:
        if not isinstance(value, str) or len(value) > 8000 or any(ord(char) < 32 for char in value):
            return None
        parts = urlsplit(value.strip())
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            return None
        host = parts.hostname.lower().encode("idna").decode("ascii")
        if any(char.isspace() for char in host) or "\\" in host:
            return None
        port = parts.port
        authority = f"[{host}]" if ":" in host else host
        if port and (parts.scheme.lower(), port) not in (("http", 80), ("https", 443)):
            authority += f":{port}"
        params = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
                  if not key.lower().startswith("utm_") and key.lower() not in ("gclid", "fbclid", "msclkid")]
        url = urlunsplit((parts.scheme.lower(), authority, parts.path or "/", urlencode(sorted(params)), ""))
        return url, host
    except (ValueError, UnicodeError):
        return None


def _plain(value, limit):
    return unescape(re.sub(r"<[^>]*>", "", value))[:limit] if isinstance(value, str) else ""


def discover(request: DiscoveryRequest) -> tuple[DiscoveryResponse, int]:
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        raise DiscoveryError("missing_key", "Set TAVILY_API_KEY in the server environment.", 503)
    # Avoid spending quota twice on equivalent prepared query strings.
    queries = {}
    for query in [*request.queries, *request.platform_queries]:
        normalized = " ".join(unicodedata.normalize("NFKC", query.query).split()).casefold()
        if not normalized:
            raise DiscoveryError("invalid_query", "Prepared queries must not be blank.", 422)
        if normalized not in queries:
            queries[normalized] = query.model_copy(deep=True)
        else:
            existing = queries[normalized]
            existing.references.extend(ref for ref in query.references if ref not in existing.references)
    candidates, issues, searched = {}, [], []
    stop_error = None
    failure_status = 502
    for query in queries.values():
        if stop_error:
            issues.append(DiscoveryIssue(query=query.query, code="not_attempted", message="Not attempted after an authentication, quota, or rate-limit failure."))
            continue
        try:
            rows = search_tavily(query.query, key)
            searched.append(query.query)
            for rank, row in enumerate(rows, start=1):
                normalized = normalize_url(row.get("url", ""))
                if not normalized:
                    continue
                original_url, domain = normalized
                url = canonical_url(original_url)
                if url not in candidates:
                    candidates[url] = CandidateSource(
                        candidate_id=candidate_id(url), platform=classify_url(url, row.get("title", "")),
                        title=_plain(row.get("title"), 1000) or domain, url=url,
                        snippet=_plain(row.get("content"), 4000), domain=domain, rank=rank, discovered_by=[],
                    )
                provenance = DiscoveryProvenance(query=query.query, url=row["url"], rank=rank, snippet=_plain(row.get("content"), 4000))
                if provenance not in candidates[url].provenance:
                    candidates[url].provenance.append(provenance)
                candidates[url].rank = min(candidates[url].rank, rank)
                if query not in candidates[url].discovered_by:
                    candidates[url].discovered_by.append(query)
        except DiscoveryError as error:
            failure_status = error.status_code
            issues.append(DiscoveryIssue(query=query.query, code=error.code, message=str(error)))
            if error.code in ("invalid_key", "rate_limited", "quota_limited"):
                stop_error = error
    status = "partial" if issues and searched else "failed" if issues else "completed" if candidates else "no_results"
    return DiscoveryResponse(status=status, candidates=list(candidates.values()), queries_searched=searched, issues=issues), (failure_status if status == "failed" else 200)
