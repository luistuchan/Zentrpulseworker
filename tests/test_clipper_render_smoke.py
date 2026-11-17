"""End-to-end local clipper render smoke test: no Gemini, credentials or network."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import clipper
import worker


class ClipperRenderSmoke(unittest.TestCase):
    def test_real_mp4_with_audio_and_captions(self):
        if not all(shutil.which(x) for x in ("ffmpeg", "ffprobe")):
            self.skipTest("ffmpeg/ffprobe unavailable")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                "-t", "6", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)
            ], check=True, timeout=90)
            words = [{"start": i * .45, "end": i * .45 + .35, "word": word}
                     for i, word in enumerate(
                         "Here is a local clip test with real audio and captions".split())]
            start, end = 0.0, 5.0
            shots = [{"start": 0.0, "end": 5.0, "centers": [],
                      "mode": "full_scene_blur", "speaker_track": None}]
            output = root / "output" / "video.mp4"
            quality = clipper.render(source, words, start, end, shots, root, output, worker)
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 10000)
            self.assertEqual(quality["dimensions"], [1080, 1920])
            self.assertTrue(quality["audio"])
            self.assertEqual(quality["decode"], "passed")


if __name__ == "__main__":
    unittest.main()
