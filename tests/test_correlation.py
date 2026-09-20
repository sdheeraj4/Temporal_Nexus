"""Evidence-backed identity correlation baseline tests."""

import json
import unittest
from urllib.request import Request, urlopen

import test_input as inputs
from backend.models import (
    AnalyzedSource,
    CandidateSource,
    CorrelationRequest,
    PreparedQuery,
    ReviewedClue,
    SearchContext,
    SourceObservation,
)
from backend.services.correlation import correlate


QUERY = PreparedQuery(query='"Alex Demo"', reason="Identity query", references=[])


def candidate(url: str, title="Alex Demo") -> CandidateSource:
    return CandidateSource(
        title=title,
        url=url,
        snippet="candidate",
        domain=url.split("/")[2],
        rank=1,
        discovered_by=[QUERY],
    )


def observation(field, value, evidence=None, method="text_match") -> SourceObservation:
    return SourceObservation(
        field=field,
        value=value,
        evidence=evidence or f"Evidence for {value}",
        extraction_method=method,
    )


def source(url: str, observations, title="Candidate") -> AnalyzedSource:
    return AnalyzedSource(
        candidate=candidate(url, title),
        status="analyzed",
        final_url=url,
        page_title=title,
        observations=observations,
    )


class CorrelationTests(unittest.TestCase):
    def test_supported_candidate_requires_multiple_independent_seed_signals(self):
        request = CorrelationRequest(
            supplied_context=SearchContext(name="Alex Demo", username="alexdemo", organization="ABC College"),
            clues=[ReviewedClue(
                clue_id="clue-0001", original_text="Nexus Summit 2026", corrected_text="Nexus Summit 2026",
                selected=True, category="event"
            )],
            sources=[source("https://example.org/alex", [
                observation("name", "Alex Demo", "Profile heading: Alex Demo"),
                observation("username", "alexdemo", "Profile handle: alexdemo"),
                observation("organization", "ABC College", "Bio: ABC College"),
                observation("event", "Nexus Summit 2026", "Participant: Nexus Summit 2026"),
            ])],
        )
        response = correlate(request)
        self.assertEqual(response.status, "completed")
        assessment = response.assessments[0]
        self.assertEqual(assessment.status, "supported")
        self.assertEqual(set(assessment.matched_fields), {"name", "username", "organization", "event"})
        self.assertFalse(assessment.conflicting_fields)
        self.assertTrue(all(item.evidence for item in assessment.comparisons if item.status == "match"))

    def test_same_name_only_is_uncertain_not_verified(self):
        request = CorrelationRequest(
            supplied_context=SearchContext(name="Alex Demo", username="alexdemo", organization="ABC College"),
            sources=[source("https://example.org/name-only", [observation("name", "Alex Demo")])],
        )
        response = correlate(request)
        self.assertEqual(response.assessments[0].status, "uncertain")
        self.assertEqual(response.assessments[0].matched_fields, ["name"])

    def test_explicit_username_conflict_marks_candidate_conflicting(self):
        request = CorrelationRequest(
            supplied_context=SearchContext(name="Alex Demo", username="alexdemo", organization="ABC College"),
            sources=[source("https://example.org/other", [
                observation("name", "Alex Demo"),
                observation("username", "alex1999"),
                observation("organization", "XYZ University"),
            ])],
        )
        response = correlate(request)
        assessment = response.assessments[0]
        self.assertEqual(assessment.status, "conflicting")
        self.assertIn("username", assessment.conflicting_fields)
        username = next(item for item in assessment.comparisons if item.field == "username")
        self.assertEqual(username.status, "conflict")
        self.assertEqual(username.evidence[0].value, "alex1999")

    def test_different_organization_is_no_support_not_absolute_identity_conflict(self):
        request = CorrelationRequest(
            supplied_context=SearchContext(name="Alex Demo", organization="ABC College"),
            sources=[source("https://example.org/alex", [
                observation("name", "Alex Demo"), observation("organization", "XYZ University")
            ])],
        )
        response = correlate(request)
        org = next(item for item in response.assessments[0].comparisons if item.field == "organization")
        self.assertEqual(org.status, "no_support")
        self.assertEqual(response.assessments[0].status, "uncertain")

    def test_cross_source_corroboration_reports_repeated_subject_values(self):
        request = CorrelationRequest(
            supplied_context=SearchContext(name="Alex Demo"),
            sources=[
                source("https://example.org/a", [observation("name", "Alex Demo"), observation("project", "SecureTrack")]),
                source("https://example.org/b", [observation("name", "Alex Demo"), observation("project", "SecureTrack")]),
            ],
        )
        response = correlate(request)
        repeated = {(item.field, item.value, len(item.source_urls)) for item in response.cross_source_support}
        self.assertIn(("name", "Alex Demo", 2), repeated)
        self.assertIn(("project", "SecureTrack", 2), repeated)

    def test_seed_deduplicates_same_value_from_supplied_context_and_image_clue(self):
        request = CorrelationRequest(
            supplied_context=SearchContext(organization="ABC College"),
            clues=[ReviewedClue(
                clue_id="clue-0001", original_text="ABC COLLEGE", corrected_text="ABC College",
                selected=True, category="organization"
            )],
            sources=[source("https://example.org/a", [observation("organization", "ABC College")])],
        )
        response = correlate(request)
        self.assertEqual(len(response.seed_profile), 1)
        self.assertEqual(len(response.seed_profile[0].references), 2)

    def test_no_seed_returns_needs_seed(self):
        response = correlate(CorrelationRequest(sources=[source("https://example.org/a", [])]))
        self.assertEqual(response.status, "needs_seed")
        self.assertFalse(response.assessments)


class CorrelationHTTPTests(unittest.TestCase):
    setUpClass = classmethod(inputs.InputEndpointTests.setUpClass.__func__)
    tearDownClass = classmethod(inputs.InputEndpointTests.tearDownClass.__func__)

    def test_endpoint_returns_supported_assessment_with_evidence(self):
        body = CorrelationRequest(
            supplied_context=SearchContext(name="Alex Demo", username="alexdemo"),
            sources=[source("https://example.org/a", [
                observation("name", "Alex Demo"), observation("username", "alexdemo")
            ])],
        ).model_dump(mode="json")
        request = Request(
            self.base_url + "/api/correlate",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            payload = json.load(response)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["assessments"][0]["status"], "supported")
        self.assertEqual(payload["assessments"][0]["comparisons"][0]["status"], "match")
        self.assertTrue(payload["assessments"][0]["comparisons"][0]["evidence"])


if __name__ == "__main__":
    unittest.main()
