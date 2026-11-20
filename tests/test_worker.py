import copy
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('pulse', Path(__file__).resolve().parents[1] / 'worker.py')
w = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(w)


def fixture():
    quote = 'This is a documented example with enough exact text to validate.'
    sources = [{'id': 's1', 'url': 'https://alpha.example/article', 'text': quote},
               {'id': 's2', 'url': 'https://beta.example/article', 'text': quote + ' Second.'}]
    claims = [{'id': 'c1', 'text': 'An example.', 'evidence': [{'source_id': 's1', 'quote': quote},
                                                           {'source_id': 's2', 'quote': quote}]}]
    scenes = [{'id': f's{i:02}', 'narration': 'This documented example explains how a mechanism works clearly.',
               'claim_ids': ['c1'], 'visual_query': 'mechanism', 'visual_description': 'A mechanism',
               'requires_exact_footage': True} for i in range(1, 7)]
    return {'decision': 'draft', 'title': 'Example', 'description': 'Explanation',
            'claims': claims, 'scenes': scenes}, sources


class WorkerTests(unittest.TestCase):
    def test_stalled_news_batch_allows_clipper_with_ai_cap_exhausted(self):
        news_batch = {'category': 'world_news', 'completed': 1, 'story_keys': ['prior-news']}
        state = {
            'content_batch': news_batch,
            'jobs': {
                'news': {'id': 'news', 'title': 'Waiting news', 'category': 'world_news',
                         'status': 'WAITING', 'blocker': 'AI request cap reached'},
                'clip': {'id': 'clip', 'title': 'Stream highlight', 'category': 'clipper',
                         'content_type': 'clipper', 'status': 'QUEUED'},
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(w, 'WORK', root), patch.object(w, 'STATE', root), \
                    patch.object(w, 'process', side_effect=w.Hold('Test clip source unavailable')) as process, \
                    patch.dict(os.environ, {'MAX_AI_CALLS': '96'}):
                budget = w.Budget(state)
                budget.counts['ai'] = 96
                w.produce_batch(state, budget, 30)
        self.assertEqual(state['last_run']['attempted_ids'], ['clip'])
        self.assertEqual(state['deferred_content_batches']['world_news'], news_batch)
        self.assertEqual(state['jobs']['news']['status'], 'WAITING')
        process.assert_called_once()

    def test_valid_plan(self):
        plan, sources = fixture()
        self.assertIs(w.validate_plan(plan, sources, 20), plan)

    def test_fabricated_quote_rejected(self):
        plan, sources = fixture()
        plan['claims'][0]['evidence'][0]['quote'] = 'Fabricated quote not in the source text.'
        with self.assertRaises(w.Hold):
            w.validate_plan(plan, sources, 20)

    def test_one_domain_rejected(self):
        plan, sources = fixture()
        sources[1]['url'] = 'https://www.alpha.example/second'
        with self.assertRaises(w.Hold):
            w.validate_plan(plan, sources, 20)

    def test_unsupported_scene_rejected(self):
        plan, sources = fixture()
        plan['scenes'][0]['claim_ids'] = ['invented']
        with self.assertRaises(w.Hold):
            w.validate_plan(plan, sources, 20)

    def test_duplicate_scene_rejected(self):
        plan, sources = fixture()
        plan['scenes'][1]['id'] = plan['scenes'][0]['id']
        with self.assertRaises(w.Hold):
            w.validate_plan(plan, sources, 20)

    def test_wrong_script_length_rejected(self):
        plan, sources = fixture()
        with self.assertRaises(w.Hold):
            w.validate_plan(plan, sources, 70)

    def test_duplicate_titles(self):
        self.assertTrue(w.duplicate('How the hidden game mechanic really works!', ['How the hidden game mechanic really works']))
        self.assertFalse(w.duplicate('A comet passing Jupiter', ['Underwater diving safety']))

    def test_private_urls_blocked(self):
        for url in ('file:///etc/passwd', 'http://example.com', 'https://user:pass@example.com',
                    'https://127.0.0.1/', 'https://[::1]/'):
            with self.subTest(url=url), self.assertRaises(w.Hold):
                w.public_url(url)

    def test_path_traversal_blocked(self):
        with self.assertRaises(w.Hold):
            w.local_file('../../../../etc/passwd')

    def test_budget_persists_and_caps(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(w, 'STATE', Path(temp)), patch.dict(os.environ, {'MAX_AI_CALLS': '2'}):
            state = {'schema_version': 3, 'jobs': {}}
            budget = w.Budget(state)
            self.assertTrue(budget.take('ai'))
            self.assertTrue(budget.take('ai'))
            self.assertFalse(budget.take('ai'))
            self.assertEqual(w.load(Path(temp) / 'queue.json', {})['budget']['ai'], 2)

    def test_budget_resets_at_pacific_midnight_not_utc(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(w, 'STATE', Path(temp)):
            state = {'schema_version': 3, 'jobs': {}, 'budget': {'day': '2026-11-16', 'ai': 96}}
            # UTC Nov 17 07:30 is still Nov 16 at 11:30 p.m. in Los Angeles.
            with patch.object(w, 'datetime') as clock:
                clock.now.side_effect = lambda tz: datetime(2026, 11, 17, 7, 30, tzinfo=timezone.utc).astimezone(tz)
                before = w.Budget(state)
                self.assertEqual(before.counts['day'], '2026-11-16')
                self.assertEqual(before.counts['ai'], 96)
                # 08:30 UTC is 12:30 a.m. Pacific: new daily allowance.
                clock.now.side_effect = lambda tz: datetime(2026, 11, 17, 8, 30, tzinfo=timezone.utc).astimezone(tz)
                after = w.Budget(state)
                self.assertEqual(after.counts['day'], '2026-11-17')
                self.assertEqual(after.counts['reset_timezone'], 'America/Los_Angeles')
                self.assertEqual(after.counts.get('ai', 0), 0)

    def test_stalled_rotation_moves_to_next_available_category(self):
        state = {'jobs': {
            'news': {'id': 'news', 'title': 'Fresh report', 'category': 'world_news', 'status': 'QUEUED'},
            'clip': {'id': 'clip', 'title': 'Creator moment', 'category': 'clipper', 'status': 'QUEUED'}},
            'content_cycle_index': 0, 'content_batch': {'category': 'world_news', 'completed': 0,
            'story_keys': [], 'media_hashes': [], 'source_urls': []}}
        batch = w.current_batch(state, excluded={'world_news'})
        self.assertEqual(batch['category'], 'clipper')

    def test_other_topics_remain_in_rotation(self):
        self.assertIn('clipper', w.CONTENT_CYCLE)
        self.assertIn('science', w.CONTENT_CYCLE)
        self.assertIn('true_events', w.CONTENT_CYCLE)
        self.assertIn('president', w.CONTENT_CYCLE)
        self.assertEqual(w.BATCH_SIZE, 3)

    def test_migrate_legacy_utc_exhaustion_after_zero_attempts(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(w, 'STATE', Path(temp)):
            state = {'schema_version': 3, 'jobs': {}, 'budget': {
                'day': '2026-11-17', 'ai': 96, 'youtube': 18, 'pixabay': 18},
                'last_run': {'attempted': 0}}
            with patch.object(w, 'datetime') as clock:
                clock.now.side_effect = lambda tz: datetime(
                    2026, 11, 17, 9, 2, tzinfo=timezone.utc).astimezone(tz)
                fresh = w.Budget(state)
                self.assertTrue(fresh.day_reset)
                self.assertEqual(fresh.counts.get('ai', 0), 0)
                self.assertEqual(fresh.counts['day'], '2026-11-17')
                self.assertEqual(fresh.counts['reset_timezone'], 'America/Los_Angeles')
                fresh.take('ai')
                subsequent = w.Budget(state)
                self.assertFalse(subsequent.day_reset)
                self.assertEqual(subsequent.counts['ai'], 1)

    def test_legacy_nonempty_run_preserves_counts_until_reset(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(w, 'STATE', Path(temp)):
            state = {'schema_version': 3, 'jobs': {}, 'budget': {
                'day': '2026-11-17', 'ai': 6},
                'last_run': {'attempted': 2}}
            with patch.object(w, 'datetime') as clock:
                clock.now.side_effect = lambda tz: datetime(
                    2026, 11, 17, 9, 2, tzinfo=timezone.utc).astimezone(tz)
                budget = w.Budget(state)
            self.assertFalse(budget.day_reset)
            self.assertEqual(budget.counts['ai'], 6)

    def test_ai_disabled_without_free_confirmation(self):
        with patch.dict(os.environ, {'FREE_TIER_CONFIRMED': '0'}):
            with self.assertRaises(w.Hold):
                w.ai('test', None)

    def test_exact_footage_cannot_use_stock(self):
        plan, _ = fixture()
        with self.assertRaises(w.Hold):
            w.select_asset(plan['scenes'][0], {}, Path('.'), None)

    def test_changed_asset_rejected(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(w, 'ROOT', Path(temp)):
            (Path(temp) / 'asset.mp4').write_bytes(b'changed')
            entry = {'assets': {'s01': {'path': 'asset.mp4', 'rights_confirmed': True,
                                      'matches_scene': True, 'rights_note': 'Owned', 'sha256': 'wrong'}}}
            with self.assertRaises(w.Hold):
                w.select_asset({'id': 's01'}, entry, Path(temp), None)

    def test_caption_escaping_and_complete_tail(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'captions.ass'
            w.captions('First {danger} \\ then the last words remain visible.', 5.25, path)
            rows = [x for x in path.read_text().splitlines() if x.startswith('Dialogue')]
            self.assertIn('0:00:05.25', rows[-1])
            self.assertNotIn('{', '\n'.join(rows))
            self.assertNotIn('\\', '\n'.join(rows))

    def test_atomic_state_and_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'queue.json'
            w.atomic_json(path, {'jobs': []})
            self.assertEqual(w.load(path, None), {'jobs': []})
            path.write_text('{broken')
            with self.assertRaises(json.JSONDecodeError):
                w.load(path, {})


if __name__ == '__main__':
    unittest.main()
