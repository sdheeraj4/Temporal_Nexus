"""Local query preparation contracts using synthetic context and OCR observations."""

import json
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pydantic import ValidationError
from backend.models import SearchPlanRequest
from backend.services.query_builder import build_search_plan
import test_input as inputs


def clue(text, category="event", selected=True, number=1, original=None):
    return {"clue_id": f"clue-{number:04}", "original_text": text if original is None else original,
            "corrected_text": text, "category": category, "selected": selected}


def plan(context=None, clues=None):
    return build_search_plan(SearchPlanRequest(supplied_context=context or {}, clues=clues or []))


class QueryBuilderTests(unittest.TestCase):
    def test_name_and_event_with_exact_references(self):
        result = plan({"name": "Alex Demo"}, [clue("Nexus Summit 2026")])
        self.assertEqual([q.query for q in result.queries], ['"Alex Demo"', '"Alex Demo" "Nexus Summit 2026"'])
        combined = result.queries[1]
        self.assertEqual(combined.references[0].field, "name")
        self.assertEqual(combined.references[1].clue_id, "clue-0001")
        self.assertEqual(combined.references[1].field, "corrected_text")
        self.assertIn("event", combined.reason)

    def test_username_only(self):
        result = plan({"username": "@demo_user"})
        self.assertEqual(result.status, "prepared")
        self.assertEqual(result.queries[0].query, '"demo_user"')

    def test_selected_name_supplies_anchor(self):
        result = plan(clues=[clue("Alex Demo", "name"), clue("Summit", number=2)])
        self.assertEqual(result.queries[1].query, '"Alex Demo" "Summit"')
        self.assertTrue(all(ref.source == "clue" for q in result.queries for ref in q.references))

    def test_deselected_and_other_clues_do_not_contribute(self):
        result = plan({"name": "Alex"}, [clue("Secret Event", selected=False), clue("Random", "other", number=2)])
        self.assertEqual(len(result.queries), 1)
        self.assertEqual(len(result.queries[0].references), 1)

    def test_correction_used_original_preserved(self):
        request = SearchPlanRequest(supplied_context={"name": "Alex"}, clues=[clue("Nexus Summit", original="Nexvs Surnmit")])
        result = build_search_plan(request)
        self.assertIn('"Nexus Summit"', result.queries[1].query)
        self.assertEqual(request.clues[0].original_text, "Nexvs Surnmit")
        self.assertNotIn("Nexvs", result.model_dump_json())

    def test_duplicate_and_blank_clues(self):
        result = plan({"name": "Alex"}, [clue("Summit"), clue(" summit ", number=2), clue("   ", number=3)])
        self.assertEqual(len(result.queries), 2)
        self.assertEqual([ref.clue_id for ref in result.queries[1].references[1:]], ["clue-0001", "clue-0002"])

    def test_operators_are_literalized(self):
        result = plan({"name": 'Alex" OR site:evil.test -filetype:pdf'}, [clue('Meetup" | (2026) *')])
        for query in result.queries:
            self.assertNotRegex(query.query, r"[:|()*\-]")
            self.assertTrue(query.query.startswith('"Alex OR site evil test filetype pdf"'))

    def test_missing_anchor_and_punctuation_only_anchor(self):
        for context, clues in [({}, []), ({"organization": "Nexus"}, [clue("Summit")]),
                               ({}, [clue("Alex", "name", selected=False)]), ({"name": '\"-:*'}, [])]:
            with self.subTest(context=context):
                result = plan(context, clues)
                self.assertEqual(result.status, "needs_context")
                self.assertEqual(result.queries, [])

    def test_conflicting_names_are_never_combined(self):
        result = plan({"name": "Alex"}, [clue("Blair", "name"), clue("Summit", number=2)])
        self.assertTrue(any('"Blair"' == q.query for q in result.queries))
        self.assertFalse(any("Alex" in q.query and "Blair" in q.query for q in result.queries))

    def test_deterministic_six_query_cap_and_organization(self):
        request = SearchPlanRequest(supplied_context={"name": "Alex", "organization": "Nexus"},
                                    clues=[clue(f"Event {i}", number=i) for i in range(1, 20)])
        first = build_search_plan(request)
        self.assertEqual(first, build_search_plan(request))
        self.assertEqual(len(first.queries), 6)
        self.assertEqual(first.queries[1].query, '"Alex" "Nexus"')
        self.assertEqual(len({q.query.casefold() for q in first.queries}), 6)

    def test_limits_and_unique_ids(self):
        invalid = [
            {"supplied_context": {field: "x" * 201}} for field in ("name", "username", "organization")
        ] + [
            {"clues": [clue("x" * 201)]}, {"clues": [clue("x", original="x" * 2001)]},
            {"clues": [clue("Event", number=i) for i in range(1, 52)]},
            {"clues": [clue("A"), clue("B")]}, {"clues": [clue("A", selected="true")]},
            {"clues": [clue("A", category="verified_identity")]},
        ]
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(ValidationError):
                SearchPlanRequest(**body)
        self.assertEqual(len(SearchPlanRequest(clues=[clue("x" * 200, number=i) for i in range(1, 51)]).clues), 50)

    def test_current_categories_values_and_duplicate_deselection(self):
        request = SearchPlanRequest(clues=[
            clue("Alex Demo", "name", original="Al3x Demo"),
            clue("Nexus Summit", "organization", number=2, original="Nexvs Summit"),
            clue("  ＮＥＸＵＳ\u00a0 SUMMIT  ", number=3),
        ])
        request.clues[1].category = "event"
        result = build_search_plan(request)
        self.assertEqual([q.query for q in result.queries], ['"Alex Demo"', '"Alex Demo" "Nexus Summit"'])
        combined = result.queries[1]
        self.assertIn('event "Nexus Summit"', combined.reason)
        self.assertNotIn("organization", combined.reason)
        self.assertEqual([r.clue_id for r in combined.references], ["clue-0001", "clue-0002", "clue-0003"])
        request.clues[2].selected = False
        remaining = build_search_plan(request).queries[1]
        self.assertEqual(remaining.query, combined.query)
        self.assertEqual([r.clue_id for r in remaining.references], ["clue-0001", "clue-0002"])
        self.assertEqual(request.clues[0].original_text, "Al3x Demo")
        self.assertEqual(request.clues[1].original_text, "Nexvs Summit")

    def test_reasons_include_all_current_categories_not_first_category_only(self):
        result = plan({"name": "Alex", "organization": "Nexus"}, [clue("Nexus", "event")])
        self.assertIn('organization "Nexus"', result.queries[1].reason)
        self.assertIn('event "Nexus"', result.queries[1].reason)

    def test_final_equivalent_query_merges_references_before_limit(self):
        # Reverse phrase order produces an equivalent final query via another anchor.
        result = plan(clues=[
            clue("Alex", "name"), clue("Blair", "name", number=2),
            clue("Blair", "event", number=3),
            *[clue(f"Event {i}", number=i + 10) for i in range(8)],
            clue("Alex", "event", number=4),
        ])
        self.assertEqual(len(result.queries), 6)
        combined = next(q for q in result.queries if q.query == '"Alex" "Blair"')
        self.assertEqual({r.clue_id for r in combined.references}, {"clue-0001", "clue-0002", "clue-0003", "clue-0004"})


class SearchPlanHTTPTests(unittest.TestCase):
    setUpClass = classmethod(inputs.InputEndpointTests.setUpClass.__func__)
    tearDownClass = classmethod(inputs.InputEndpointTests.tearDownClass.__func__)

    def test_json_endpoint(self):
        for body, expected in [
            ({"supplied_context": {"username": "demo"}}, 200),
            ({}, 200), ({"supplied_context": {"name": "x" * 201}}, 422)
        ]:
            request = Request(self.base_url + "/api/search-plan", data=json.dumps(body).encode(),
                              headers={"Content-Type": "application/json"})
            try:
                response = urlopen(request, timeout=5)
            except HTTPError as error:
                response = error
            with response:
                self.assertEqual(response.status, expected)
                result = json.load(response)
                if not body:
                    self.assertEqual(result["status"], "needs_context")


if __name__ == "__main__":
    unittest.main()
