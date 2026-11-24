"""Local, draft-only highlight editing. No generative narration or paid service.

Visual mouth-motion tracking is a heuristic, not speaker diarization. Ambiguous
shots retain the full scene and are explicitly flagged for human review.
"""
from __future__ import annotations
import html
import json
import math
import os
from pathlib import Path
import re
import sys
import urllib.parse


def discover(budget, *, api, topics=(), warnings=None):
    """Find footage for today's leads, with a separate reuse-filtered YouTube search."""
    warnings = [] if warnings is None else warnings
    custom = os.getenv('CLIPPER_SEARCH_QUERY', '').strip()
    queries = [custom] if custom else []
    categories = set()
    for topic in topics:
        category = topic['category']
        if category in categories:
            continue
        query = ' '.join(topic['title'].rsplit(' - ', 1)[0].split()[:10])[:120]
        if query and query not in queries:
            queries.append(query)
            categories.add(category)
        if len(queries) >= 6:
            break
    if not queries:
        queries = ['video game tips gameplay', 'science demonstrations', 'creator highlights']

    leads = []
    candidates = {}
    key = os.getenv('YOUTUBE_API_KEY', '').strip()
    cap = int(os.getenv('MAX_YOUTUBE_SEARCHES', '18'))
    searches = [(query, True) for query in queries] + [(queries[0], False)]
    for query, reusable_only in searches:
        if not key or budget.counts.get('youtube', 0) + 2 > cap or not budget.take('youtube'):
            break
        params = urllib.parse.urlencode({
            'key': key, 'part': 'snippet', 'type': 'video', 'q': query,
            'maxResults': 12,
            **({'videoLicense': 'creativeCommon'} if reusable_only else {}),
        })
        try:
            results = api.json_http('https://www.googleapis.com/youtube/v3/search?' + params, retries=0)
        except (api.Hold, api.Wait):
            warnings.append('YouTube search unavailable for: ' + query)
            continue
        for item in results.get('items', []):
            video_id = item.get('id', {}).get('videoId', '')
            if re.fullmatch(r'[\w-]{11}', video_id):
                candidates.setdefault(video_id, query)
    if candidates and budget.take('youtube'):
        for offset in range(0, len(candidates), 50):
            if offset and (budget.counts.get('youtube', 0) >= cap or not budget.take('youtube')):
                break
            ids = list(candidates)[offset:offset + 50]
            params = urllib.parse.urlencode({'key': key, 'part': 'status,snippet', 'id': ','.join(ids)})
            try:
                videos = api.json_http('https://www.googleapis.com/youtube/v3/videos?' + params,
                                       retries=0).get('items', [])
            except (api.Hold, api.Wait):
                warnings.append('YouTube license metadata unavailable')
                continue
            for video in videos:
                snippet = video.get('snippet', {})
                status = video.get('status', {})
                video_id = video.get('id')
                if (video_id not in candidates or snippet.get('liveBroadcastContent') in ('live', 'upcoming')):
                    continue
                reusable = status.get('license') == 'creativeCommon'
                leads.append({'title': 'Clip highlight: ' + snippet.get('title', video_id),
                              'category': 'clipper', 'content_type': 'clipper',
                              'source_video_id': video_id, 'discovery_query': candidates[video_id],
                              'discovery_only': not reusable,
                              'discovered_at': api.now(),
                              'media': {'source_url': 'https://www.youtube.com/watch?v=' + video_id,
                                        'creator': snippet.get('channelTitle', ''),
                                        'license': 'creativeCommon' if reusable else 'standardYouTube'}})
    if not any(not lead['discovery_only'] for lead in leads):
        warnings.append('YouTube returned no verified reusable video leads for the selected topics')

    seen = set()
    for query in queries[:4]:
        params = urllib.parse.urlencode({
            'action': 'query', 'generator': 'search', 'gsrsearch': 'filetype:video ' + query,
            'gsrnamespace': 6, 'gsrlimit': 6, 'prop': 'imageinfo', 'iilimit': 1,
            'iiprop': 'url|mime|size|extmetadata',
            'iiextmetadatafilter': 'LicenseShortName|LicenseUrl|Artist',
            'format': 'json', 'formatversion': 2,
        })
        try:
            result = api.json_http('https://commons.wikimedia.org/w/api.php?' + params, retries=0)
        except (api.Hold, api.Wait):
            warnings.append('Wikimedia Commons search unavailable for: ' + query)
            continue
        for page in result.get('query', {}).get('pages', []):
            info = next(iter(page.get('imageinfo', [])), {})
            metadata = info.get('extmetadata', {})
            license_name = metadata.get('LicenseShortName', {}).get('value', '')
            url = info.get('url', '')
            if (url in seen or urllib.parse.urlsplit(url).hostname != 'upload.wikimedia.org'
                    or not info.get('mime', '').startswith('video/')
                    or not 0 < info.get('size', 0) <= 250_000_000
                    or not re.fullmatch(r'CC BY [1-4]\.0|CC0|Public domain', license_name, re.I)):
                continue
            seen.add(url)
            artist = metadata.get('Artist', {}).get('value', '')
            leads.append({'title': 'Commons clip: ' + page['title'].removeprefix('File:'),
                          'category': 'clipper', 'content_type': 'clipper',
                          'discovery_query': query, 'discovered_at': api.now(),
                          'media': {'source_url': url, 'attribution_url': info.get('descriptionurl', ''),
                                    'creator': html.unescape(re.sub(r'<[^>]*>', '', artist)),
                                    'license': license_name,
                                    'license_url': metadata.get('LicenseUrl', {}).get('value', ''),
                                    'rights_confirmed': True, 'rights_note': 'Wikimedia Commons license metadata'}})
    if not seen:
        warnings.append('Wikimedia Commons returned no eligible video files for the selected topics')
    # Additional public video libraries; no new paid key or AI calls.
    import public_media
    for query in queries[:2]:
        for media in list(public_media.discover(query, api))[:6]:
            if media['source_url'] in seen:
                continue
            seen.add(media['source_url'])
            leads.append({'title': media['provider'] + ' clip: ' + media['title'],
                          'category': 'clipper', 'content_type': 'clipper',
                          'discovery_query': query, 'discovered_at': api.now(),
                          'media': {**media, 'rights_confirmed': True,
                                    'rights_note': 'Public provider item-level rights metadata; verify before posting'}})
    return leads


def source_media(entry, folder, api):
    media = entry.get('media', {})
    if not isinstance(media, dict):
        raise api.Hold('Clipper media manifest must be an object')
    cc = media.get('license') == 'creativeCommon'
    if not cc and (media.get('rights_confirmed') is not True or not media.get('rights_note')):
        raise api.Hold('Clipper needs owned/authorized footage or a Creative Commons source')
    if media.get('path'):
        path = api.local_file(media['path'])
        if media.get('sha256') != api.digest(path):
            raise api.Hold('Clipper local source changed or SHA256 confirmation missing')
        return path, {'provider': 'local', 'rights_note': media['rights_note']}
    url = media.get('source_url', '')
    api.public_url(url)
    p = urllib.parse.urlsplit(url)
    cached = api.load(folder / 'source.json', {})
    path = folder / cached.get('filename', 'source.mp4')
    if (cached.get('source_url') == url and path.is_file()
            and cached.get('sha256') == api.digest(path)):
        return path, cached
    social_hosts = ('instagram.com', 'www.instagram.com', 'tiktok.com', 'www.tiktok.com',
                    'x.com', 'www.x.com', 'twitter.com', 'www.twitter.com',
                    'facebook.com', 'www.facebook.com', 'fb.watch')
    if p.hostname in ('www.youtube.com', 'youtube.com', 'youtu.be') or p.hostname in social_hosts:
        if p.hostname in social_hosts and (cc or media.get('rights_confirmed') is not True):
            raise api.Hold('Social clip requires explicit reuse authorization')

        # Do not trust stale partial files from a previous failed download.
        for stale in folder.glob('source.*'):
            if stale.suffix in ('.mp4', '.webm', '.mkv', '.part', '.ytdl', '.json') and stale.is_file():
                stale.unlink()
        # Prefer a combined MP4, then allow separately served video/audio
        # streams when FFmpeg can merge them. Do not bypass restricted access.
        formats = (
            'b[height<=720][ext=mp4]/b[height<=720]',
            'bv*[height<=720]+ba/b[height<=720]/b',
        )
        download_ok = False
        for fmt in formats:
            try:
                api.run([sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-playlist',
                     '--max-filesize', '250M', '--match-filter', 'duration <= 1200 & !is_live',
                     '--format', fmt, '--merge-output-format', 'mp4',
                     '--output', str(folder / 'source.%(ext)s'), '--write-info-json',
                     '--no-warnings', '--quiet', '--no-progress', '--', url], timeout=360)
                download_ok = True
                break
            except api.Hold:
                continue
        if not download_ok:
            raise api.Hold('Clipper source unavailable in both combined and split formats; try another authorized source') from None
        info = api.load(folder / 'source.info.json', {})
        if not info:
            raise api.Hold('Clipper source unavailable or exceeds 20-minute download limit')
        if cc:
            license_text = str(info.get('license') or '').lower()
            license_url = str(info.get('license_url') or '').lower()
            if not ('creative commons' in license_text or 'creativecommons.org/licenses/' in license_url):
                raise api.Hold('Downloader could not confirm the source Creative Commons license')
        files = [x for x in folder.glob('source.*') if x.suffix in ('.mp4', '.webm', '.mkv')]
        if not files:
            raise api.Hold('Clipper download unavailable; supply a local source video')
        path = files[0]
        creator = info.get('uploader') or media.get('creator', '')
    else:
        if cc:
            raise api.Hold('Automatic Creative Commons discovery only accepts verified YouTube sources')
        raw, _, content_type = api.http(url, limit=250_000_000, retries=0)
        if not (content_type.startswith('video/') or 'octet-stream' in content_type):
            raise api.Hold('Clipper source URL must serve video bytes, not a webpage')
        path = folder / 'source.mp4'
        path.write_bytes(raw)
        creator = media.get('creator', '')
    metadata = {'source_url': url, 'filename': path.name, 'sha256': api.digest(path),
                'creator': creator, 'license': media.get('license', 'user-authorized'),
                'attribution_url': media.get('attribution_url', ''), 'license_url': media.get('license_url', ''),
                'rights_note': media.get('rights_note', '')}
    api.atomic_json(folder / 'source.json', metadata)
    return path, metadata


def validate_words(words, seconds, api):
    if not isinstance(words, list) or len(words) < 10:
        raise api.Hold('Clipper needs a timestamped transcript with at least ten words')
    clean, previous = [], -1.0
    for word in words:
        try:
            start, end = float(word['start']), float(word['end'])
            text = word['word'].strip()
        except (KeyError, TypeError, ValueError, AttributeError):
            raise api.Hold('Invalid clipper transcript word') from None
        if (not math.isfinite(start + end) or not 0 <= start < end <= seconds + .1
                or start < previous or not text or len(text) > 100):
            raise api.Hold('Clipper transcript timestamps/text are invalid')
        previous = end - .04  # tolerate small timestamp overlaps from ASR
        clean.append({'start': start, 'end': end, 'word': text})
    return clean


def transcript(entry, source, folder, seconds, source_hash, api):
    supplied = entry.get('transcript_words')
    if supplied is None and entry.get('transcript_path'):
        document = api.load(api.local_file(entry['transcript_path']), {})
        if document.get('source_sha256') != source_hash:
            raise api.Hold('Supplied clip transcript does not match source SHA256')
        supplied = document.get('words')
    if supplied is not None:
        return validate_words(supplied, seconds, api), 'user-supplied; review against audio'
    cached = api.load(folder / 'transcript.json', {})
    if cached.get('source_sha256') == source_hash:
        return validate_words(cached.get('words'), seconds, api), cached.get('method', 'local ASR')
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel('base.en', device='cpu', compute_type='int8', cpu_threads=2,
                             download_root=str(api.WORK / 'asr-models'))
        segments, _ = model.transcribe(str(source), word_timestamps=True, vad_filter=True,
                                       beam_size=5, condition_on_previous_text=False)
        words = [{'start': w.start, 'end': w.end, 'word': w.word} for s in segments for w in (s.words or [])]
    except Exception:
        raise api.Wait('Free local transcription unavailable; retry model download or supply timestamped transcript') from None
    words = validate_words(words, seconds, api)
    api.atomic_json(folder / 'transcript.json', {'source_sha256': source_hash, 'words': words,
                                              'method': 'faster-whisper base.en CPU; estimated word timings'})
    return words, 'faster-whisper base.en CPU; estimated word timings'


def sentences(words):
    groups, current = [], []
    for i, word in enumerate(words):
        current.append(word)
        gap = i == len(words) - 1 or words[i + 1]['start'] - word['end'] > .7
        if re.search(r'[.!?]["\u201d\u2019]*$', word['word']) or gap:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def choose_highlight(words, target, api):
    groups = sentences(words)
    candidates = []
    for i, group in enumerate(groups):
        selected = []
        for following in groups[i:]:
            selected += following
            start, end = selected[0]['start'], selected[-1]['end']
            seconds = end - start
            if seconds > target * 1.25:
                break
            if seconds < target * .75:
                continue
            text = ' '.join(x['word'] for x in selected)
            content = set(re.findall(r'[a-z]{4,}', text.lower()))
            hook = sum(term in text.lower() for term in ('because', 'actually', 'but ', 'why ', 'never', 'first', 'what happened'))
            # A transparent heuristic, not a claim that the clip will go viral.
            opening = ' '.join(w['word'] for w in selected if w['start'] < start + 3).lower()
            opening_hook = sum(term in opening for term in ('why', 'how', 'what', 'never', 'but', 'actually', '?', '!'))
            pauses = sum(max(0, b['start'] - a['end'] - .35) for a, b in zip(selected, selected[1:]))
            filler = sum(w['word'].lower().strip('.,!?') in ('um', 'uh', 'erm') for w in selected)
            score = len(content) / max(1, len(selected)) + hook * .12 + opening_hook * .35 - abs(seconds - target) / target - pauses / max(seconds, 1) - filler * .08
            candidates.append((score, start, end, list(selected)))
    if not candidates:
        # Keep a short exchange when no complete passage fits the preferred duration.
        # For a long continuous monologue, cut at a word boundary.
        chosen = max((group for group in groups
                      if group[-1]['end'] - group[0]['start'] <= target * 1.25),
                     key=lambda group: group[-1]['end'] - group[0]['start'], default=[])
        if not chosen:
            chosen = [word for word in words if word['end'] <= words[0]['start'] + target]
        if not chosen:
            raise api.Hold('No usable timed speech found for the excerpt')
        return chosen[0]['start'], chosen[-1]['end'], chosen
    _, start, end, chosen = max(candidates, key=lambda x: x[0])
    return start, end, chosen


def track_faces(source, start, end, api):
    try:
        import cv2
    except ImportError:
        raise api.Hold('Clipper tracking dependency missing; install requirements.txt') from None
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    if detector.empty():
        raise api.Hold('Clipper face detector data unavailable')
    cap = cv2.VideoCapture(str(source))
    points, previous, next_id = [], {}, 0
    try:
        for index in range(math.ceil((end - start) / .4)):
            t = start + index * .4
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                break
            h, width = frame.shape[:2]
            scale = min(1., 640 / width)
            gray = cv2.cvtColor(cv2.resize(frame, (round(width * scale), round(h * scale))), cv2.COLOR_BGR2GRAY)
            faces = detector.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(28, 28))
            current, used = {}, set()
            for x, y, fw, fh in faces:
                center = ((x + fw / 2) / gray.shape[1], (y + fh / 2) / gray.shape[0])
                matches = [(math.dist(center, item['center']), key) for key, item in previous.items() if key not in used]
                distance, identity = min(matches, default=(1., next_id))
                if distance > .15:
                    identity, next_id = next_id, next_id + 1
                used.add(identity)
                face = cv2.resize(gray[y:y + fh, x:x + fw], (48, 48))
                score = 0.
                if identity in previous:
                    delta = cv2.absdiff(face, previous[identity]['image'])
                    score = max(0., float(delta[29:45, 8:40].mean()) - float(delta[8:25, 8:40].mean()))
                current[identity] = {'center': center, 'image': face, 'score': score,
                    'box': [float(x / scale), float(y / scale), float(fw / scale), float(fh / scale)]}
            points.append({'time': t - start, 'faces': [dict(id=k, box=v['box'], score=v['score']) for k, v in current.items()]})
            previous = current
    finally:
        cap.release()
    return points


def shot_plan(words, start, end, points):
    boundaries = [0.]
    for group in sentences(words)[:-1]:
        t = group[-1]['end'] - start
        if t - boundaries[-1] >= 1.0 and end - start - t >= 1.0:
            boundaries.append(t)
    boundaries.append(end - start)
    shots, active = [], None
    for lo, hi in zip(boundaries, boundaries[1:]):
        samples = [p for p in points if lo <= p['time'] < hi]
        scores, appearances = {}, {}
        for sample in samples:
            for face in sample['faces']:
                identity = face['id']
                scores[identity] = scores.get(identity, 0) + face['score']
                appearances[identity] = appearances.get(identity, 0) + 1
        visible = [k for k, count in appearances.items() if count >= max(1, len(samples) * .6)]
        ranked = sorted(visible, key=lambda k: scores[k], reverse=True)
        if len(ranked) == 1:
            active = ranked[0]
        elif len(ranked) > 1 and scores[ranked[0]] > max(4., scores[ranked[1]] * 1.4):
            active = ranked[0]
        else:
            active = None  # retain full scene instead of guessing the speaker
        centers = []
        for sample in samples:
            face = next((f for f in sample['faces'] if f['id'] == active), None)
            if face:
                x, y, width, height = face['box']
                centers.append([sample['time'] - lo, x + width / 2, y + height / 2, width])
        shots.append({'start': lo, 'end': hi, 'speaker_track': active, 'centers': centers,
                      'mode': 'face_follow' if centers else 'full_scene_blur'})
    return shots


def motion_expression(points, axis, limit):
    """Piecewise interpolation with clamped endpoints, so pans do not jump."""
    if not points:
        return '0'
    values, smoothed = [], points[0][axis]
    for point in points:
        smoothed += .3 * (point[axis] - smoothed)
        values.append((point[0], min(limit, max(0., smoothed))))
    expression = f'{values[-1][1]:.3f}'
    for (lo, a), (hi, b) in reversed(list(zip(values, values[1:]))):
        if hi <= lo:
            continue
        value = f'{a:.3f}+({b-a:.3f})*clip((t-{lo:.3f})/{hi-lo:.3f},0,1)'
        expression = f'if(lt(t,{hi:.3f}),{value},{expression})'
    return expression


def caption_file(words, start, end, path, api):
    selected = [w for w in words if w['end'] > start and w['start'] < end]
    # Reuse the existing caption style but replace its estimated phrase timings.
    api.captions('caption style template', max(.1, end-start), path)
    header = path.read_text().split('Dialogue:', 1)[0]
    chunks, chunk = [], []
    for word in selected:
        if chunk and (len(chunk) >= 4 or len(' '.join(x['word'] for x in chunk + [word])) > 26):
            chunks.append(chunk)
            chunk = []
        chunk.append(word)
    if chunk:
        chunks.append(chunk)
    rows = []
    for chunk in chunks:
        text = ' '.join(x['word'] for x in chunk).replace('\\', '').replace('{', '').replace('}', '').replace('\n', ' ')
        rows.append(f'Dialogue: 0,{api.ass_time(max(0.,chunk[0]["start"]-start))},'
                    f'{api.ass_time(min(end-start,chunk[-1]["end"]-start))},Default,,0,0,0,,{text}')
    path.write_text(header + '\n'.join(rows) + '\n')


def render(source, words, start, end, shots, folder, output, api):
    streams = api.probe(source)['streams']
    video = next(x for x in streams if x['codec_type'] == 'video')
    if not any(x['codec_type'] == 'audio' for x in streams):
        raise api.Hold('Clipper source must contain its original audio')
    width, height = video['width'], video['height']
    parts = []
    for i, shot in enumerate(shots):
        lo, hi = start + shot['start'], start + shot['end']
        subs, part = folder / f'clip-{i:03}.ass', folder / f'clip-{i:03}.mp4'
        caption_file(words, lo, hi, subs, api)
        if shot['centers']:
            face_width = sorted(x[3] for x in shot['centers'])[len(shot['centers'])//2]
            cw = int(min(width, height * 9 / 16, max(height * 9 / 16 * .55, face_width * 4))) // 2 * 2
            ch = int(cw * 16 / 9) // 2 * 2
            positions = [[p[0], p[1]-cw/2, p[2]-ch*.28] for p in shot['centers']]
            x = motion_expression(positions, 1, width-cw)
            y = motion_expression(positions, 2, height-ch)
            graph = f"[0:v]setpts=PTS-STARTPTS,crop={cw}:{ch}:x='{x}':y='{y}',scale=1080:1920,setsar=1[v]"
        else:
            graph = ('[0:v]setpts=PTS-STARTPTS,split=2[bg][fg];'
                '[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[b];'
                '[fg]scale=1080:1536:force_original_aspect_ratio=increase,crop=1080:1536[f];'
                '[b][f]overlay=(W-w)/2:(H-h)/2,setsar=1[v]')
        graph += f';[v]fps=30,subtitles={subs.as_posix()},format=yuv420p[out]'
        api.run(['ffmpeg','-v','error','-y','-ss',f'{lo:.3f}','-i',str(source),'-t',f'{hi-lo:.3f}',
                 '-filter_complex',graph,'-map','[out]','-map','0:a:0','-c:v','libx264','-preset','veryfast',
                 '-crf','21','-c:a','aac','-ar','48000','-ac','2',
                 '-af','asetpts=PTS-STARTPTS,loudnorm=I=-16:TP=-1.5:LRA=11','-movflags','+faststart',str(part)], timeout=300)
        parts.append(part)
    concat = folder / 'clip-concat.txt'
    concat.write_text(''.join("file '"+str(p)+"'\n" for p in parts))
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name('video.partial.mp4')
    api.run(['ffmpeg','-v','error','-y','-f','concat','-safe','0','-i',str(concat),
             '-c','copy','-movflags','+faststart',str(partial)])
    quality = api.media_quality(partial, end-start)
    partial.replace(output)
    return quality


def process(entry, target, *, api):
    folder = api.WORK / entry['id']
    folder.mkdir(parents=True, exist_ok=True)
    source, metadata = source_media(entry, folder, api)
    source_hash = api.digest(source)
    if source.stat().st_size > 250_000_000:
        raise api.Hold('Clipper source exceeds the 250 MB limit')
    if source_hash in entry.get('_excluded_media_hashes', []):
        raise api.Hold('A different original source video is required; this source was already clipped')
    seconds = api.duration(source)
    kinds = {s['codec_type'] for s in api.probe(source).get('streams', [])}
    if not {'audio', 'video'} <= kinds:
        raise api.Hold('Clipper source needs video and its original audio')
    if seconds > 1200:
        raise api.Hold('Clipper source exceeds the 20-minute CPU/transcription limit')
    words, method = transcript(entry, source, folder, seconds, source_hash, api)
    start, end, chosen = choose_highlight(words, target, api)
    points = track_faces(source, start, end, api)
    shots = shot_plan(chosen, start, end, points)
    output = api.OUTPUT / entry['id'] / 'video.mp4'
    quality = render(source, chosen, start, end, shots, folder, output, api)
    quality['caption_timing'] = method
    quality['requires_human_review'] += ['original transcript accuracy', 'excerpt context',
        'face tracking and active speaker (visual heuristic)', 'source attribution and reuse rights']
    attribution_url = metadata.get('attribution_url') or metadata.get('source_url') or entry.get('media', {}).get('source_url', '')
    # Omit URL queries that might contain signed media credentials.
    if attribution_url:
        parsed = urllib.parse.urlsplit(attribution_url)
        if parsed.hostname not in ('www.youtube.com','youtube.com','youtu.be'):
            attribution_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))
    attribution = {'creator': metadata.get('creator', ''), 'url': attribution_url,
                   'license': metadata.get('license', 'user-authorized'), 'license_url': metadata.get('license_url', ''),
                   'modifications': 'Excerpted, reframed, normalized audio, transcribed captions'}
    review = {'schema_version': 3, 'job_id': entry['id'], 'content_type': 'clipper',
        'status': 'REVIEW_REQUIRED', 'generated_at': api.now(), 'video_sha256': api.digest(output),
        'source_media_sha256': source_hash, 'source_range': {'start': start, 'end': end},
        'attribution': attribution, 'transcript_method': method, 'transcript_words': chosen,
        'shots': shots, 'quality': quality, 'selection': 'sentence-bounded highlight heuristic; no virality claim',
        'publishing': 'disabled; no upload endpoint is called'}
    api.atomic_json(output.parent / 'review.json', review)
    # Credit the actual originating channel without fabricating a social handle.
    creator_credit = attribution['creator'].strip() or 'Original creator (verify before posting)'
    credit_caption = (entry['title'] + '\n\nCredit: ' + creator_credit +
                      '\nOriginal video: ' + attribution_url +
                      ('\nLicense: ' + attribution['license_url'] if attribution['license_url'] else '') +
                      '\nEdited highlight with reframing and captions.\n'
                      'Verify creator permission and any third-party music before publishing.\n')
    (output.parent / 'caption.txt').write_text(credit_caption)
    api.run(['ffmpeg','-v','error','-y','-i',str(output),'-vf','thumbnail,scale=270:480',
             '-frames:v','1',str(output.parent / 'preview.jpg')])
    entry.update(status='REVIEW_REQUIRED', video_sha256=review['video_sha256'],
                 source_media_sha256=source_hash, completed_at=api.now())
    entry.pop('blocker', None)
