"""Explicit, hash-bound YouTube publishing adapter; never called by worker.py.

Usage: python publish.py --prepare JOB_ID
Review video, edit approvals/JOB_ID.json to publish=true and desired privacy.
Then: python publish.py --publish JOB_ID
Run on a trusted machine with authoritative state/queue.json restored first.
"""
import argparse
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
import worker as w


def request(url, data, headers, method='POST'):
    w.public_url(url)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.build_opener(w.SafeRedirect()).open(req, timeout=180) as response:
            return response.read(2_000_000), dict(response.headers)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise w.Hold('Upload request failed. Uploads are never blindly retried; check YouTube Studio.') from None


def paths(job_id):
    if not re.fullmatch(r'[a-f0-9]{16}', job_id):
        raise w.Hold('Invalid job ID')
    folder = w.OUTPUT / job_id
    return folder / 'video.mp4', folder / 'review.json', w.ROOT / 'approvals' / (job_id + '.json')


def prepare(job_id):
    video, review, approval = paths(job_id)
    if not video.is_file() or not review.is_file():
        raise w.Hold('Restore this job video and review.json before preparing approval')
    if approval.exists():
        raise w.Hold('Approval already exists; inspect it rather than overwriting')
    data = w.load(review, {})
    if data.get('video_sha256') != w.digest(video):
        raise w.Hold('Video does not match review record')
    w.atomic_json(approval, {'job_id': job_id, 'video_sha256': w.digest(video),
                            'review_sha256': w.digest(review), 'publish': False,
                            'privacy_status': 'private', 'made_for_kids': False,
                            'reviewed_facts_visuals_rights_and_captions': False})
    return approval


def validate_approval(job_id):
    video, review_path, approval_path = paths(job_id)
    approval, review = w.load(approval_path, {}), w.load(review_path, {})
    if approval.get('job_id') != job_id or approval.get('publish') is not True:
        raise w.Hold('Explicit publish approval is missing')
    if approval.get('reviewed_facts_visuals_rights_and_captions') is not True:
        raise w.Hold('Content review is not confirmed')
    if (approval.get('video_sha256') != w.digest(video) or review.get('video_sha256') != w.digest(video)
            or approval.get('review_sha256') != w.digest(review_path)):
        raise w.Hold('Video or review changed after approval')
    if approval.get('privacy_status') not in ('private', 'unlisted', 'public'):
        raise w.Hold('Invalid approved privacy setting')
    if not isinstance(approval.get('made_for_kids'), bool):
        raise w.Hold('Audience declaration missing')
    if video.stat().st_size > 200_000_000:
        raise w.Hold('Video exceeds this adapter upload-size limit')
    return video, review, approval


def publish(job_id):
    video, review, approval = validate_approval(job_id)
    required = ('YOUTUBE_CLIENT_ID', 'YOUTUBE_CLIENT_SECRET', 'YOUTUBE_REFRESH_TOKEN')
    if not all(os.getenv(x) for x in required):
        raise w.Hold('YouTube OAuth secrets missing; a Data API key cannot upload videos')
    state_path = w.STATE / 'queue.json'
    state = w.load(state_path, None)
    if not state or job_id not in state.get('jobs', {}):
        raise w.Hold('Restore authoritative production state before uploading')
    job = state['jobs'][job_id]
    if job.get('video_sha256') != approval['video_sha256']:
        raise w.Hold('Video hash does not match authoritative queue')
    if job.get('upload'):
        raise w.Hold('An upload attempt already exists. Check YouTube Studio before any further action')
    token_data = urllib.parse.urlencode({'client_id': os.environ['YOUTUBE_CLIENT_ID'],
                                       'client_secret': os.environ['YOUTUBE_CLIENT_SECRET'],
                                       'refresh_token': os.environ['YOUTUBE_REFRESH_TOKEN'],
                                       'grant_type': 'refresh_token'}).encode()
    raw, _ = request('https://oauth2.googleapis.com/token', token_data,
                     {'Content-Type': 'application/x-www-form-urlencoded'})
    token = json.loads(raw).get('access_token')
    if not token:
        raise w.Hold('OAuth token refresh failed')
    metadata = {'snippet': {'title': review['plan']['title'][:100],
                            'description': (review['plan']['description'] + '\n\nSources:\n'
                                            + '\n'.join(x['url'] for x in review['sources'])
                                            + '\n\nZENTR job: ' + job_id)[:5000],
                            'categoryId': '27'},
                'status': {'privacyStatus': approval['privacy_status'],
                           'selfDeclaredMadeForKids': approval['made_for_kids']}}
    # Persist intent before any upload operation. A crash becomes an explicit hold.
    job['upload'] = {'status': 'IN_PROGRESS_OR_AMBIGUOUS', 'started_at': w.now(),
                     'sha256': approval['video_sha256']}
    w.atomic_json(state_path, state)
    _, headers = request('https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status',
                         json.dumps(metadata).encode(), {'Authorization': 'Bearer ' + token,
                         'Content-Type': 'application/json', 'X-Upload-Content-Type': 'video/mp4',
                         'X-Upload-Content-Length': str(video.stat().st_size)})
    session = next((v for k, v in headers.items() if k.lower() == 'location'), '')
    host = urllib.parse.urlsplit(session).hostname or ''
    if not session.startswith('https://') or not (host == 'googleapis.com' or host.endswith('.googleapis.com')):
        raise w.Hold('Invalid upload-session endpoint')
    raw, _ = request(session, video.read_bytes(), {'Authorization': 'Bearer ' + token,
                      'Content-Type': 'video/mp4', 'Content-Length': str(video.stat().st_size)}, 'PUT')
    result = json.loads(raw)
    if not result.get('id'):
        raise w.Hold('Upload response ambiguous; check YouTube Studio before retrying')
    job['upload'] = {'status': 'UPLOADED', 'youtube_id': result['id'], 'completed_at': w.now(),
                     'requested_privacy': approval['privacy_status'],
                     'reported_privacy': result.get('status', {}).get('privacyStatus', 'unknown')}
    w.atomic_json(state_path, state)
    return 'https://www.youtube.com/watch?v=' + result['id']


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare')
    group.add_argument('--publish')
    args = parser.parse_args()
    # Same lock as production, so upload cannot race a local state mutation.
    import fcntl
    w.STATE.mkdir(exist_ok=True)
    with (w.STATE / 'worker.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise w.Hold('Another worker or uploader is active') from None
        print(prepare(args.prepare) if args.prepare else publish(args.publish))


if __name__ == '__main__':
    try:
        main()
    except w.Hold as exc:
        print('Publishing held: ' + str(exc))
        raise SystemExit(1)
