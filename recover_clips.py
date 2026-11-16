"""Render existing clip leads without discovery or generative AI."""
import os
import worker as w

def rescue(state, target):
    attempted, completed = [], []
    state['last_run'] = {'started_at': w.now(), 'mode': 'direct_clip_recovery',
                         'attempted_ids': attempted, 'completed_ids': completed}
    existing_hashes = [j['source_media_sha256'] for j in state['jobs'].values()
                       if j.get('source_media_sha256') and j.get('status') == 'REVIEW_REQUIRED']
    jobs = [j for j in state['jobs'].values()
            if j.get('content_type') == 'clipper'
            and j.get('status') in ('QUEUED', 'WAITING', 'PRODUCING')]
    jobs.sort(key=lambda j: (not (w.WORK / j['id'] / 'source.json').is_file(),
                             j.get('last_attempt_at', '')))
    for job in jobs[:3]:
        attempted.append(job['id'])
        job.update(status='PRODUCING', attempts=job.get('attempts', 0)+1, last_attempt_at=w.now())
        try:
            job['_excluded_media_hashes'] = existing_hashes
            # clipper uses source audio, local transcription and FFmpeg; no Gemini.
            w.process(job, None, target)
            path = w.OUTPUT / job['id'] / 'video.mp4'
            review = w.load(path.parent / 'review.json', {})
            if not path.is_file() or review.get('video_sha256') != w.digest(path):
                raise w.Hold('No matching playable draft and review record')
            completed.append(job['id'])
            existing_hashes.append(job['source_media_sha256'])
        except w.Wait as exc:
            job.update(status='WAITING', blocker=str(exc))
        except w.Hold as exc:
            job.update(status='HOLD', blocker=str(exc))
        except Exception:
            job.update(status='FAILED', blocker='Direct clip processing failed; inspect source and transcription')
        finally:
            job.pop('_excluded_media_hashes', None)
            w.atomic_json(w.STATE / 'queue.json', state)
    state['last_run'].update(finished_at=w.now(), attempted=len(attempted), completed=len(completed))
    # Recovery previews do not advance or erase the user's three-video rotation.
    w.atomic_json(w.STATE / 'queue.json', state)
    w.report(state, ['Direct clip recovery: original footage and audio; no generative AI. Rotation retained.'])
    return 0 if completed else 2

if __name__ == '__main__':
    w.STATE.mkdir(exist_ok=True)
    state = w.load(w.STATE / 'queue.json', {'schema_version': 3, 'jobs': {}})
    raise SystemExit(rescue(state, int(os.getenv('TARGET_SECONDS', '30'))))
