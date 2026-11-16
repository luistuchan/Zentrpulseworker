import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import recover_clips as recovery

class DirectRecoveryTests(unittest.TestCase):
    def test_failed_source_moves_to_next_clip_without_ai(self):
        w = recovery.w
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = {'schema_version': 3, 'budget': {'ai': 96},
                'content_batch': {'category': 'world_news', 'completed': 0},
                'jobs': {k: {'id': k, 'title': k, 'content_type': 'clipper', 'status': 'QUEUED'}
                         for k in ('first', 'second')}}
            def process(job, budget, target):
                if job['id'] == 'first':
                    raise w.Hold('Source unavailable')
                path = root / 'output' / job['id'] / 'video.mp4'
                path.parent.mkdir(parents=True)
                path.write_bytes(b'fixture')
                w.atomic_json(path.parent / 'review.json', {'video_sha256': w.digest(path)})
                job.update(status='REVIEW_REQUIRED', source_media_sha256='different-source')
            with patch.object(w, 'STATE', root / 'state'), patch.object(w, 'WORK', root / 'work'), \
                 patch.object(w, 'OUTPUT', root / 'output'), patch.object(w, 'process', side_effect=process), \
                 patch.object(w, 'report'), patch.object(w, 'ai', side_effect=AssertionError('AI must not be used')):
                self.assertEqual(recovery.rescue(state, 30), 0)
            self.assertEqual(state['jobs']['first']['status'], 'HOLD')
            self.assertEqual(state['last_run']['completed_ids'], ['second'])
            self.assertEqual(state['budget']['ai'], 96)
            self.assertEqual(state['content_batch'], {'category': 'world_news', 'completed': 0})
