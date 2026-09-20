"""Local platform classification and conservative identity keys; never fetch URLs."""
import hashlib
import re
from urllib.parse import urlsplit, urlunsplit

HOSTS = {'linkedin.com':'linkedin','github.com':'github','instagram.com':'instagram',
         'youtube.com':'youtube','youtu.be':'youtube','x.com':'x','twitter.com':'twitter',
         'scholar.google.com':'google_scholar','huggingface.co':'huggingface','substack.com':'substack'}
ALIASES = {'www.'+h:h for h in HOSTS}
ALIASES.update({'m.youtube.com':'youtube.com','mobile.twitter.com':'twitter.com','mobile.x.com':'x.com','m.instagram.com':'instagram.com'})

def classify_url(url, title=''):
    try:
        p=urlsplit(url); host=(p.hostname or '').lower(); host=ALIASES.get(host,host)
        if p.scheme not in ('http','https') or p.username or p.password:
            return 'other'
        if host in HOSTS:
            return HOSTS[host]
        if host.endswith('.substack.com'): return 'substack'
        text=(p.path+' '+title).casefold()
        for category, pattern in [('publication',r'\b(publications?|journals?|papers?|researchgate|doi)\b'),('conference',r'\b(conferences?|symposium)\b'),('event',r'\b(events?|summit)\b'),('faculty',r'\b(faculty|staff|people|directory)\b')]:
            if re.search(pattern,text): return category
        if host.endswith(('.edu','.ac.in','.edu.in','.ac.uk')): return 'institution'
        if re.search(r'\b(personal website|personal homepage|about me|portfolio)\b',text): return 'personal_site'
    except ValueError:
        pass
    return 'other'

def profile_handle(url):
    try:
        p=urlsplit(url); platform=classify_url(url); path=[s for s in p.path.split('/') if s]
        if not path: return None
        if platform == 'linkedin': return None  # A public slug is not a verified username.
        if platform == 'substack': return path[0][1:] if len(path)==1 and path[0].startswith('@') else None
        if platform == 'youtube': return path[0][1:] if len(path)==1 and path[0].startswith('@') else None
        reserved={'search','explore','topics','orgs','organizations','login','signup','accounts','reel','reels','p','tv','home','i','intent','share','features','marketplace','settings'}
        if platform in {'github','instagram','x','twitter','huggingface'} and len(path)==1 and path[0].lower() not in reserved:
            return path[0]
    except ValueError: pass
    return None

def canonical_url(url):
    # Shared validator strips known tracking only. Unknown websites retain their
    # scheme, host and path: equivalence cannot safely be assumed globally.
    from backend.services.discovery import normalize_url
    normalized=normalize_url(url)
    if not normalized: return None
    clean, _=normalized; p=urlsplit(clean); host=ALIASES.get(p.hostname,p.hostname)
    if host in HOSTS and p.port is None:
        clean=urlunsplit(('https',host,p.path.rstrip('/') or '/',p.query,''))
    return clean

def candidate_id(url):
    return 'candidate-'+hashlib.sha256((canonical_url(url) or url).encode()).hexdigest()[:16]
