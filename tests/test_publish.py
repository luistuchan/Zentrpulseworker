import importlib.util
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import test_worker

w = test_worker.w
sys.modules['worker'] = w
SPEC = importlib.util.spec_from_file_location('publish', Path(__file__).resolve().parents[1] / 'publish.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(w, 'ROOT', self.root), patch.object(w, 'OUTPUT', self.root / 'output'),
                        patch.object(w, 'STATE', self.root / 'state')]
        for item in self.patches:
            item.start()
        self.job = '0123456789abcdef'
        self.video, self.review, self.approval = p.paths(self.job)
        self.video.parent.mkdir(parents=True)
        self.video.write_bytes(b'fixture video')
        w.atomic_json(self.review, {'video_sha256': w.digest(self.video), 'plan': {}})
        p.prepare(self.job)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def approve(self):
        value = w.load(self.approval, {})
        value.update(publish=True, reviewed_facts_visuals_rights_and_captions=True)
        w.atomic_json(self.approval, value)

    def test_default_is_not_approved(self):
        with self.assertRaises(w.Hold):
            p.validate_approval(self.job)

    def test_exact_hash_approval(self):
        self.approve()
        video, _, approval = p.validate_approval(self.job)
        self.assertEqual(video, self.video)
        self.assertEqual(approval['privacy_status'], 'private')

    def test_changed_video_invalidates_approval(self):
        self.approve()
        self.video.write_bytes(b'changed video')
        with self.assertRaises(w.Hold):
            p.validate_approval(self.job)

    def test_changed_review_invalidates_approval(self):
        self.approve()
        self.review.write_text(self.review.read_text() + ' ')
        with self.assertRaises(w.Hold):
            p.validate_approval(self.job)

    def test_approval_cannot_be_overwritten(self):
        with self.assertRaises(w.Hold):
            p.prepare(self.job)

    def test_ambiguous_attempt_cannot_repeat(self):
        self.approve()
        w.atomic_json(w.STATE / 'queue.json', {'jobs': {self.job: {
            'video_sha256': w.digest(self.video), 'upload': {'status': 'IN_PROGRESS_OR_AMBIGUOUS'}}}})
        with patch.dict('os.environ', {'YOUTUBE_CLIENT_ID': 'test', 'YOUTUBE_CLIENT_SECRET': 'test',
                                      'YOUTUBE_REFRESH_TOKEN': 'test'}), patch.object(p, 'request') as network:
            with self.assertRaises(w.Hold):
                p.publish(self.job)
            network.assert_not_called()


if __name__ == '__main__':
    unittest.main()
