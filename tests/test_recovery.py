import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from test_worker import w


class RecoveryTests(unittest.TestCase):
    def test_temporary_gemini_failure_retries(self):
        budget = Mock()
        budget.take.return_value = True
        response = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': '{"ok":true}'}]}}]}
        with patch.dict(os.environ, {'FREE_TIER_CONFIRMED': '1', 'GEMINI_API_KEY': 'test'}), \
             patch.object(w, 'json_http', side_effect=[w.Wait('Service HTTP 503'), w.Wait('Service HTTP 503'), response]) as request, \
             patch.object(w.time, 'sleep') as sleep:
            self.assertEqual(w.ai('test', budget), {'ok': True})
            self.assertEqual(request.call_count, 3)
            self.assertEqual([x.args[0] for x in sleep.call_args_list], [5, 10])

    def test_quota_error_does_not_retry_immediately(self):
        budget = Mock()
        budget.take.return_value = True
        with patch.dict(os.environ, {'FREE_TIER_CONFIRMED': '1', 'GEMINI_API_KEY': 'test'}), \
             patch.object(w, 'json_http', side_effect=w.Wait('Service HTTP 429')) as request, \
             patch.object(w.time, 'sleep') as sleep:
            with self.assertRaises(w.Wait):
                w.ai('test', budget)
            self.assertEqual(request.call_count, 1)
            sleep.assert_not_called()

    def test_broader_query_is_evaluated(self):
        def candidates(query, budget):
            start = 0 if query == 'specific' else 10
            return [{'id': start + i, 'duration': 10, 'videos': {'small': {
                'url': 'https://pixabay.com/test.mp4', 'width': 1080, 'height': 1920, 'size': 100}}}
                for i in range(4)]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            scene = {'id': 's01', 'visual_description': 'A subject', '_required_seconds': 2}
            with patch.object(w, 'OUTPUT', root), \
                 patch.object(w, 'stock_queries', return_value=['specific', 'broader']), \
                 patch.object(w, 'pixabay_candidates', side_effect=candidates), \
                 patch.object(w, 'http', return_value=(b'fixture', '', 'video/mp4')), \
                 patch.object(w, 'duration', return_value=10), \
                 patch.object(w, 'run'), \
                 patch.object(w, 'ai', side_effect=[{'matches': False}, {'matches': False}, {'matches': True}]) as match:
                _, metadata = w.select_pixabay_asset(scene, {'id': 'test'}, root, Mock())
                self.assertEqual(metadata['id'], 10)
                self.assertEqual(match.call_count, 3)
