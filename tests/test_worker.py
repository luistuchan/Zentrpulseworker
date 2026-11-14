import copy
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
