import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import telegram_delivery as tg

class TelegramDeliveryTests(unittest.TestCase):
    def test_missing_credentials_does_not_send(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': '', 'TELEGRAM_CHAT_ID': ''}), patch.object(tg, 'upload') as send:
            with self.assertRaises(tg.w.Hold):
                tg.deliver({})
            send.assert_not_called()

    def test_confirmed_video_is_not_sent_twice(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / 'output' / 'draft' / 'video.mp4'
            video.parent.mkdir(parents=True)
            video.write_bytes(b'fixture')
            tg.w.atomic_json(video.parent / 'review.json',
                             {'status': 'REVIEW_REQUIRED', 'video_sha256': tg.w.digest(video)})
            state = {'jobs': {}}
            with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': '1:test', 'TELEGRAM_CHAT_ID': '2'}), \
                 patch.object(tg.w, 'OUTPUT', root / 'output'), patch.object(tg.w, 'STATE', root / 'state'), \
                 patch.object(tg, 'upload', return_value={'ok': True, 'result': {'message_id': 7, 'video': {'file_id': 'test'}}}) as send:
                self.assertEqual(tg.deliver(state), 1)
                self.assertEqual(tg.deliver(state), 0)
                self.assertEqual(send.call_count, 1)
