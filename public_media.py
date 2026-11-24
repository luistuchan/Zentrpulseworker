"""Free, bounded, license-screened public video discovery for Pulse.
Provider metadata is not proof of ownership; all output is review-only.
"""
import hashlib
import html
import re
import time
import urllib.parse as u


def permitted_license(value):
    value = str(value or '').strip().lower()
    return bool(re.fullmatch(r'https?://(?:www\.)?creativecommons\.org/(?:licenses/by/[1-4]\.0|publicdomain/(?:zero|mark)/1\.0)/?', value)
                or re.fullmatch(r'cc by [1-4]\.0|cc0(?: 1\.0)?|public domain', value))


def terms(query):
    return set(re.findall(r'[a-z0-9]{3,}', query.lower())) - {'the', 'and', 'for', 'with', 'video', 'footage'}


def relevant(query, description):
    wanted = terms(query)
    return bool(wanted) and len(wanted & terms(description)) >= min(2, len(wanted))


def commons(query, api):
    params = u.urlencode({'action':'query','generator':'search','gsrsearch':'filetype:video '+query,
        'gsrnamespace':6,'gsrlimit':12,'prop':'imageinfo','iilimit':1,
        'iiprop':'url|mime|size|extmetadata','format':'json','formatversion':2})
    data = api.json_http('https://commons.wikimedia.org/w/api.php?'+params, retries=0)
    for page in data.get('query',{}).get('pages',[]):
        info = next(iter(page.get('imageinfo',[])),{})
        meta = info.get('extmetadata',{})
        get = lambda k: html.unescape(re.sub(r'<[^>]*>','',meta.get(k,{}).get('value','')))
        title = page.get('title','')
        if not relevant(query, title+' '+get('ImageDescription')):
            continue
        license_url, license_name = get('LicenseUrl'), get('LicenseShortName')
        url = info.get('url','')
        if (u.urlsplit(url).hostname != 'upload.wikimedia.org' or not info.get('mime','').startswith('video/')
                or not 0 < info.get('size',0) <= 60_000_000
                or not (permitted_license(license_url) or permitted_license(license_name))):
            continue
        yield {'provider':'Wikimedia Commons','source_url':url,'title':title,
               'attribution_url':info.get('descriptionurl',''),'creator':get('Artist'),
               'license':license_name,'license_url':license_url}


def archive(query, api):
    safe = ' '.join(re.findall(r'[a-zA-Z0-9]+',query))[:100]
    if not safe:
        return
    params = u.urlencode([('q','mediatype:movies AND ('+' AND '.join('title:'+x+' OR description:'+x for x in safe.split()[:3])+')'),
        ('fl[]','identifier'),('fl[]','title'),('fl[]','description'),('rows','8'),('output','json')])
    data = api.json_http('https://archive.org/advancedsearch.php?'+params,retries=0)
    for doc in data.get('response',{}).get('docs',[])[:5]:
        ident = doc.get('identifier','')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',ident) or not relevant(query,str(doc.get('title',''))+' '+str(doc.get('description',''))):
            continue
        item = api.json_http('https://archive.org/metadata/'+u.quote(ident,safe=''),retries=0)
        meta = item.get('metadata',{})
        license_url = meta.get('licenseurl','')
        if not isinstance(license_url,str) or not permitted_license(license_url):
            continue
        for f in item.get('files',[]):
            name = f.get('name','')
            if not isinstance(name,str) or not name.lower().endswith(('.mp4','.webm')) or f.get('private') == 'true':
                continue
            try:
                size = int(f.get('size',0))
            except (TypeError,ValueError):
                continue
            if not 0 < size <= 60_000_000:
                continue
            yield {'provider':'Internet Archive','source_url':'https://archive.org/download/'+u.quote(ident,safe='')+'/'+u.quote(name,safe=''),
                   'title':str(meta.get('title',ident)),'attribution_url':'https://archive.org/details/'+u.quote(ident,safe=''),
                   'creator':str(meta.get('creator','')),'license':license_url,'license_url':license_url}
            break


def discover(query, api):
    # Cache successful searches and empty results to avoid repeated free requests.
    for provider in (commons, archive):
        cache = api.WORK / 'public-media-v1' / (hashlib.sha256((provider.__name__+'|'+query).encode()).hexdigest()+'.json')
        old = api.load(cache,{})
        if old and time.time()-old.get('time',0)<86400:
            yield from old.get('items',[])
            continue
        try:
            items = list(provider(query,api))[:8]
        except (api.Hold,api.Wait,ValueError,TypeError):
            continue
        api.atomic_json(cache,{'time':time.time(),'items':items})
        yield from items


def select(scene, entry, folder, budget, api):
    used = entry.setdefault('_used_stock',[])
    checked = set()
    for query in api.stock_queries(scene)[:2]:
        shortlist = []
        for media in discover(query,api):
            url = media['source_url']
            key = 'public:'+hashlib.sha256(url.encode()).hexdigest()[:20]
            if key in used or key in checked:
                continue
            checked.add(key)
            path = folder / (key.replace(':','-')+'.mp4')
            thumb = path.with_suffix('.jpg')
            try:
                if not path.is_file():
                    raw,_,kind = api.http(url,limit=60_000_000,retries=0)
                    if not (kind.startswith('video/') or 'octet-stream' in kind):
                        continue
                    path.write_bytes(raw)
                seconds = api.duration(path)
                if seconds+.05 < scene.get('_required_seconds',0):
                    continue
                api.run(['ffmpeg','-v','error','-y','-ss',str(min(1,seconds/2)),'-i',str(path),'-frames:v','1','-vf','scale=480:-2',str(thumb)])
            except (api.Hold,api.Wait):
                continue
            shortlist.append((path,thumb,key,media))
            if len(shortlist)>=4:
                break
        if shortlist:
            verdicts = api.review_stock_frames(scene,entry,folder,budget,[x[1] for x in shortlist])
            for (path,_,key,media),match in zip(shortlist,verdicts):
                if match.get('matches') is True:
                    used.append(key)
                    return path,{**media,'id':key,'illustrative':True,'match_check':match,'sha256':api.digest(path)}
    return None
