"""Normalize subject-scoped records and assess public-profile associations locally."""
import re
import unicodedata
from urllib.parse import urlsplit
from backend.models import SourceObservation, CandidateProfile, ProfileRelation, ConnectedProfile, CorrelationEvidence, AnalyzedSource
from backend.services.platforms import classify_url, canonical_url, candidate_id, profile_handle

FIELDS=('name','username','organization','role','department')

def normalized(value):
    return ' '.join(re.findall(r'\w+',unicodedata.normalize('NFKC',value or '').casefold()))

def comparable(field,value):
    value=normalized(value)
    if field=='role': value=re.sub(r'\bhead of (?:the )?department\b','hod',value)
    if field=='department': value=value.replace('computer science and engineering','cse').replace('artificial intelligence','ai').replace('machine learning','ml')
    return value

def matches(field,a,b):
    original=b
    a,b=comparable(field,a),comparable(field,b)
    return a==b or field=='role' and a in [comparable(field,v) for v in re.split(r'\s*(?:&|\band\b|;)\s*',original,flags=re.I)]

def subject_observations(source, context):
    observations=source.observations if source.status=='analyzed' else []
    names={normalized(o.value) for o in observations if o.field=='name' and o.extraction_method!='text_match'}
    scoped=[o for o in observations if o.subject_name and o.scope_id]
    if scoped:
        scopes={o.scope_id for o in scoped if not context.name or normalized(o.subject_name)==normalized(context.name)}
        if len(scopes)!=1: return []
        scope=next(iter(scopes))
        return [o for o in scoped if o.scope_id==scope]
    # Older records can be represented, but unscoped context is not promoted to
    # confirmed subject evidence by the connected-profile assessment.
    return [o for o in observations if o.field in {'name','username'} and o.extraction_method!='text_match'] if len(names)<=1 else []

def normalize_profile(source,context):
    candidate=source.candidate;url=canonical_url(source.final_url or candidate.url) or candidate.url
    sid=candidate_id(url);platform=classify_url(url,source.page_title or candidate.title)
    observations=subject_observations(source,context)
    from backend.services.indexed_clues import indexed_observations
    observations = list(observations) + indexed_observations(candidate, context)
    handle = profile_handle(url)
    if handle:
        observations.append(SourceObservation(field="username", value=handle, evidence="Public account handle in URL: " + url, extraction_method="url"))
    observations=[o.model_copy(update={"source_url": o.source_url or (candidate.url if o.extraction_method=="search_snippet" else url)}) for o in observations]
    def values(field): return list(dict.fromkeys(o.value for o in observations if o.field==field))
    names=values('name'); usernames=values('username')
    edges=[];domains=[]
    for item in observations:
        if item.field not in {'profile_link','project','event'}: continue
        target=canonical_url(item.value)
        if not target or target==url: continue
        kind=classify_url(target)
        relation='project_reference' if item.field=='project' else 'event_reference' if item.field=='event' else 'institutional_profile_link' if kind in {'institution','faculty'} else 'personal_domain_link' if kind in {'other','personal_site'} else 'explicit_profile_link'
        edge=ProfileRelation(source_candidate_id=sid,relation_type=relation,target_url=target,evidence=item.evidence,evidence_origin=url,evidence_type="search_snippet" if item.extraction_method=="search_snippet" else "direct_page")
        if edge not in edges: edges.append(edge)
        if relation=='personal_domain_link': domains.append(urlsplit(target).hostname)
    access='PUBLIC_PAGE_ANALYZED' if source.status=='analyzed' else 'SOURCE_INACCESSIBLE'
    handle=profile_handle(url)
    relationship='profile_candidate' if names or handle or (platform=='youtube' and '/channel/' in url) or (platform=='google_scholar' and 'user=' in url) else 'page_reference'
    parts=urlsplit(url)
    if platform=='youtube' and (parts.hostname=='youtu.be' or parts.path.startswith(('/watch','/shorts/','/embed/'))):
        relationship='page_reference'
    if platform=='github' and len([v for v in parts.path.split('/') if v])!=1:
        relationship='page_reference'
    slug=parts.path.strip('/').split('/')[-1] if platform=='linkedin' and parts.path.startswith('/in/') and len(parts.path.strip('/').split('/'))==2 else None
    return CandidateProfile(profile_slug=slug, candidate_id=sid,platform=platform,url=url,domain=urlsplit(url).hostname or candidate.domain,
        page_title=source.page_title or candidate.title,display_name=names[0] if len(names)==1 else None,
        username=usernames[0] if len(usernames)==1 else handle,organization=values('organization'),role=values('role'),department=values('department'),education=values('education'),location=values('location'),projects=values('project'),publications=values('publication'),events=values('event'),
        personal_domains=list(dict.fromkeys(domains)),external_profile_links=edges,bio_or_summary=next(iter(values("bio")), None),
        source_evidence=observations,discovery_queries=candidate.discovered_by,access_status=access,relationship=relationship,
        source_metadata={'indexed_title':candidate.title,'indexed_snippet':candidate.snippet,'description':source.meta_description,
                         'page_attribution':source.page_attribution.model_dump(),'read_status':source.status,
                         'discovery_provenance':[p.model_dump() for p in candidate.provenance]})

def connected_profiles(request):
    # Never trust a client-provided profile; recompute from observations.
    grouped={}
    for source in request.sources:
        key=canonical_url(source.final_url or source.candidate.url) or source.candidate.url
        if key not in grouped: grouped[key]=source.model_copy(deep=True)
        else:
            existing=grouped[key]
            if source.status=='analyzed':
                existing.status='analyzed'
                for item in source.observations:
                    if item not in existing.observations: existing.observations.append(item)
            for query in source.candidate.discovered_by:
                if query not in existing.candidate.discovered_by: existing.candidate.discovered_by.append(query)
    context=request.supplied_context
    profiles=[normalize_profile(s,context) for s in grouped.values()]
    aliases={canonical_url(s.candidate.url) for s in grouped.values()}
    for candidate in request.discovered_candidates:
        key=canonical_url(candidate.url)
        if key in grouped or key in aliases: continue
        p=normalize_profile(AnalyzedSource(candidate=candidate,status='unreadable'),context)
        p.access_status='SEARCH_SNIPPET_ONLY';profiles.append(p);aliases.add(key)
    results=[]
    for profile in profiles:
        support=[];missing=[];conflicts=[];evidence=[];partial_context=False
        for field in FIELDS:
            seed=getattr(context,field)
            if not seed: continue
            items=[o for o in profile.source_evidence if o.field==field]
            good=[o for o in items if matches(field,seed,o.value) and (field in {'name','username'} or o.subject_name) and o.temporal_context!='historical']
            bad=[o for o in items if not matches(field,seed,o.value) and o.subject_name and o.temporal_context=='current' and field == 'organization']
            if not good and field in {'department','organization'} and any(set(comparable(field,o.value).split()) < set(comparable(field,seed).split()) for o in items):
                partial_context=True
            if good: support.append(field)
            else: missing.append(field)
            if bad: conflicts.append(field+': explicitly current value differs from supplied context; review possible concurrent affiliations.')
            for item in items:
                evidence.append(CorrelationEvidence(value=item.value,evidence=item.evidence,extraction_method=item.extraction_method,source_url=profile.url,field=field))
        meaningful=bool(set(support)&{'organization','role','department'})
        direct_name=any(o.field=='name' and o.extraction_method!='search_snippet' for o in profile.source_evidence)
        direct_context=any(o.field in {'organization','role','department'} and o.field in support and o.extraction_method!='search_snippet' for o in profile.source_evidence)
        status='conflicting' if conflicts else 'supported' if 'name' in support and meaningful and direct_name and direct_context else 'partially_supported' if 'name' in support and (profile.external_profile_links or partial_context or meaningful or 'username' in support) else 'insufficient_evidence'
        if not profile.external_profile_links: missing.append('explicit_profile_link')
        results.append(ConnectedProfile(profile=profile,status=status,supporting=support,missing=missing,conflicts=conflicts,evidence=evidence,
            rationale='Candidate record has an explicit name and contextual support.' if status=='supported' else 'Explicit current differences require review; no automatic merge.' if conflicts else 'Matching signals need further corroboration.'))
    # One-hop links only from an already supported record. Never propagate through
    # a name-only chain; reference links (projects/events) are not ownership edges.
    trusted=[r for r in results if r.status=='supported']
    for result in results:
        for origin in trusted:
            if origin is result: continue
            edges=[e for e in origin.profile.external_profile_links if e.target_url==result.profile.url and e.relation_type in {'explicit_profile_link','personal_domain_link','institutional_profile_link'}]
            for edge in edges:
                label='explicit profile cross-link from '+origin.profile.url
                if label not in result.supporting: result.supporting.append(label)
                result.evidence.append(CorrelationEvidence(value=edge.target_url,evidence=edge.evidence,extraction_method='explicit_link',source_url=edge.evidence_origin))
                if 'explicit_profile_link' in result.missing: result.missing.remove('explicit_profile_link')
                if not result.conflicts:
                    result.status='supported' if 'name' in result.supporting and edge.evidence_type=='direct_page' else 'partially_supported'
                    result.rationale='An already supported subject record explicitly links this candidate. Missing target fields remain unresolved; ownership is not proven.'
        # Shared projects/publications/events/domains are context, never enough alone.
        for origin in trusted:
            if origin is result or result.profile.display_name!=origin.profile.display_name or not result.profile.display_name: continue
            for field in ('projects','publications','events','personal_domains','education','location'):
                shared={normalized(v) for v in getattr(result.profile,field)} & {normalized(v) for v in getattr(origin.profile,field)}
                if shared: result.supporting.append(field+' overlap with '+origin.profile.url+' (context only)')
    return results
