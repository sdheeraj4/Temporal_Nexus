"""Temporal reasoning, relationship graph, and custom ML-assist regressions."""
import unittest

from backend.models import (
    AnalyzedSource,
    CandidateSource,
    CorrelationRequest,
    PreparedQuery,
    SearchContext,
    SourceObservation,
)
from backend.services.correlation import correlate
from backend.services.ml_correlation import _trained_model

QUERY = PreparedQuery(query='"Alex Demo"', reason='test', references=[])


def candidate(url, title='Alex Demo'):
    return CandidateSource(title=title, url=url, snippet='candidate', domain=url.split('/')[2], rank=1, discovered_by=[QUERY])


def subject(field, value, *, temporal='unknown', observed_date=None, url='https://example.org/alex', evidence=None):
    return SourceObservation(
        field=field,
        value=value,
        evidence=evidence or f'{value} {observed_date or ""}'.strip(),
        extraction_method='json_ld',
        subject_name='Alex Demo',
        scope_id='subject-alex',
        source_url=url,
        observed_date=observed_date,
        temporal_context=temporal,
    )


def source(url, observations, title='Alex Demo'):
    return AnalyzedSource(candidate=candidate(url, title), status='analyzed', final_url=url, page_title=title, observations=observations)


class AdvancedIntelligenceTests(unittest.TestCase):
    def test_temporal_history_is_retained_without_current_conflict(self):
        request = CorrelationRequest(
            supplied_context=SearchContext(name='Alex Demo', organization='New Labs'),
            sources=[source('https://example.org/alex', [
                subject('name', 'Alex Demo'),
                subject('organization', 'Old Labs', temporal='historical', observed_date='2021-01-01'),
            ])],
        )
        report = correlate(request).report
        self.assertEqual(report.temporal_assessment.status, 'mixed')
        self.assertEqual(report.temporal_assessment.issues[0].severity, 'review')
        self.assertNotEqual(report.status, 'review_required')
        self.assertTrue(any(item.year_label == '2021' for item in report.temporal_assessment.timeline))

    def test_explicit_current_temporal_difference_forces_review(self):
        request = CorrelationRequest(
            supplied_context=SearchContext(name='Alex Demo', organization='New Labs'),
            sources=[source('https://example.org/alex', [
                subject('name', 'Alex Demo'),
                subject('organization', 'Other Labs', temporal='current', evidence='Alex Demo currently works for Other Labs.'),
            ])],
        )
        report = correlate(request).report
        self.assertEqual(report.temporal_assessment.status, 'conflicting')
        self.assertEqual(report.status, 'review_required')
        self.assertGreaterEqual(report.conflict_count, 1)

    def test_relationship_graph_contains_identity_profile_and_supported_context(self):
        request = CorrelationRequest(
            supplied_context=SearchContext(name='Alex Demo', organization='Example Campus', role='Engineer'),
            sources=[source('https://github.com/alexdemo', [
                subject('name', 'Alex Demo', url='https://github.com/alexdemo'),
                subject('username', 'alexdemo', url='https://github.com/alexdemo'),
                subject('organization', 'Example Campus', url='https://github.com/alexdemo'),
                subject('role', 'Engineer', url='https://github.com/alexdemo'),
                subject('project', 'NexusShield', url='https://github.com/alexdemo'),
            ])],
        )
        report = correlate(request).report
        graph = report.relationship_graph
        self.assertTrue(any(node.type == 'identity' for node in graph.nodes))
        self.assertTrue(any(node.type == 'profile' for node in graph.nodes))
        self.assertTrue(any(node.type == 'organization' and node.label == 'Example Campus' for node in graph.nodes))
        self.assertTrue(any(edge.relation == 'public trace' for edge in graph.edges))
        self.assertTrue(all(edge.source_urls for edge in graph.edges if edge.relation == 'public trace'))

    def test_ml_baseline_is_trained_and_strong_for_multi_signal_case(self):
        _, accuracy = _trained_model()
        self.assertGreaterEqual(accuracy, 0.85)
        request = CorrelationRequest(
            supplied_context=SearchContext(name='Alex Demo', username='alexdemo', organization='Example Campus', role='Engineer'),
            sources=[source('https://github.com/alexdemo', [
                subject('name', 'Alex Demo', url='https://github.com/alexdemo'),
                subject('username', 'alexdemo', url='https://github.com/alexdemo'),
                subject('organization', 'Example Campus', url='https://github.com/alexdemo'),
                subject('role', 'Engineer', url='https://github.com/alexdemo'),
            ])],
        )
        report = correlate(request).report
        self.assertEqual(report.ml_assessment.band, 'strong')
        self.assertGreater(report.ml_assessment.score, 0.7)
        self.assertIn('synthetic', report.ml_assessment.training_basis.lower())
        self.assertIn('not identity proof', report.ml_assessment.disclaimer.lower())

    def test_ml_never_overrides_explicit_conflict(self):
        request = CorrelationRequest(
            supplied_context=SearchContext(name='Alex Demo', username='alexdemo', organization='Example Campus'),
            sources=[source('https://example.org/other', [
                subject('name', 'Alex Demo'),
                subject('username', 'someoneelse'),
                subject('organization', 'Other Campus', temporal='current'),
            ])],
        )
        report = correlate(request).report
        self.assertEqual(report.status, 'review_required')
        self.assertLessEqual(report.ml_assessment.score, 0.25)
        self.assertNotEqual(report.ml_assessment.band, 'strong')


if __name__ == '__main__':
    unittest.main()
