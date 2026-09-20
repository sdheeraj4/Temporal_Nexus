"""Safe source reading and conservative information-extraction tests."""

from email.message import Message
import json
import socket
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import test_input as inputs
from backend.models import CandidateSource, PreparedQuery, ReviewedClue, SearchContext, SourceAnalysisRequest
from backend.services.extraction import analyze_candidates, extract_document
from backend.services.source_reader import (
    MAX_RESPONSE_BYTES,
    SourceDocument,
    SourceReadError,
    _ensure_public_destination,
    read_source,
)


QUERY = PreparedQuery(query='"Alex Demo"', reason="Identity query", references=[])


def candidate(url="https://example.org/profile"):
    return CandidateSource(
        title="Alex Demo",
        url=url,
        snippet="Candidate snippet",
        domain="example.org",
        rank=1,
        discovered_by=[QUERY],
    )


class FakeResponse:
    def __init__(self, body, status=200, content_type="text/html; charset=utf-8", extra_headers=None):
        self.body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        for key, value in (extra_headers or {}).items():
            self.headers[key] = value

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class SourceReaderTests(unittest.TestCase):
    def test_public_html_read_is_bounded_and_decoded(self):
        response = FakeResponse("<html><title>Demo</title><p>Hello</p></html>")
        with patch("backend.services.source_reader._ensure_public_destination"), \
             patch("backend.services.source_reader.urlopen", return_value=response) as opened:
            document = read_source("https://Example.org/profile#fragment")
        self.assertEqual(document.final_url, "https://example.org/profile")
        self.assertIn("Hello", document.text)
        self.assertEqual(opened.call_count, 1)
        self.assertLessEqual(opened.call_args.kwargs["timeout"], 8)

    def test_local_private_link_local_and_reserved_addresses_blocked(self):
        for address in ("127.0.0.1", "10.0.0.2", "169.254.1.2", "192.0.2.5", "::1"):
            with self.subTest(address=address), patch("socket.getaddrinfo", return_value=[
                (socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443, 0, 0) if ":" in address else (address, 443))
            ]):
                with self.assertRaises(SourceReadError) as error:
                    _ensure_public_destination("example.test", 443)
                self.assertEqual(error.exception.code, "blocked_address")

    def test_only_http_https_and_no_credentials(self):
        for url in ("file:///etc/passwd", "ftp://example.org/a", "https://user:pass@example.org/"):
            with self.subTest(url=url), self.assertRaises(SourceReadError) as error:
                read_source(url)
            self.assertIn(error.exception.code, {"unsupported_scheme", "invalid_url"})

    def test_redirect_destination_is_revalidated_and_private_redirect_blocked(self):
        headers = Message()
        headers["Location"] = "http://127.0.0.1/private"
        redirect = HTTPError("https://example.org/start", 302, "Found", headers, None)
        with patch("backend.services.source_reader._ensure_public_destination") as validate, \
             patch("backend.services.source_reader.urlopen", side_effect=redirect):
            # First validation is mocked; restore real validation for the redirect by side effect.
            validate.side_effect = [None, SourceReadError("blocked_address", "blocked")]
            with self.assertRaises(SourceReadError) as error:
                read_source("https://example.org/start")
        self.assertEqual(error.exception.code, "blocked_address")
        self.assertEqual(validate.call_count, 2)

    def test_restricted_not_found_timeout_content_type_and_size(self):
        cases = []
        for status, code in ((403, "restricted"), (404, "not_found")):
            cases.append((HTTPError("https://example.org/", status, "err", Message(), None), code))
        cases.extend([
            (TimeoutError(), "timeout"),
            (FakeResponse("{}", content_type="application/json"), "unsupported_content_type"),
            (FakeResponse(b"x" * (MAX_RESPONSE_BYTES + 1)), "response_too_large"),
        ])
        for effect, expected in cases:
            with self.subTest(expected=expected), patch("backend.services.source_reader._ensure_public_destination"), \
                 patch("backend.services.source_reader.urlopen", side_effect=effect if isinstance(effect, BaseException) else None,
                       return_value=None if isinstance(effect, BaseException) else effect):
                with self.assertRaises(SourceReadError) as error:
                    read_source("https://example.org/")
                self.assertEqual(error.exception.code, expected)


class ExtractionTests(unittest.TestCase):
    def test_jsonld_metadata_text_patterns_links_and_missing_values(self):
        html = '''<!doctype html><html><head>
        <title>Alex Demo profile</title>
        <meta name="description" content="Controlled public demo profile">
        <meta property="profile:username" content="@alexdemo">
        <script type="application/ld+json">{
          "@context":"https://schema.org","@type":"Person","name":"Alex Demo",
          "jobTitle":"Student Developer","worksFor":{"@type":"Organization","name":"ABC College"},
          "sameAs":["https://github.com/alexdemo"]
        }</script></head><body>
        <h1>Alex Demo</h1>
        <p>Event: Nexus Summit 2026</p>
        <p>Project: SecureTrack</p>
        <p>Year: 2026</p>
        <a href="https://github.com/alexdemo">GitHub</a>
        <p>This page does not contain an invented publication.</p>
        </body></html>'''
        document = SourceDocument("https://example.org", "https://example.org/", 200, "text/html", html)
        title, description, excerpt, observations = extract_document(document)
        self.assertEqual(title, "Alex Demo profile")
        self.assertEqual(description, "Controlled public demo profile")
        self.assertIn("Nexus Summit", excerpt)
        values = {(item.field, item.value) for item in observations}
        for expected in (
            ("name", "Alex Demo"), ("username", "alexdemo"), ("organization", "ABC College"),
            ("role", "Student Developer"), ("event", "Nexus Summit 2026"),
            ("project", "SecureTrack"), ("date", "2026"),
            ("profile_link", "https://github.com/alexdemo"),
        ):
            self.assertIn(expected, values)
        self.assertFalse(any(item.field == "publication" for item in observations))
        self.assertTrue(all(item.evidence for item in observations))

    def test_event_and_product_jsonld_are_subject_observations_but_generic_article_is_not(self):
        html = '''<script type="application/ld+json">[
          {"@type":"Event","name":"Nexus Summit","startDate":"2026-09-19","location":{"name":"Hyderabad"}},
          {"@type":"SoftwareSourceCode","name":"SecureTrack"},
          {"@type":"ScholarlyArticle","headline":"Identity Correlation","datePublished":"2026-01-02"}
        ]</script>'''
        document = SourceDocument("https://example.org", "https://example.org/", 200, "text/html", html)
        *_, observations = extract_document(document)
        values = {(item.field, item.value) for item in observations}
        self.assertIn(("event", "Nexus Summit"), values)
        self.assertIn(("location", "Hyderabad"), values)
        self.assertIn(("project", "SecureTrack"), values)
        self.assertNotIn(("publication", "Identity Correlation"), values)


    def test_seed_hints_are_confirmed_from_source_text_without_inventing_fields(self):
        html = """<html><head><title>Alex Demo at Nexus Summit</title></head><body>
        <h1>Alex Demo</h1><p>Alex Demo represented ABC College at Nexus Summit 2026.</p>
        </body></html>"""
        document = SourceDocument("https://example.org", "https://example.org/", 200, "text/html", html)
        context = SearchContext(name="Alex Demo", organization="ABC College")
        clues = [ReviewedClue(clue_id="clue-0001", original_text="Nexus Summit 2026", corrected_text="Nexus Summit 2026", selected=True, category="event")]
        *_, observations = extract_document(document, context=context, clues=clues)
        values = {(item.field, item.value, item.extraction_method) for item in observations}
        self.assertIn(("name", "Alex Demo", "text_match"), values)
        self.assertIn(("organization", "ABC College", "text_match"), values)
        self.assertIn(("event", "Nexus Summit 2026", "text_match"), values)

    def test_plain_text_label_extraction(self):
        document = SourceDocument(
            "https://example.org", "https://example.org/", 200, "text/plain",
            "Name: Alex Demo\nOrganization: ABC College\nEvent: Nexus Summit\nUnstructured words here",
        )
        *_, observations = extract_document(document)
        self.assertEqual({(o.field, o.value) for o in observations}, {
            ("name", "Alex Demo"), ("organization", "ABC College"), ("event", "Nexus Summit")
        })


class AnalysisServiceTests(unittest.TestCase):
    def test_multiple_sources_partial_failure_and_provenance(self):
        request = SourceAnalysisRequest(candidates=[candidate("https://example.org/a"), candidate("https://example.org/b")])
        document = SourceDocument("https://example.org/a", "https://example.org/a", 200, "text/plain", "Name: Alex Demo")
        with patch("backend.services.source_reader.read_source", side_effect=[document, SourceReadError("restricted", "blocked", 403)]):
            response = analyze_candidates(request)
        self.assertEqual(response.status, "partial")
        self.assertEqual(response.sources[0].status, "analyzed")
        self.assertEqual(response.sources[0].candidate.discovered_by[0].query, '"Alex Demo"')
        self.assertEqual(response.sources[1].status, "restricted")
        self.assertEqual(response.sources[1].issue.http_status, 403)

    def test_article_author_publisher_and_footer_social_links_are_separate_from_subject(self):
        html = '''<html><head>
        <meta name="author" content="Britannica Editors">
        <script type="application/ld+json">{
          "@type":"Article","headline":"Alex Demo biography",
          "author":{"@type":"Person","name":"Britannica Editors"},
          "publisher":{"@type":"Organization","name":"Encyclopedia Demo"}
        }</script></head><body><h1>Alex Demo</h1>
        <p>Alex Demo is a student developer at ABC College.</p>
        <footer><a href="https://x.com/publisher">X</a></footer></body></html>'''
        request = SourceAnalysisRequest(
            candidates=[candidate("https://example.org/alex")],
            supplied_context=SearchContext(name="Alex Demo", organization="ABC College"),
        )
        document = SourceDocument("https://example.org/alex", "https://example.org/alex", 200, "text/html", html)
        with patch("backend.services.source_reader.read_source", return_value=document):
            response = analyze_candidates(request)
        source = response.sources[0]
        values = {(item.field, item.value) for item in source.observations}
        self.assertIn(("name", "Alex Demo"), values)
        self.assertIn(("organization", "ABC College"), values)
        self.assertNotIn(("name", "Britannica Editors"), values)
        self.assertNotIn(("organization", "Encyclopedia Demo"), values)
        self.assertIn("Britannica Editors", source.page_attribution.authors)
        self.assertIn("Encyclopedia Demo", source.page_attribution.publishers)
        self.assertFalse(any(item.field == "profile_link" and "publisher" in item.value for item in source.observations))



class SourceAnalysisHTTPTests(unittest.TestCase):
    setUpClass = classmethod(inputs.InputEndpointTests.setUpClass.__func__)
    tearDownClass = classmethod(inputs.InputEndpointTests.tearDownClass.__func__)

    def test_endpoint_returns_independent_source_results(self):
        body = {"candidates": [candidate().model_dump(mode="json")]}
        from backend.models import AnalyzedSource, SourceAnalysisResponse, SourceObservation
        mocked = SourceAnalysisResponse(status="completed", sources=[AnalyzedSource(
            candidate=candidate(), status="analyzed", final_url="https://example.org/profile",
            page_title="Alex Demo", content_excerpt="Name: Alex Demo",
            observations=[SourceObservation(field="name", value="Alex Demo", evidence="Name: Alex Demo", extraction_method="text_pattern")]
        )])
        with patch("backend.main.analyze_candidates", return_value=mocked):
            request = Request(self.base_url + "/api/analyze-sources", data=json.dumps(body).encode(),
                              headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(request, timeout=5) as response:
                payload = json.load(response)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["sources"][0]["observations"][0]["value"], "Alex Demo")

    def test_endpoint_rejects_more_than_three_sources(self):
        body = {"candidates": [candidate(f"https://example.org/{i}").model_dump(mode="json") for i in range(4)]}
        request = Request(self.base_url + "/api/analyze-sources", data=json.dumps(body).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=5)
        self.assertEqual(error.exception.code, 422)


if __name__ == "__main__":
    unittest.main()
