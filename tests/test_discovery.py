"""Provider-mocked discovery tests: never use real keys or API quota."""

from io import BytesIO
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pydantic import ValidationError
from backend.models import DiscoveryRequest
from backend.services import discovery as service
import test_input as inputs


def query(text='"Alex Demo"', field="name"):
    return {"query": text, "reason": "Prepared identity query", "references": [{"source": "supplied_context", "field": field}]}


def provider_result(url="https://example.org/profile", title="Demo"):
    return {"url": url, "title": title, "content": "A <b>public</b> snippet &amp; context."}


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"TAVILY_API_KEY": "synthetic-test-token"})
        self.env.start()
        self.addCleanup(self.env.stop)
        # Even an accidentally unmocked test must never reach the network.
        self.network = patch.object(service, "urlopen", side_effect=AssertionError("No network in tests"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_success_and_provider_contract(self):
        payload = BytesIO(json.dumps({"results": [dict(provider_result(), score=0.9, raw_content="Not returned")]}).encode())
        with patch.object(service, "urlopen", return_value=payload) as provider:
            result, status = service.discover(DiscoveryRequest(queries=[query()]))
        self.assertEqual(status, 200)
        self.assertEqual(result.status, "completed")
        candidate = result.candidates[0]
        self.assertEqual(candidate.domain, "example.org")
        self.assertEqual(candidate.provider, "tavily")
        self.assertEqual(candidate.rank, 1)
        self.assertNotIn("score", candidate.model_dump())
        self.assertNotIn("raw_content", candidate.model_dump())
        self.assertEqual(candidate.source_type, "web_page")
        self.assertEqual(candidate.snippet, "A public snippet & context.")
        self.assertEqual(candidate.discovered_by[0].query, '"Alex Demo"')
        request = provider.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.tavily.com/search")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-test-token")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(request.data), {
            "query": '"Alex Demo"', "topic": "general", "search_depth": "basic", "max_results": 5,
            "auto_parameters": False, "include_answer": False, "include_raw_content": False, "include_images": False,
        })
        self.assertEqual(provider.call_args.kwargs["timeout"], 8)
        provider.assert_called_once()

    def test_url_duplicates_merge_exact_query_provenance(self):
        with patch.object(service, "search_tavily", side_effect=[
            [provider_result("https://EXAMPLE.org:443/profile?b=2&a=1&utm_source=demo#top")],
            [provider_result("https://example.org/profile?a=1&b=2&fbclid=demo")]
        ]):
            result, _ = service.discover(DiscoveryRequest(queries=[query(), query('"Alex Demo" "Summit"')]))
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].url, "https://example.org/profile?a=1&b=2")
        self.assertEqual(len(result.candidates[0].discovered_by), 2)
        self.assertEqual(result.candidates[0].discovered_by[0].references[0].field, "name")

    def test_duplicate_queries_do_not_spend_extra_quota(self):
        with patch.object(service, "search_tavily", return_value=[provider_result()]) as provider:
            result, _ = service.discover(DiscoveryRequest(queries=[query(), query('"alex demo"', "username")]))
        provider.assert_called_once()
        self.assertEqual(len(result.candidates[0].discovered_by[0].references), 2)

    def test_no_results(self):
        with patch.object(service, "search_tavily", return_value=[]):
            result, status = service.discover(DiscoveryRequest(queries=[query()]))
        self.assertEqual((status, result.status, result.candidates), (200, "no_results", []))

    def test_missing_key_does_not_call_provider(self):
        with patch.dict(os.environ, {"TAVILY_API_KEY": ""}), patch.object(service, "search_tavily") as provider:
            with self.assertRaises(service.DiscoveryError) as error:
                service.discover(DiscoveryRequest(queries=[query()]))
        self.assertEqual(error.exception.code, "missing_key")
        self.assertEqual(error.exception.status_code, 503)
        provider.assert_not_called()

    def test_invalid_key_and_rate_limit_stop_further_requests(self):
        for status, body, expected, code in [
            (401, {}, 502, "invalid_key"),
            (403, {}, 502, "invalid_key"),
            (429, {}, 429, "rate_limited"),
            (432, {}, 429, "quota_limited"),
            (433, {}, 429, "quota_limited"),
        ]:
            with self.subTest(status=status), patch.object(service, "urlopen", side_effect=HTTPError(
                service.PROVIDER_URL, status, "private provider diagnostic", {}, BytesIO(json.dumps(body).encode())
            )) as provider:
                result, http_status = service.discover(DiscoveryRequest(queries=[query(), query('"Other"')]))
            self.assertEqual(http_status, expected)
            self.assertEqual(result.status, "failed")
            self.assertEqual([item.code for item in result.issues], [code, "not_attempted"])
            self.assertNotIn("private provider diagnostic", result.model_dump_json())
            provider.assert_called_once()

    def test_timeout(self):
        with patch.object(service, "urlopen", side_effect=TimeoutError()):
            result, status = service.discover(DiscoveryRequest(queries=[query()]))
        self.assertEqual((status, result.status, result.issues[0].code), (504, "failed", "timeout"))

    def test_partial_failure_preserves_successes(self):
        with patch.object(service, "search_tavily", side_effect=[
            [provider_result()], service.DiscoveryError("timeout", "Provider timeout", 504), []
        ]):
            result, status = service.discover(DiscoveryRequest(queries=[query(), query('"Other"'), query('"Third"')]))
        self.assertEqual((status, result.status), (200, "partial"))
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(len(result.queries_searched), 2)
        self.assertEqual(result.issues[0].query, '"Other"')

    def test_malformed_provider_response_is_not_no_results(self):
        for data in (b"not json", b'{"results":null}', b'{"error":{}}', b'{}',
                     b'{"results":[{}]}', b'{"results":[null]}'):
            with self.subTest(data=data), patch.object(service, "urlopen", return_value=BytesIO(data)):
                result, status = service.discover(DiscoveryRequest(queries=[query()]))
            self.assertEqual((status, result.status), (502, "failed"))

    def test_empty_tavily_results_are_no_results(self):
        with patch.object(service, "urlopen", return_value=BytesIO(b'{"results":[]}')):
            result, status = service.discover(DiscoveryRequest(queries=[query()]))
        self.assertEqual((status, result.status, result.candidates), (200, "no_results", []))

    def test_result_cap_and_response_size_bound(self):
        rows = [provider_result(f"https://example.org/{i}") for i in range(8)]
        with patch.object(service, "urlopen", return_value=BytesIO(json.dumps({"results": rows}).encode())):
            result, _ = service.discover(DiscoveryRequest(queries=[query()]))
        self.assertEqual(len(result.candidates), 5)
        with patch.object(service, "urlopen", return_value=BytesIO(b"x" * (service.MAX_RESPONSE_BYTES + 1))):
            result, status = service.discover(DiscoveryRequest(queries=[query()]))
        self.assertEqual((status, result.status), (502, "failed"))

    def test_deduplicated_candidate_uses_best_provider_rank(self):
        with patch.object(service, "search_tavily", side_effect=[
            [provider_result("https://example.org/first"), provider_result()], [provider_result()]
        ]):
            result, _ = service.discover(DiscoveryRequest(queries=[query(), query('"Other"')]))
        candidate = next(item for item in result.candidates if item.url.endswith("/profile"))
        self.assertEqual(candidate.rank, 1)
        self.assertEqual(len(candidate.discovered_by), 2)

    def test_partial_quota_failure_preserves_candidates_and_stops(self):
        with patch.object(service, "search_tavily", side_effect=[
            [provider_result()], service.DiscoveryError("quota_limited", "Credit limit", 429)
        ]) as provider:
            result, status = service.discover(DiscoveryRequest(queries=[query(), query('"Other"'), query('"Third"')]))
        self.assertEqual((status, result.status, len(result.candidates)), (200, "partial", 1))
        self.assertEqual(provider.call_count, 2)
        self.assertEqual([issue.code for issue in result.issues], ["quota_limited", "not_attempted"])

    def test_url_safety_and_distinct_resources(self):
        for url in ("javascript:alert(1)", "file:///private", "https://user:pass@example.org", "https://example.org:bad"):
            self.assertIsNone(service.normalize_url(url))
        self.assertNotEqual(service.normalize_url("https://example.org/?id=1"), service.normalize_url("https://example.org/?id=2"))

    def test_request_limits(self):
        for queries in ([], [query()] * 7, [query("x" * 501)]):
            with self.assertRaises(ValidationError):
                DiscoveryRequest(queries=queries)


class DiscoveryHTTPTests(unittest.TestCase):
    setUpClass = classmethod(inputs.InputEndpointTests.setUpClass.__func__)
    tearDownClass = classmethod(inputs.InputEndpointTests.tearDownClass.__func__)

    def test_endpoint_success_missing_key_and_rate_limit(self):
        for key, provider_effect, expected in [
            ("test-token", None, 200), ("", None, 503),
            ("test-token", service.DiscoveryError("rate_limited", "Rate limited", 429), 429)
        ]:
            with patch.dict(os.environ, {"TAVILY_API_KEY": key}), patch.object(service, "search_tavily", return_value=[provider_result()], side_effect=provider_effect):
                request = Request(self.base_url + "/api/discover", data=json.dumps({"queries": [query()]}).encode(), headers={"Content-Type": "application/json"})
                try:
                    response = urlopen(request, timeout=5)
                except HTTPError as error:
                    response = error
                with response:
                    self.assertEqual(response.status, expected)
                    payload = json.load(response)
                    if expected == 200:
                        self.assertEqual(payload["candidates"][0]["provider"], "tavily")
