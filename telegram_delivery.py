"""Deliver hash-verified draft MP4s to the explicitly configured Telegram chat."""
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid
import worker as w

def upload(token, chat, video, caption):
    boundary = 'Pulse' + uuid.uuid4().hex
    chunks = []
    for key, value in {'chat_id': chat, 'caption': caption[:1024], 'supports_streaming': 'true'}.items():
        chunks.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+key+
                       '"\r\n\r\n'+value+'\r\n').encode())
    chunks.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="video"; filename="ZENTR_Pulse.mp4"'
                   '\r\nContent-Type: video/mp4\r\n\r\n').encode())
    chunks += [video.read_bytes(), ('\r\n--'+boundary+'--\r\n').encode()]
    request = urllib.request.Request('https://api.telegram.org/bot'+token+'/sendVideo',
        data=b''.join(chunks), headers={'Content-Type': 'multipart/form-data; boundary='+boundary})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.loads(response.read(2_000_000))
        except urllib.error.HTTPError as exc:
            with exc:
                if exc.code >= 500:
                    raise w.Hold('Telegram delivery unconfirmed; inspect chat before retrying') from None
                if exc.code != 429 or attempt == 2:
                    return {'ok': False, 'error_code': exc.code}
                try:
                    delay = json.loads(exc.read(65_536))['parameters']['retry_after']
                except (KeyError, TypeError, ValueError):
                    return {'ok': False, 'error_code': 429}
            if type(delay) is not int or not 1 <= delay <= 20:
                return {'ok': False, 'error_code': 429}
            time.sleep(delay)
        except (OSError, ValueError):
            raise w.Hold('Telegram delivery unconfirmed; inspect chat before retrying') from None

def deliver(state):
    token, chat = os.getenv('TELEGRAM_BOT_TOKEN', '').strip(), os.getenv('TELEGRAM_CHAT_ID', '').strip()
    missing = [name for name, value in [('TELEGRAM_BOT_TOKEN', token), ('TELEGRAM_CHAT_ID', chat)] if not value]
    if missing:
        raise w.Hold('Telegram configuration missing: '+', '.join(missing))
    sent = 0
    ledger = state.setdefault('telegram_deliveries', {})
    for review_path in sorted(w.OUTPUT.glob('*/review.json')):
        review = w.load(review_path, {})
        video = review_path.parent / 'video.mp4'
        if review.get('status') != 'REVIEW_REQUIRED' or not video.is_file():
            continue
        sha = w.digest(video)
        if review.get('video_sha256') != sha:
            raise w.Hold('Telegram draft hash does not match review')
        if video.stat().st_size > 49_000_000:
            raise w.Hold('Telegram draft exceeds configured 49 MB upload limit')
        key = hashlib.sha256((token.split(':')[0]+'|'+chat+'|'+sha).encode()).hexdigest()
        prior = ledger.get(key, {})
        if prior.get('status') == 'DELIVERED':
            print('Telegram: draft already delivered')
            continue
        if prior.get('status') == 'UNCONFIRMED':
            raise w.Hold('Prior Telegram delivery unconfirmed; inspect chat before retrying')
        ledger[key] = {'status': 'UNCONFIRMED', 'video_sha256': sha, 'started_at': w.now()}
        w.atomic_json(w.STATE / 'queue.json', state)
        title = review.get('plan', {}).get('title', 'ZENTR Pulse video draft')
        result = upload(token, chat, video, title+'\nDraft for your review. Not published.')
        if result.get('ok') is not True:
            ledger[key]['status'] = 'REJECTED'
            ledger[key]['error_code'] = result.get('error_code')
            w.atomic_json(w.STATE / 'queue.json', state)
            raise w.Hold('Telegram rejected delivery; HTTP '+str(result.get('error_code', 'unknown')))
        message = result.get('result', {})
        if not message.get('message_id') or not message.get('video'):
            raise w.Hold('Telegram response did not confirm a video message')
        ledger[key].update(status='DELIVERED', message_id=message['message_id'], completed_at=w.now())
        w.atomic_json(w.STATE / 'queue.json', state)
        print('TELEGRAM VIDEO DELIVERED: message '+str(message['message_id']))
        sent += 1
    return sent

if __name__ == '__main__':
    try:
        w.STATE.mkdir(exist_ok=True)
        state = w.load(w.STATE / 'queue.json', {'schema_version': 3, 'jobs': {}})
        print('Telegram newly delivered drafts: '+str(deliver(state)))
    except w.Hold as exc:
        print(str(exc))
        raise SystemExit(2)
