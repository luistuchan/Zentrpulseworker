"""Regression tests for broad viral clip discovery (no API key/network required)."""
import os
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import unittest

import clipper


class Budget:
    def __init__(self):
        self.calls = 0

    def take(self, name):
        assert name == 'youtube'
        self.calls += 1
        return True


class API:
    class Hold(Exception):
        pass

    def __init__(self):
        self.searches = []

    def now(self):
        return '2026-11-17T00:00:00Z'

    def json_http(self, url, retries=0):
        params = parse_qs(urlsplit(url).query)
        if '/search' in url:
            self.searches.append(params)
            return {'items': [{'id': {'videoId': 'abcdefghijk'}},
                              {'id': {'videoId': 'lmnopqrstuv'}}]}
        if '/videos' in url:
            return {'items': [
                {'id': 'abcdefghijk', 'snippet': {'title': 'Funny streamer moment',
                   'channelTitle': 'Creator One', 'publishedAt': '2026-11-16T00:00:00Z'},
                 'status': {'license': 'youtube'}, 'statistics': {'viewCount': '2000000',
                                                                 'likeCount': '350000'}},
                {'id': 'lmnopqrstuv', 'snippet': {'title': 'Funny pets moment',
                   'channelTitle': 'Creator Two', 'publishedAt': '2026-11-15T00:00:00Z'},
                 'status': {'license': 'creativeCommon'}, 'statistics': {'viewCount': '30000',
                                                                        'likeCount': '1500'}},
            ]}
        raise AssertionError('Unexpected API request: ' + url)


class ViralDiscoveryTests(unittest.TestCase):
    def test_discovers_regular_creator_videos_without_cc_filter(self):
        api = API()
        with patch.dict(os.environ, {'YOUTUBE_API_KEY': 'test-api-key'}):
            leads = clipper.discover(Budget(), api=api)
        self.assertEqual(len(leads), 2)
        self.assertTrue(all('videoLicense' not in q for q in api.searches))
        self.assertEqual(leads[0]['source_video_id'], 'abcdefghijk')
        self.assertEqual(leads[0]['views'], 2000000)
        self.assertTrue(leads[0]['discovery_only'])
        self.assertFalse(leads[1]['discovery_only'])
        self.assertEqual(leads[0]['media']['creator'], 'Creator One')

    def test_research_does_not_imply_reuse_permission(self):
        api = API()
        with patch.dict(os.environ, {'YOUTUBE_API_KEY': 'test-api-key'}):
            leads = clipper.discover(Budget(), api=api)
        self.assertFalse(leads[0]['media']['rights_confirmed'])
        self.assertIn('Rights review', leads[0]['media']['rights_note'])


if __name__ == '__main__':
    unittest.main()
