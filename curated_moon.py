"""Curated science draft from a directly downloadable NASA visualization."""
import worker as w

def main():
    entry = {'id': 'moon-rotation-science', 'title': 'The Moon is spinning. Here is why you cannot tell.',
             'assets': {}}
    folder = w.WORK / entry['id']
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / 'nasa-orbit.mp4'
    url = 'https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004709/orbit_1080p30.mp4'
    if not source.is_file():
        source.write_bytes(w.http(url, limit=30_000_000)[0])
    total_source = w.duration(source)
    lines = [
        'The Moon is spinning. So why do we keep seeing the same face?',
        'This NASA animation makes the hidden movement easier to follow.',
        'Watch the yellow line. The Moon turns as it travels around Earth.',
        'One spin takes the same time as one trip around our planet.',
        'That synchronized motion keeps essentially the same side facing us.',
        'It is called synchronous rotation. Seeing one face does not mean no spin.',
    ]
    scenes = []
    offset = 0.
    for i, text in enumerate(lines, 1):
        scene = {'id': f's{i:02}', 'narration': text, 'requires_exact_footage': True}
        _, seconds = w.narration(scene, folder, entry)
        if offset + seconds > total_source:
            raise w.Hold('NASA source too short for the curated narration')
        entry['assets'][scene['id']] = {
            'path': str(source.relative_to(w.ROOT)), 'sha256': w.digest(source),
            'rights_confirmed': True, 'matches_scene': True,
            'rights_note': 'NASA SVS 4709 educational visualization; attributed; no endorsement implied.',
            'source_url': 'https://svs.gsfc.nasa.gov/4709', 'creator': 'NASA Scientific Visualization Studio',
            'start_seconds': offset, 'visualization': True}
        offset += seconds
        scenes.append(scene)
    plan = {'title': entry['title'], 'description': 'Why the Moon keeps one face toward Earth. NASA visualization, edited with original narration and captions.', 'scenes': scenes}
    result, quality, assets = w.render(plan, entry, folder, None, round(offset))
    sources = ['https://svs.gsfc.nasa.gov/4709', 'https://science.nasa.gov/moon/facts/']
    review = {'job_id': entry['id'], 'status': 'REVIEW_REQUIRED',
        'content_type': 'science_explainer', 'video_sha256': w.digest(result),
        'generated_at': w.now(), 'plan': plan, 'quality': quality, 'assets': assets,
        'sources': sources, 'visuals': 'NASA scientific animation, not camera footage or renderer test',
        'editorial_check': 'Curated against NASA explanation; no generative model was called',
        'publishing': 'disabled; human review required'}
    w.atomic_json(result.parent / 'review.json', review)
    (result.parent / 'caption.txt').write_text(plan['title'] + '\n\n' + plan['description']
        + '\n\nVisualization: NASA Scientific Visualization Studio\n' + '\n'.join(sources))
    w.atomic_json(w.OUTPUT / 'curated-result.json', {'outcome': 'REVIEW_READY', 'video': str(result.relative_to(w.OUTPUT)),
                                                   'sha256': w.digest(result), 'duration': w.duration(result)})
    print('REAL SCIENCE DRAFT READY: ' + str(result.relative_to(w.OUTPUT)))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
