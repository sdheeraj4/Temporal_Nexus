"""Synthetic multi-platform regressions. Provider/network calls are always mocked."""
import json
import os
import unittest
from unittest.mock import patch
from pydantic import ValidationError
from backend.models import CorrelationRequest, SearchContext, SearchPlanRequest, DiscoveryRequest, SourceAnalysisRequest
from backend.services import discovery
from backend.services.platforms import classify_url, canonical_url, profile_handle
from backend.services.query_builder import build_search_plan
from backend.services.profiles import connected_profiles, normalize_profile
from backend.services.extraction import extract_document, analyze_candidates
from backend.services.source_reader import SourceDocument, SourceReadError
from test_correlation import source, candidate, observation


def subject(field,value,who='Alex Demo',scope='person',current='unknown'):
    return observation(field,value,method='json_ld').model_copy(update={'subject_name':who,'scope_id':scope,'temporal_context':current})

def assess(sources,context=None,discovered=None):
    return connected_profiles(CorrelationRequest(supplied_context=context or {'name':'Alex Demo','organization':'Example Campus'},sources=sources,discovered_candidates=discovered or []))

def doc(html,url='https://example.org/about'):
    return SourceDocument(url,url,200,'text/html',html)

class PlatformTests(unittest.TestCase):
    def test_platforms_and_content_routes(self):
        for url,expected in [('https://linkedin.com/in/alex','linkedin'),('https://github.com/alex','github'),('https://instagram.com/alex','instagram'),('https://youtube.com/@alex','youtube'),('https://x.com/alex','x'),('https://twitter.com/alex','twitter'),('https://scholar.google.com/citations?user=abc','google_scholar'),('https://example.edu/faculty/alex','faculty'),('https://example.edu/conference','conference'),('https://example.edu/','institution'),('https://example.org/event','event'),('https://example.org/publications','publication')]:
            with self.subTest(url=url): self.assertEqual(classify_url(url),expected)
        self.assertEqual(classify_url('https://github.com.evil.test/alex'),'other')
        self.assertEqual(classify_url('https://example.org','Personal homepage'),'personal_site')

    def test_known_aliases_and_tracking(self):
        self.assertEqual(canonical_url('http://www.linkedin.com/in/Alex/?utm_source=demo#about'),canonical_url('https://linkedin.com/in/Alex'))
        self.assertEqual(canonical_url('http://mobile.twitter.com/alex'),canonical_url('https://twitter.com/alex/'))
        self.assertNotEqual(canonical_url('https://github.com/alex'),canonical_url('https://github.com/morgan'))
        self.assertNotEqual(canonical_url('https://scholar.google.com/citations?user=a'),canonical_url('https://scholar.google.com/citations?user=b'))
        self.assertNotEqual(canonical_url('https://example.org/path'),canonical_url('http://www.example.org/path/'))

    def test_nonprofile_urls_do_not_supply_username(self):
        for url in ['https://github.com/alex/project','https://github.com/orgs/example','https://instagram.com/p/post','https://x.com/alex/status/1','https://youtube.com/watch?v=abc']:
            self.assertIsNone(profile_handle(url))
        self.assertEqual(profile_handle('https://youtube.com/@alex'),'alex')

    def test_targeted_queries_bounded_canonical_and_safe(self):
        req=SearchPlanRequest(supplied_context={'name':'Alex Demo','organization':'Example Technical Campus'},clues=[{'clue_id':'clue-0001','original_text':'ETC','corrected_text':'ETC','category':'organization','selected':True}])
        result=build_search_plan(req)
        self.assertEqual(len(result.platform_queries),3)
        self.assertLessEqual(len(result.queries),6)
        self.assertIn('"Example Technical Campus"',result.platform_queries[0].query)
        self.assertNotIn('"ETC"',result.platform_queries[0].query)
        self.assertEqual(len({q.query for q in result.platform_queries}),3)
        self.assertTrue(all(r.source=='supplied_context' for q in result.platform_queries for r in q.references))
        req.supplied_context.name='Alex" OR site:evil.test'
        self.assertNotIn('site:evil.test',build_search_plan(req).platform_queries[0].query)

    def test_username_targeted_queries_and_operator_budget(self):
        result=build_search_plan(SearchPlanRequest(supplied_context={'name':'Alex Demo','username':'alex_dev'}))
        self.assertIn('"alex_dev"',result.platform_queries[1].query)
        with self.assertRaises(ValidationError): DiscoveryRequest(queries=[result.queries[0].model_dump()],platform_queries=[q.model_dump() for q in result.platform_queries]*2)

    def test_provider_dedup_keeps_every_query_rank_and_snippet(self):
        queries=build_search_plan(SearchPlanRequest(supplied_context={'name':'Alex'})).platform_queries
        rows=[[{'title':'Alex','url':'http://www.linkedin.com/in/alex/?utm_source=a','content':'one'}],[{'title':'Alex','url':'https://linkedin.com/in/alex','content':'two'}]]
        with patch.dict(os.environ,{'TAVILY_API_KEY':'synthetic'}),patch.object(discovery,'search_tavily',side_effect=rows) as call:
            result,status=discovery.discover(DiscoveryRequest(queries=[queries[0].model_dump()],platform_queries=[queries[1].model_dump()]))
        self.assertEqual(call.call_count,2);self.assertEqual(status,200);self.assertEqual(len(result.candidates),1)
        c=result.candidates[0];self.assertEqual(c.platform,'linkedin');self.assertEqual(len(c.discovered_by),2)
        self.assertEqual({p.snippet for p in c.provenance},{'one','two'});self.assertTrue(c.candidate_id)

    def test_partial_platform_timeout_keeps_generic_results(self):
        queries=build_search_plan(SearchPlanRequest(supplied_context={'name':'Alex'}))
        with patch.dict(os.environ,{'TAVILY_API_KEY':'synthetic'}),patch.object(discovery,'search_tavily',side_effect=[[{'title':'A','url':'https://github.com/a','content':'a'}],discovery.DiscoveryError('timeout','Timed out')]):
            result,_=discovery.discover(DiscoveryRequest(queries=[q.model_dump() for q in queries.queries],platform_queries=[q.model_dump() for q in queries.platform_queries[:1]]))
        self.assertEqual(result.status,'partial');self.assertEqual(len(result.candidates),1)

    def test_profile_unknowns_and_snippet_only_are_not_identity(self):
        s=source('https://linkedin.com/in/alex',[]);s.status='restricted';s.candidate.snippet='Alex works at Example Campus'
        p=normalize_profile(s,SearchContext(name='Alex Demo'))
        self.assertIsNone(p.display_name);self.assertEqual(p.organization,[]);self.assertEqual(p.access_status,'SOURCE_INACCESSIBLE')
        result=assess([s])[0];self.assertFalse(result.conflicts);self.assertEqual(result.status,'insufficient_evidence')

    def test_same_name_different_org_not_supported(self):
        result=assess([source('https://github.com/alex',[subject('name','Alex Demo'),subject('organization','Other University'),subject('role','Painter')])])[0]
        self.assertEqual(result.status,'insufficient_evidence');self.assertFalse(result.conflicts)

    def test_explicit_current_difference_is_reviewable_conflict(self):
        result=assess([source('https://example.org/alex',[subject('name','Alex Demo'),subject('organization','Other University',current='current')])])[0]
        self.assertEqual(result.status,'conflicting');self.assertTrue(result.conflicts)

    def test_historical_difference_is_not_conflict(self):
        result=assess([source('https://example.org/alex',[subject('name','Alex Demo'),subject('organization','Old Campus',current='historical')])])[0]
        self.assertFalse(result.conflicts)

    def test_footer_site_accounts_not_subject_links(self):
        html='<div itemscope itemtype="https://schema.org/Person"><span itemprop="name">Alex Demo</span><a itemprop="sameAs" href="https://github.com/alex">GitHub</a><footer><a itemprop="sameAs" href="https://youtube.com/@university">Site channel</a></footer></div>'
        observations=extract_document(doc(html))[3]
        p=normalize_profile(source('https://example.org/about',observations),SearchContext(name='Alex Demo'))
        self.assertEqual([e.target_url for e in p.external_profile_links],['https://github.com/alex'])

    def test_explicit_crosslink_supports_but_does_not_verify_missing_target(self):
        a=source('https://example.org/about',[subject('name','Alex Demo'),subject('organization','Example Campus'),subject('profile_link','https://github.com/alex')])
        b=source('https://github.com/alex',[]);b.status='restricted'
        results=assess([a,b])
        self.assertEqual(results[0].status,'supported');self.assertEqual(results[1].status,'partially_supported')
        self.assertTrue(any('cross-link' in x for x in results[1].supporting));self.assertIn('name',results[1].missing)
        self.assertEqual(results[1].profile.access_status,'SOURCE_INACCESSIBLE')

    def test_no_crosslink_promotion_from_name_only_candidate(self):
        a=source('https://example.org/about',[subject('name','Alex Demo'),subject('profile_link','https://github.com/alex')]);b=source('https://github.com/alex',[])
        self.assertEqual(assess([a,b])[1].status,'insufficient_evidence')

    def test_multiple_people_cannot_supply_one_profile(self):
        a=source('https://example.org/faculty',[subject('name','Alex Demo'),subject('organization','Example Campus',who='Morgan',scope='other')])
        result=assess([a])[0];self.assertEqual(result.status,'insufficient_evidence');self.assertEqual(result.profile.organization,[])

    def test_json_subject_normalization_and_metadata_separation(self):
        html='<meta name="author" content="Site Editor"><script type="application/ld+json">'+json.dumps({'@type':'Person','name':'Alex Demo','jobTitle':'Engineer','worksFor':{'name':'Example Campus'},'department':'Robotics','homeLocation':{'name':'City'},'alumniOf':{'name':'College'},'sameAs':'https://github.com/alex'})+'</script>'
        p=normalize_profile(source('https://example.org/about',extract_document(doc(html))[3]),SearchContext(name='Alex Demo'))
        self.assertEqual(p.role,['Engineer']);self.assertEqual(p.department,['Robotics']);self.assertEqual(p.education,['College']);self.assertEqual(p.location,['City'])
        self.assertNotIn('Site Editor',p.model_dump_json());self.assertEqual(len(p.external_profile_links),1)

    def test_snippet_candidates_retained_in_report(self):
        s=source('https://example.org/about',[])
        results=assess([s],discovered=[candidate('https://instagram.com/alex')])
        self.assertEqual(len(results),2);self.assertEqual(results[1].profile.access_status,'SEARCH_SNIPPET_ONLY');self.assertFalse(results[1].conflicts)

    def test_analysis_populates_profile_without_extra_fetches(self):
        request=SourceAnalysisRequest(candidates=[candidate('https://github.com/alex')],supplied_context={'name':'Alex Demo'})
        with patch('backend.services.source_reader.read_source',return_value=doc('<div class="h-card"><span class="p-name">Alex Demo</span><span class="p-org">Example Campus</span></div>','https://github.com/alex')) as reader:
            result=analyze_candidates(request)
        self.assertEqual(reader.call_count,1);self.assertEqual(result.sources[0].profile.organization,['Example Campus'])

    def test_restricted_source_preserved_with_profile(self):
        with patch('backend.services.source_reader.read_source',side_effect=SourceReadError('restricted','Restricted',403)):
            result=analyze_candidates(SourceAnalysisRequest(candidates=[candidate('https://linkedin.com/in/alex')]))
        self.assertEqual(result.sources[0].profile.access_status,'SOURCE_INACCESSIBLE')

    def test_subject_sentence_current_org_conflict(self):
        context=SearchContext(name='Alex Demo',organization='Example Campus')
        observations=extract_document(doc('<p>Alex Demo currently works for Other University.</p>'),context)[3]
        self.assertEqual(assess([source('https://example.org/about',observations)])[0].status,'conflicting')
    def test_role_alias_and_specialized_department_are_conservative(self):
        context={'name':'Alex Demo','role':'HoD','department':'CSE (AI & ML)'}
        a=source('https://example.org/faculty',[subject('name','Alex Demo'),subject('role','Professor & Head of Department'),subject('department','CSE')])
        r=assess([a],context)[0]
        self.assertIn('role',r.supporting);self.assertIn('department',r.missing);self.assertFalse(r.conflicts)
        a.observations=[subject('name','Alex Demo'),subject('role','Professor')]
        self.assertNotIn('role',assess([a],context)[0].supporting)

    def test_link_does_not_hide_target_conflict(self):
        a=source('https://example.org/about',[subject('name','Alex Demo'),subject('organization','Example Campus'),subject('profile_link','https://github.com/alex')])
        b=source('https://github.com/alex',[subject('name','Alex Demo'),subject('organization','Other Campus',current='current')])
        r=assess([a,b])[1]
        self.assertEqual(r.status,'conflicting');self.assertTrue(any('cross-link' in s for s in r.supporting))

    def test_same_source_dedup_merges_evidence(self):
        a=source('http://www.linkedin.com/in/alex/',[subject('name','Alex Demo')])
        b=source('https://linkedin.com/in/alex',[subject('organization','Example Campus')])
        r=assess([a,b]);self.assertEqual(len(r),1);self.assertEqual(r[0].status,'supported')

    def test_page_and_platform_report_cannot_mix_people(self):
        from backend.services.correlation import correlate
        s=source('https://example.org/people',[subject('name','Alex Demo'),subject('organization','Example Campus',who='Morgan',scope='morgan')])
        report=correlate(CorrelationRequest(supplied_context={'name':'Alex Demo','organization':'Example Campus'},sources=[s])).report
        self.assertEqual(report.status,'insufficient');self.assertNotEqual(report.fields[1].status,'supported')
    def test_video_and_repository_stay_page_references(self):
        for url in ['https://youtube.com/watch?v=abc','https://github.com/alex/project']:
            p=normalize_profile(source(url,[subject('name','Alex Demo')]),SearchContext(name='Alex Demo'))
            self.assertEqual(p.relationship,'page_reference');self.assertIsNone(p.username)

    def test_dated_old_profile_role_does_not_claim_current_role(self):
        html='<script type="application/ld+json">'+json.dumps({'@type':'Person','name':'Alex Demo','jobTitle':'Engineer','dateModified':'2015-01-01'})+'</script>'
        observations=extract_document(doc(html))[3]
        r=assess([source('https://example.org/alex',observations)],{'name':'Alex Demo','role':'Engineer'})[0]
        self.assertNotIn('role',r.supporting);self.assertNotEqual(r.status,'supported')

    def test_sebastian_style_profiles_consolidate_and_indexed_role_supports_seed(self):
        from backend.services.correlation import correlate
        from backend.models import DiscoveryProvenance

        context=SearchContext(name='Sebastian Raschka',username='rasbt',role='LLM Research Engineer')
        personal=source('https://sebastianraschka.com/',[
            subject('name','Sebastian Raschka',who='Sebastian Raschka'),
            subject('role','LLM Research Engineer',who='Sebastian Raschka'),
            subject('profile_link','https://github.com/rasbt',who='Sebastian Raschka'),
            subject('profile_link','https://x.com/rasbt',who='Sebastian Raschka'),
            subject('profile_link','https://linkedin.com/in/sebastianraschka',who='Sebastian Raschka'),
        ],title='Sebastian Raschka, LLM Research Engineer')
        github=source('https://github.com/rasbt',[
            subject('name','Sebastian Raschka',who='Sebastian Raschka'),
            subject('username','rasbt',who='Sebastian Raschka'),
        ],title='rasbt (Sebastian Raschka) · GitHub')

        def discovered(url,title,snippet):
            c=candidate(url,title).model_copy(update={'snippet':snippet})
            return c.model_copy(update={'provenance':[DiscoveryProvenance(query='q',url=url,rank=1,snippet=snippet)]})

        discovered_rows=[
            discovered('https://linkedin.com/in/sebastianraschka','Sebastian Raschka, PhD - ML/AI research engineer','Sebastian Raschka, PhD, is an LLM Research Engineer with over a decade of experience in artificial intelligence.'),
            discovered('https://x.com/rasbt','Sebastian Raschka (@rasbt) / X','Sebastian Raschka @rasbt ML/AI research engineer. Website: sebastianraschka.com'),
            discovered('https://scholar.google.com/citations?user=X4RCC0IAAAAJ','Sebastian Raschka - Google Scholar','Sebastian Raschka. AI Research Engineer, Formerly Asst. Professor of Statistics, University of Wisconsin-Madison.'),
            discovered('https://github.com/rasbt/mini-coding-agent','rasbt/mini-coding-agent','rasbt / mini-coding-agent Public'),
            discovered('https://x.com/rasbt/status/123','Sebastian Raschka on X','Sebastian Raschka @rasbt Some post'),
            discovered('https://sebastianraschka.com/about','About Sebastian Raschka','Sebastian Raschka works as an LLM Research Engineer.'),
            discovered('https://linkedin.com/posts/sebastianraschka_foo','Sebastian Raschka post','Sebastian Raschka, PhD is an LLM Research Engineer.'),
        ]
        report=correlate(CorrelationRequest(supplied_context=context,sources=[personal,github],discovered_candidates=discovered_rows)).report
        self.assertEqual(report.status,'supported')
        self.assertEqual((report.supported_fields,report.supplied_fields),(3,3))
        urls={p.profile.url for p in report.connected_profiles}
        self.assertEqual(urls,{
            'https://github.com/rasbt',
            'https://linkedin.com/in/sebastianraschka',
            'https://x.com/rasbt',
            'https://sebastianraschka.com/',
            'https://scholar.google.com/citations?user=X4RCC0IAAAAJ',
        })
        linkedin=next(p for p in report.connected_profiles if p.profile.platform=='linkedin')
        self.assertIn('role',linkedin.supporting)
        self.assertEqual(linkedin.status,'supported')
        github_profile=next(p for p in report.connected_profiles if p.profile.platform=='github')
        self.assertTrue(any(r.record_type=='repository' and 'mini-coding-agent' in r.url for r in github_profile.profile.related_records))
        x_profile=next(p for p in report.connected_profiles if p.profile.platform=='x')
        self.assertTrue(any(r.record_type=='post' for r in x_profile.profile.related_records))
        personal_profile=next(p for p in report.connected_profiles if p.profile.platform=='personal_site')
        self.assertTrue(any('about' in r.url for r in personal_profile.profile.related_records))
        self.assertFalse(any('/posts/' in p.profile.url or '/status/' in p.profile.url or 'mini-coding-agent' in p.profile.url for p in report.connected_profiles))

    def test_tavily_domain_filter_enforces_generated_platform_group(self):
        from io import BytesIO
        query=build_search_plan(SearchPlanRequest(supplied_context={'name':'Alex'})).platform_queries[0]
        with patch.object(discovery,'urlopen',return_value=BytesIO(b'{"results":[]}')) as transport:
            discovery.search_tavily(query.query,'synthetic-test-key')
        body=json.loads(transport.call_args.args[0].data)
        self.assertEqual(body['include_domains'],['linkedin.com','scholar.google.com'])
        self.assertEqual(body['search_depth'],'basic');self.assertFalse(body['include_raw_content'])
