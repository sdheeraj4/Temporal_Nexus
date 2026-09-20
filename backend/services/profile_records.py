"""Classify identity URLs separately from their content; never infer post ownership from a mention."""
from urllib.parse import urlsplit, urlunsplit, parse_qs, urlencode
from backend.services.platforms import canonical_url, classify_url, profile_handle


def record_identity(url, personal_hosts=()):
    clean=canonical_url(url)
    if not clean: return 'page_reference',None,None
    p=urlsplit(clean); parts=[v for v in p.path.split('/') if v]; platform=classify_url(clean)
    root=urlunsplit((p.scheme,p.netloc,'/','',''))
    def account(path): return urlunsplit((p.scheme,p.netloc,path,'',''))
    if platform in {'github','huggingface'}:
        if profile_handle(clean): return 'profile',account('/'+parts[0]),parts[0]
        if len(parts)>1 and profile_handle(account('/'+parts[0])):
            return 'repository',account('/'+parts[0]),parts[0]
    if platform in {'x','twitter'} and parts:
        owner=parts[0]
        if profile_handle(account('/'+owner)):
            if len(parts)==1:return 'profile',account('/'+owner),owner
            if parts[1] in {'status','reposts','with_replies','media','likes','highlights'}:
                return 'post',account('/'+owner),owner
    if platform=='linkedin':
        if len(parts)==2 and parts[0]=='in':return 'profile',account('/in/'+parts[1]),parts[1]
        if parts and parts[0] in {'posts','feed','pulse'}:return 'post',None,parts[1].split('_',1)[0] if len(parts)>1 and parts[0]=='posts' and '_' in parts[1] else None
    if platform=='instagram' and profile_handle(clean):return 'profile',account('/'+parts[0]),parts[0]
    if platform=='instagram' and parts:return 'post',None,None
    if platform=='youtube':
        if parts and parts[0].startswith('@'):return ('profile' if len(parts)==1 else 'profile_evidence'),account('/'+parts[0]),parts[0][1:]
        if len(parts)>=2 and parts[0] in {'channel','c','user'}:return ('profile' if len(parts)==2 else 'profile_evidence'),account('/'+'/'.join(parts[:2])),None
        return 'post',None,None
    if platform=='google_scholar':
        query=parse_qs(p.query)
        if p.path=='/citations' and len(query.get('user',[]))==1:
            target=account('/citations')+'?'+urlencode({'user':query['user'][0]})
            return ('publication' if 'citation_for_view' in query else 'profile'),target,None
        return 'publication',None,None
    if platform=='substack':
        if parts and parts[0].startswith('@'):
            return ('profile' if len(parts)==1 else 'post'),account('/'+parts[0]),parts[0][1:]
        if p.hostname.endswith('.substack.com'):
            return ('personal_site' if not parts or parts==['about'] else 'article'),root,None
        return 'page_reference',None,None
    if p.hostname in personal_hosts:
        return ('personal_site' if not parts or parts in [['about'],['about.html']] else 'profile_evidence'),root,None
    if platform=='faculty' and len(parts)>=2:return 'institution_page',clean,None
    if platform in {'publication','conference','event'}:return ('publication' if platform=='publication' else 'event'),None,None
    return 'page_reference',None,None
