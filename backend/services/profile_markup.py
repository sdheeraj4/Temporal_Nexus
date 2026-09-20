"""Read explicit Person microdata/h-card fields; ignore navigation/footer links."""
from html.parser import HTMLParser
from urllib.parse import urlsplit

MAP={'description':'bio','p-note':'bio','name':'name','p-name':'name','alternateName':'username','p-nickname':'username','jobTitle':'role','p-job-title':'role',
     'affiliation':'organization','worksFor':'organization','p-org':'organization','department':'department',
     'alumniOf':'education','homeLocation':'location','p-locality':'location','sameAs':'profile_link','url':'profile_link','u-url':'profile_link'}
VOID={'meta','link','img','br','hr','input','source','area','base','embed','param','wbr'}
class ProfileMarkup(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.stack=[];self.records=[];self.ignored=0
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs);classes=attrs.get('class','').split();parent=self.stack[-1] if self.stack else {}
        ignored=parent.get('ignored',False) or tag in {'footer','nav','aside','script','style'}
        root=not ignored and ('schema.org/Person' in attrs.get('itemtype','') or 'h-card' in classes)
        record={'observations':[]} if root else parent.get('record')
        if root: self.records.append(record)
        fields=[MAP[t] for t in attrs.get('itemprop','').split()+classes if t in MAP]
        frame={'tag':tag,'ignored':ignored,'record':record,'field':fields[0] if fields else None,'text':[],'href':attrs.get('href'),'content':attrs.get('content')}
        self.stack.append(frame)
        if tag in VOID: self.handle_endtag(tag)
    def handle_data(self,data):
        if self.stack and not self.stack[-1]['ignored']:
            for frame in self.stack:
                if frame.get('field'): frame['text'].append(data)
    def handle_endtag(self,tag):
        index=next((i for i in range(len(self.stack)-1,-1,-1) if self.stack[i]['tag']==tag),None)
        if index is None:return
        frames=self.stack[index:];del self.stack[index:]
        for frame in frames:
            record=frame['record'];field=frame['field']
            if record is None or not field or frame['ignored']:continue
            text=' '.join(''.join(frame['text']).split())[:500]
            value=frame['href'] if field=='profile_link' else frame['content'] or text
            if not value:continue
            if field=='profile_link':
                try:
                    p=urlsplit(value)
                    if p.scheme not in {'https','http'} or p.username or p.password:continue
                except ValueError:continue
            if field=='name':record['name']=value
            record['observations'].append((field,value,(text+' '+value if field=='profile_link' else value)[:800]))

def read_profile_markup(html):
    parser=ProfileMarkup()
    try:parser.feed(html);parser.close()
    except (ValueError,RecursionError):return []
    return parser.records[:20]
