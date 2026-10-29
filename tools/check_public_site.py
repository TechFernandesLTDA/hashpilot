"""Check published-page assets, language routes and metadata without networking."""
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import urlsplit,unquote

ROOT=Path(__file__).resolve().parents[1]/'docs'

class Page(HTMLParser):
    def __init__(self):super().__init__();self.links=[];self.language=None;self.canonical=None;self.meta={};self.main_count=0
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='html':self.language=attrs.get('lang')
        if tag=='main':self.main_count+=1
        if tag=='link' and attrs.get('rel')=='canonical':self.canonical=attrs.get('href')
        if tag=='meta':self.meta[attrs.get('property',attrs.get('name'))]=attrs.get('content')
        for key in ('href','src'):
            if attrs.get(key):self.links.append(attrs[key])

reports=[]
for route,language in [('index.html','pt-BR'),('en/index.html','en')]:
    path=ROOT/route;page=Page();page.feed(path.read_text(encoding='utf-8'))
    assert page.language==language and page.main_count==1,(route,'language/main')
    canonical='https://techfernandesltda.github.io/hashpilot/'+('en/' if language=='en' else '')
    assert page.canonical==canonical and page.meta['og:url']==canonical
    assert page.meta.get('description') and page.meta.get('og:image') and page.meta.get('robots')=='index,follow'
    checked=0
    for link in page.links:
        parsed=urlsplit(link)
        if parsed.scheme or parsed.netloc or not parsed.path:continue
        target=(path.parent/unquote(parsed.path)).resolve()
        assert target==ROOT or ROOT in target.parents,(route,link,'outside docs')
        if target.is_dir():target=target/'index.html'
        assert target.is_file(),(route,link,'missing')
        checked+=1
    reports.append({'page':route,'language':language,'local_links_checked':checked,'errors':[]})
assert (ROOT/'.nojekyll').is_file()
print(json.dumps(reports))
