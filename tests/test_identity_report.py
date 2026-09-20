"""Report regressions: canonical context, provenance, coverage and ambiguous records."""
import unittest
from backend.models import CorrelationRequest, SearchContext, ReviewedClue
from backend.services.correlation import correlate
from test_correlation import source, observation


def explicit(field, value):
    return observation(field, value, method="json_ld")


class IdentityReportTests(unittest.TestCase):
    def report(self, observations, **context):
        return correlate(CorrelationRequest(supplied_context=SearchContext(**context),
            sources=[source("https://example.org/person", observations)])).report

    def test_role_department_supported_with_subject_anchor(self):
        report = self.report([explicit("name", "Alex Demo"), explicit("role", "Head of Department"),
            explicit("department", "Computer Science")], name="Alex Demo", role="Head of Department", department="Computer Science")
        self.assertEqual((report.supported_fields, report.supplied_fields), (3, 3))
        self.assertEqual(report.status, "supported")

    def test_page_mentions_are_partial_not_identity_support(self):
        report = self.report([observation("name", "Alex Demo"), observation("organization", "ABC College")],
            name="Alex Demo", organization="ABC College")
        self.assertEqual(report.supported_fields, 0)
        self.assertEqual(report.status, "insufficient")
        self.assertTrue(all(f.status == "partial" for f in report.fields))

    def test_canonical_organization_preserved_with_ocr_provenance(self):
        request = CorrelationRequest(supplied_context=SearchContext(name="Alex", organization="ABC College"),
            clues=[ReviewedClue(clue_id="clue-0001", original_text="A8C", corrected_text="ABC", category="organization", selected=True)],
            sources=[source("https://example.org/a", [])])
        response = correlate(request)
        self.assertEqual([s.value for s in response.seed_profile if s.field == "organization"], ["ABC College"])
        self.assertEqual(response.report.reviewed_image_clues[0].original_text, "A8C")
        self.assertEqual(response.report.supplied_context.organization, "ABC College")

    def test_missing_role_is_not_conflict(self):
        report = self.report([explicit("name", "Alex")], name="Alex", role="Engineer")
        self.assertEqual(report.fields[1].status, "missing")

    def test_changed_affiliation_requires_review_not_conflict(self):
        report = self.report([explicit("name", "Alex"), explicit("organization", "Old Company")], name="Alex", organization="New Company")
        self.assertEqual(report.fields[1].status, "partial")

    def test_other_person_on_page_cannot_support_organization(self):
        report = self.report([explicit("name", "Alex"), explicit("name", "Morgan"), explicit("organization", "ABC")], name="Alex", organization="ABC")
        self.assertEqual(report.fields[0].status, "conflicting")
        self.assertEqual(report.fields[1].status, "partial")
        self.assertEqual(report.status, "review_required")

    def test_duplicate_url_does_not_inflate_counts(self):
        a=source("https://example.org/alex", [explicit("name", "Alex")])
        b=source("https://example.org/alex#bio", [explicit("name", "Alex")])
        report=correlate(CorrelationRequest(supplied_context=SearchContext(name="Alex"), sources=[a,b])).report
        self.assertEqual(report.analyzed_sources, 1)
        self.assertEqual(report.source_domains, 1)


    def test_report_synthesizes_most_supported_identity(self):
        report = self.report([explicit("name", "Alex Demo"), explicit("role", "Engineer")], name="Alex Demo", role="Engineer")
        self.assertEqual(report.most_supported_identity.label, "Alex Demo")
        self.assertEqual(report.most_supported_identity.role, "Engineer")
        self.assertEqual(set(report.most_supported_identity.corroborated_fields), {"name", "role"})
        self.assertEqual(report.most_supported_identity.status, "supported")
        self.assertIn("corroborated", report.most_supported_identity.rationale.lower())

    def test_organization_without_explicit_identity_is_not_supported(self):
        report=self.report([explicit("organization", "ABC")], organization="ABC")
        self.assertEqual(report.supported_fields, 0)
