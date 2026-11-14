"""Offline integration check using synthetic video/audio, never a publishable sample.
Run manually: python tests/render_smoke.py. No API calls or keys needed.
"""
from pathlib import Path
import tempfile
from unittest.mock import patch
from test_worker import w


def main():
    with tempfile.TemporaryDirectory(prefix='pulse-smoke-') as temp:
        root = Path(temp)
        folder = root / 'work'
        folder.mkdir()
        output = root / 'output'
        entry = {'id': 'offline-smoke', 'assets': {}, 'audio': {}}
        scenes = []
        for i, color in enumerate(('blue', 'green', 'red', 'orange', 'purple', 'teal'), 1):
            sid = f's{i:02}'
            asset = root / (sid + '.mp4')
            audio = root / (sid + '.wav')
            w.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                   f'color=c={color}:s=180x320:r=30:d=4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(asset)])
            w.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                   'sine=frequency=440:sample_rate=48000:duration=3.4', str(audio)])
            entry['assets'][sid] = {'path': asset.name, 'sha256': w.digest(asset), 'rights_confirmed': True,
                                    'matches_scene': True, 'rights_note': 'Synthetic offline test fixture'}
            entry['audio'][sid] = audio.name
            scenes.append({'id': sid, 'narration': 'Synthetic test captions stay visible until this scene ends.',
                           'requires_exact_footage': True})
        with patch.object(w, 'ROOT', root), patch.object(w, 'OUTPUT', output):
            result, quality, assets = w.render({'scenes': scenes}, entry, folder, None, 20)
            assert result.exists() and len(assets) == 6
            assert quality['decode'] == 'passed'
            first_digest = w.digest(result)
            # A resumed render must use completed scenes without calling narration again.
            with patch.object(w, 'narration', side_effect=AssertionError('Scene rerendered unexpectedly')):
                result2, _, _ = w.render({'scenes': scenes}, entry, folder, None, 20)
                assert w.digest(result2) == first_digest
            print('OFFLINE RENDER PASSED: six scenes, 1080x1920, audio, captions, full decode, resume.')
            print(quality)


if __name__ == '__main__':
    main()
