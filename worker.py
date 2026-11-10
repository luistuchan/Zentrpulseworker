
from pathlib import Path
import subprocess

INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")

def process_videos():
    INPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)

    videos = list(INPUT_DIR.glob("*.mp4"))

    for index, video in enumerate(videos, start=1):
        destination = OUTPUT_DIR / f"pulse_{index}.mp4"

        command = [
            "ffmpeg", "-y",
            "-i", str(video),
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920",
            "-t", "30",
            "-c:v", "libx264",
            "-c:a", "aac",
            str(destination)
        ]

        subprocess.run(command, check=True)

if __name__ == "__main__":
    process_videos()
