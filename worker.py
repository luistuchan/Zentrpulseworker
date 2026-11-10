
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlencode, quote_plus
from urllib.request import Request, urlopen
import json
import re
import subprocess
import xml.etree.ElementTree as ET

INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")
REPORT_DIR = OUTPUT_DIR / "research"

DAILY_TARGET = 9
PER_CATEGORY_LIMIT = 2

NEWS_SEARCHES = {
    "Movies & TV": (
        "movie film series ending explained hidden detail"
    ),
    "Streamers & Creators": (
        "streamer creator livestream viral moment"
    ),
    "Internet & Culture": (
        "viral internet trend unusual story"
    ),
    "Science & Technology": (
        "technology discovery science breakthrough"
    ),
    "Sports & Skills": (
        "sports record incredible performance explained"
    ),
    "Mysteries & True Events": (
        "unusual mystery strange real event"
    ),
    "Gaming": (
        "video game gaming hidden detail discovery"
    ),
    "Music & Entertainment": (
        "music artist performance pop culture news"
    ),
}

QUALITY_RULES = [
    "Find a specific WHY, HOW, or WHAT-WAS-MISSED question.",
    "Provide a surprising insight, not a visible-action recap.",
    "Verify key claims against credible independent sources.",
    "Separate confirmed facts from fan theories.",
    "Use accurate relevant visuals and original commentary.",
    "Start with a precise hook and deliver a real payoff.",
    "Reject repetition, invented details and weak explanations.",
    "Use footage you own, license, or have permission to use.",
    "Require human approval before publication.",
]


def fetch_news(category, query, limit=8):
    parameters = urlencode({
        "q": query,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    })

    url = (
        "https://news.google.com/rss/search?"
        + parameters
    )

    request = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
    )

    try:
        with urlopen(request, timeout=15) as response:
            root = ET.fromstring(response.read())
    except Exception as error:
        print(f"News unavailable for {category}: {error}")
        return []

    results = []

    for item in root.findall("./channel/item")[:limit]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()

        if not title or not link:
            continue

        results.append({
            "category": category,
            "headline": title,
            "news_link": link,
            "youtube_search": (
                "https://www.youtube.com/results?"
                "search_query=" + quote_plus(title)
            ),
            "research_status": "UNVERIFIED",
            "approval_status": "NEEDS_REVIEW",
        })

    return results


def choose_varied_candidates(pool):
    grouped = {
        name: [] for name in NEWS_SEARCHES
    }

    seen = set()

    for entry in pool:
        normalized = re.sub(
            r"[^a-z0-9]+",
            " ",
            entry["headline"].lower(),
        ).strip()

        if normalized in seen:
            continue

        seen.add(normalized)
        grouped[entry["category"]].append(entry)

    chosen = []

    for round_index in range(PER_CATEGORY_LIMIT):
        for category in NEWS_SEARCHES:
            options = grouped[category]

            if len(options) > round_index:
                chosen.append(options[round_index])

            if len(chosen) >= DAILY_TARGET:
                return chosen

    return chosen


def build_research_report():
    REPORT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    pool = []

    for category, query in NEWS_SEARCHES.items():
        pool.extend(
            fetch_news(category, query)
        )

    selected = choose_varied_candidates(pool)

    generated = datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")

    report = [
        "# ZENTR PULSE — VIRAL RESEARCH QUEUE",
        "",
        f"Generated: {generated}",
        "",
        "These are research leads, NOT verified scripts.",
        "A headline alone cannot explain a scene.",
        "",
        "## Editorial quality gate",
    ]

    report += [
        f"- {rule}" for rule in QUALITY_RULES
    ]

    for index, lead in enumerate(selected, 1):
        report += [
            "",
            (
                f"## {index}. [{lead['category']}] "
                f"{lead['headline']}"
            ),
            f"Source lead: {lead['news_link']}",
            f"YouTube research: {lead['youtube_search']}",
            (
                "Deep-dive question: What genuinely "
                "non-obvious fact or mechanism can "
                "be verified about this?"
            ),
            "Evidence: NOT YET VERIFIED.",
            "Narration: NOT YET WRITTEN.",
            "Visual rights: NOT YET CLEARED.",
            "Decision: HOLD until reviewed.",
        ]

    if not selected:
        report += [
            "",
            "No sources returned. Try again later.",
        ]

    report_path = REPORT_DIR / "viral_radar.md"

    report_path.write_text(
        "\n".join(report) + "\n",
        encoding="utf-8",
    )

    json_path = REPORT_DIR / "viral_radar.json"

    json_path.write_text(
        json.dumps(
            selected,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(
        f"Created research report with "
        f"{len(selected)} leads."
    )


def process_approved_videos():
    from shutil import which

    INPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    videos = sorted(
        INPUT_DIR.glob("*.mp4")
    )

    if not videos:
        print("No MP4 files in input/.")
        return

    if which("ffmpeg") is None:
        print("FFmpeg not installed.")
        return

    for index, video in enumerate(videos, 1):
        destination = (
            OUTPUT_DIR / f"pulse_{index}.mp4"
        )

        # Preserve the whole video over a
        # blurred vertical background.
        filter_graph = (
            "[0:v]split=2[bg][fg];"
            "[bg]scale=1080:1920:"
            "force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            "boxblur=30:10[blur];"
            "[fg]scale=1080:1920:"
            "force_original_aspect_ratio=decrease"
            "[front];"
            "[blur][front]overlay="
            "(W-w)/2:(H-h)/2,"
            "format=yuv420p[v]"
        )

        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-i", str(video),
            "-filter_complex", filter_graph,
            "-map", "[v]",
            "-map", "0:a?",
            "-t", "60",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "22",
            "-c:a", "aac",
            "-movflags", "+faststart",
            str(destination),
        ]

        try:
            subprocess.run(
                command,
                check=True,
            )

            print(f"Rendered: {destination}")

        except (
            subprocess.CalledProcessError,
            OSError,
        ) as error:
            print(
                f"Could not render "
                f"{video.name}: {error}"
            )


if __name__ == "__main__":
    build_research_report()
    process_approved_videos()

    print(
        "Review every research lead "
        "and video before publishing."
    )
