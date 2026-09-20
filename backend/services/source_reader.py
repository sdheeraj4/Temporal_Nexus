"""Safely retrieve a small number of explicitly selected public web pages."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

TIMEOUT_SECONDS = 8
MAX_RESPONSE_BYTES = 1_500_000
MAX_REDIRECTS = 3
ALLOWED_CONTENT_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}
USER_AGENT = "TemporalNexus/0.1 public-source-reader"


@dataclass(frozen=True)
class SourceDocument:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    text: str


class SourceReadError(RuntimeError):
    """A bounded, user-displayable source-reading failure."""

    def __init__(self, code: str, message: str, http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Disable ambient proxies so URL validation applies to the destination we intend to contact.
urlopen = build_opener(ProxyHandler({}), _NoRedirect()).open


def _normalized_http_url(value: str) -> tuple[str, str, int]:
    """Validate URL syntax and return normalized URL, hostname and effective port."""
    try:
        if not isinstance(value, str) or not value.strip() or len(value) > 8000:
            raise SourceReadError("invalid_url", "The selected source URL is invalid.")
        parts = urlsplit(value.strip())
        scheme = parts.scheme.lower()
        if scheme not in ("http", "https"):
            raise SourceReadError("unsupported_scheme", "Only public HTTP and HTTPS sources can be analyzed.")
        if not parts.hostname or parts.username or parts.password:
            raise SourceReadError("invalid_url", "The selected source URL is invalid.")
        host = parts.hostname.rstrip(".").lower().encode("idna").decode("ascii")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise SourceReadError("blocked_address", "Local or internal network destinations are not allowed.")
        port = parts.port or (443 if scheme == "https" else 80)
        authority = f"[{host}]" if ":" in host else host
        if parts.port and parts.port != (443 if scheme == "https" else 80):
            authority += f":{parts.port}"
        normalized = urlunsplit((scheme, authority, parts.path or "/", parts.query, ""))
        return normalized, host, port
    except SourceReadError:
        raise
    except (ValueError, UnicodeError):
        raise SourceReadError("invalid_url", "The selected source URL is invalid.") from None


def _ensure_public_destination(host: str, port: int) -> None:
    """Resolve the target and reject any non-public address before each request."""
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise SourceReadError("dns_failure", "The selected source hostname could not be resolved.") from None
    addresses = {item[4][0] for item in results if item and item[4]}
    if not addresses:
        raise SourceReadError("dns_failure", "The selected source hostname could not be resolved.")
    try:
        parsed = [ipaddress.ip_address(address) for address in addresses]
    except ValueError:
        raise SourceReadError("dns_failure", "The selected source resolved to an invalid address.") from None
    if any(not address.is_global for address in parsed):
        raise SourceReadError("blocked_address", "Local, private, link-local, or reserved destinations are not allowed.")


def _content_type(headers) -> str:
    try:
        return headers.get_content_type().lower()
    except AttributeError:
        raw = str(headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
        return raw


def _charset(headers) -> str:
    try:
        return headers.get_content_charset() or "utf-8"
    except AttributeError:
        raw = str(headers.get("Content-Type", ""))
        for part in raw.split(";")[1:]:
            key, _, value = part.partition("=")
            if key.strip().lower() == "charset" and value.strip():
                return value.strip().strip('"')
        return "utf-8"


def _read_bounded(response) -> bytes:
    length = response.headers.get("Content-Length")
    if length:
        try:
            if int(length) > MAX_RESPONSE_BYTES:
                raise SourceReadError("response_too_large", "The source page exceeds the analysis size limit.")
        except ValueError:
            pass
    data = response.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise SourceReadError("response_too_large", "The source page exceeds the analysis size limit.")
    return data


def read_source(url: str) -> SourceDocument:
    """Fetch one public text/HTML page with redirect and SSRF protections."""
    requested_url = url
    current_url = url
    visited: set[str] = set()

    for redirect_count in range(MAX_REDIRECTS + 1):
        normalized, host, port = _normalized_http_url(current_url)
        if normalized in visited:
            raise SourceReadError("redirect_loop", "The selected source entered a redirect loop.")
        visited.add(normalized)
        _ensure_public_destination(host, port)
        request = Request(
            normalized,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                status = getattr(response, "status", response.getcode())
                content_type = _content_type(response.headers)
                if content_type not in ALLOWED_CONTENT_TYPES:
                    raise SourceReadError(
                        "unsupported_content_type",
                        f"The source returned unsupported content type {content_type or 'unknown'}.",
                        status,
                    )
                raw = _read_bounded(response)
                try:
                    text = raw.decode(_charset(response.headers), errors="replace")
                except LookupError:
                    text = raw.decode("utf-8", errors="replace")
                if not text.strip():
                    raise SourceReadError("empty_content", "The source returned no readable content.", status)
                return SourceDocument(
                    requested_url=requested_url,
                    final_url=normalized,
                    status_code=status,
                    content_type=content_type,
                    text=text,
                )
        except HTTPError as error:
            status = error.code
            location = error.headers.get("Location") if error.headers else None
            error.close()
            if status in (301, 302, 303, 307, 308) and location:
                if redirect_count >= MAX_REDIRECTS:
                    raise SourceReadError("too_many_redirects", "The selected source redirected too many times.", status) from None
                current_url = urljoin(normalized, location)
                continue
            if status in (401, 403):
                raise SourceReadError("restricted", "The source is public-searchable but does not permit direct reading.", status) from None
            if status == 404:
                raise SourceReadError("not_found", "The selected source page was not found.", status) from None
            raise SourceReadError("http_error", f"The source returned HTTP {status}.", status) from None
        except TimeoutError:
            raise SourceReadError("timeout", "The selected source timed out.") from None
        except URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise SourceReadError("timeout", "The selected source timed out.") from None
            raise SourceReadError("network_error", "The selected source could not be reached.") from None
        except SourceReadError:
            raise
        except OSError:
            raise SourceReadError("network_error", "The selected source could not be reached.") from None

    raise SourceReadError("too_many_redirects", "The selected source redirected too many times.")
