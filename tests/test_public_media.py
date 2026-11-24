import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import public_media as p


class API:
    class Hold(Exception):
        pass
    class Wait(Hold):
        pass
    def load(self, path, default):
        return default
    def atomic_json(self, path, value):
        pass


class PublicMediaTests(unittest.TestCase):
    def test_license_filters(self):
        self.assertTrue(p.permitted_license('https://creativecommons.org/licenses/by/4.0/'))
        self.assertFalse(p.permitted_license('https://creativecommons.org/licenses/by-nc/4.0/'))
        self.assertFalse(p.permitted_license('https://creativecommons.org/licenses/by-nd/4.0/'))

    def test_provider_fallback(self):
        api = API()
        with tempfile.TemporaryDirectory() as tmp:
            api.WORK = Path(tmp)
            with patch.object(p, 'archive', side_effect=api.Wait('unavailable')) as archive, \
                 patch.object(p, 'nasa', return_value=[{'source_url':'https://images-assets.nasa.gov/test.mp4'}]) as nasa, \
                 patch.object(p, 'gov_library', return_value=[]) as gov, \
                 patch.object(p, 'commons', return_value=[]) as commons:
                for mock, name in ((archive,'archive'),(nasa,'nasa'),(gov,'gov_library'),(commons,'commons')):
                    mock.__name__ = name
                self.assertEqual(len(list(p.discover('moon landing', api))), 1)
                nasa.assert_called_once()
                commons.assert_not_called()

    def test_publisher_video_and_social_links(self):
        links = p.media_links('https://example.org/article',
            '<video src="/clip.mp4"></video><a href="https://www.tiktok.com/@user/video/123">clip</a>'
            '<a href="https://www.instagram.com/user/">profile</a>')
        self.assertEqual(len(links),2)
        self.assertEqual(links[0]['source_url'],'https://example.org/clip.mp4')
        self.assertEqual(links[1]['provider'],'Social media')

    def test_archive_rights_and_download_metadata(self):
        api = API()
        responses = [
            {'response':{'docs':[{'identifier':'moon-film','title':'Moon landing'}]}},
            {'metadata':{'title':'Moon landing','licenseurl':'https://creativecommons.org/licenses/by/4.0/'},
             'files':[{'name':'film.mp4','size':'2000'}]}
        ]
        with patch.object(api,'json_http',side_effect=responses,create=True):
            result = list(p.archive('moon landing',api))
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['source_url'],'https://archive.org/download/moon-film/film.mp4')
