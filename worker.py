
from pathlib import Path
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, quote_plus
from urllib.request import Request, urlopen
from shutil import which
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET

INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")
REPORT_DIR = OUTPUT_DIR / "research"

DAILY_TARGET = 9
MAX_PER_CATEGORY = 3
MAX_AGE_DAYS = 14

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ENABLE_AI = os.getenv("ENABLE_AI", "0") == "1"
AI_MODEL = "gemini-2.5-flash"

SEARCHES = {
    "Movies & TV": [
        "movie ending explained hidden clues twist",
        "series scene explained foreshadowing mystery"
    ],
    "Streamers & Creators": [
        "streamer viral moment explained livestream",
        "creator unexpected livestream incident"
    ],
    "Internet & Culture": [
        "viral internet mystery explained",
        "internet viral video unusual discovery"
    ],
    "Science & Technology": [
        "surprising technology discovery demonstration",
        "science experiment unexpected result explained"
    ],
    "Sports & Skills": [
        "sports viral play unusual rule explained",
        "athlete unbelievable skill record breakdown"
    ],
    "Mysteries & True Events": [
        "bizarre true story unexpected explanation",
        "real mystery unusual discovery investigation"
    ],
    "Gaming": [
        "video game secret discovery hidden mechanic",
        "game speedrun exploit explained discovery"
    ],
    "Music & Entertainment": [
        "concert viral performance surprising moment",
        "music video hidden meaning detail explained"
    ]
}

STRONG_SIGNALS = (
    "explained", "hidden", "secret", "twist",
    "mystery", "unexpected", "revealed",
    "discovered", "why", "how", "behind",
    "viral", "caught", "record", "clue",
    "ending", "breakdown", "rare"
)

REJECT_PHRASES = (
    "things to do", "events calendar",
    "tea party", "anniversary party",
    "tickets on sale", "shopping guide",
    "coupon", "horoscope", "job openings",
    "local events", "recipe"
)

ANGLES = {
    "Movies & TV":
        "Which hidden clue changes the meaning "
        "of the scene or character decision?",
    "Streamers & Creators":
        "What actually caused this moment and "
        "what context did viewers miss?",
    "Internet & Culture":
        "Where did this originate and what "
        "surprising fact explains its popularity?",
    "Science & Technology":
        "What mechanism produced the result "
        "and what common explanation is wrong?",
    "Sports & Skills":
        "What technique or unusual rule "
        "made the moment possible?",
    "Mysteries & True Events":
        "What verified clue explains the event "
        "and what remains unknown?",
    "Gaming":
        "What hidden mechanic explains this "
        "and how can it be demonstrated?",
    "Music & Entertainment":
        "What documented detail makes this "
        "performance or story surprising?"
}


def fetch_json(url, headers=None, payload=None):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    request = Request(
        url,
        data=data,
        headers=headers or {
            "User-Agent": "ZENTR-Pulse/2"
        }
    )

    with urlopen(request, timeout=25) as response:
        return json.loads(response.read())


def fetch_news(category, query):
    params = urlencode({
        "q": query + " when:14d",
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en"
    })

    url = (
        "https://news.google.com/rss/search?"
        + params
    )

    try:
        request = Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"}
        )

        with urlopen(request, timeout=18) as response:
            root = ET.fromstring(response.read())

    except Exception as error:
        print("News error:", error)
        return []

    results = []

    for item in root.findall("./channel/item")[:12]:
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        published = item.findtext("pubDate") or ""

        if not title or not link:
            continue

        date = None

        try:
            stamp = parsedate_to_datetime(
                published
            ).astimezone(timezone.utc)

            if stamp < (
                datetime.now(timezone.utc)
                - timedelta(days=MAX_AGE_DAYS)
            ):
                continue

            date = stamp.isoformat()

        except (ValueError, TypeError, OverflowError):
            pass

        results.append({
            "category": category,
            "headline": title.strip(),
            "source_link": link.strip(),
            "published": date
        })

    return results


def score_topic(lead):
    title = lead["headline"].lower()

    if any(x in title for x in REJECT_PHRASES):
        return -100

    score = 0

    for signal in STRONG_SIGNALS:
        if signal in title:
            score += 3

    if "?" in title:
        score += 1

    if lead["published"]:
        age = (
            datetime.now(timezone.utc)
            - datetime.fromisoformat(
                lead["published"]
            )
        )

        if age <= timedelta(days=2):
            score += 2
        elif age <= timedelta(days=7):
            score += 1

    return score


def youtube_check(title):
    if not YOUTUBE_API_KEY:
        return {"status": "NOT CONNECTED"}

    query = title.rsplit(" - ", 1)[0][:110]

    params = urlencode({
        "part": "snippet",
        "type": "video",
        "maxResults": 5,
        "q": query,
        "key": YOUTUBE_API_KEY,
        "publishedAfter": (
            datetime.now(timezone.utc)
            - timedelta(days=30)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    })

    try:
        data = fetch_json(
            "https://www.googleapis.com/youtube/v3/search?"
            + params
        )

        ids = [
            item.get("id", {}).get("videoId")
            for item in data.get("items", [])
        ]

        ids = [x for x in ids if x]

        if not ids:
            return {"status": "NO MATCHES"}

        details = fetch_json(
            "https://www.googleapis.com/youtube/v3/videos?"
            + urlencode({
                "part": "statistics",
                "id": ",".join(ids),
                "key": YOUTUBE_API_KEY
            })
        )

        views = [
            int(item.get("statistics", {}).get(
                "viewCount", 0
            ))
            for item in details.get("items", [])
        ]

        return {
            "status": "OK",
            "highest_related_views": max(
                views, default=0
            )
        }

    except Exception as error:
        return {
            "status": "ERROR",
            "message": str(error)[:120]
        }


def ai_research(lead):
    if not ENABLE_AI or not GEMINI_API_KEY:
        return None

    prompt = f"""
You are the ZENTR Pulse research editor.

CATEGORY: {lead['category']}
TOPIC: {lead['headline']}
NEWS SOURCE: {lead['source_link']}

Find a genuinely surprising explanation.

Do not simply describe what happens.
Do not summarize obvious information.
Do not manufacture hidden details.
Do not invent movie scenes or character motives.

Find the deeper WHY or HOW.

Use grounded research when available.
Separate verified facts from theories.
Include checkable sources for major claims.

If there is no interesting verified explanation,
respond REJECT.

If there is enough evidence, provide:

1. Specific hook
2. Surprising explanation
3. Supporting facts
4. 45–60 second narration draft
5. Suggested relevant visuals
6. Sources
7. Uncertainties or verification gaps

The script is a DRAFT for human review.
Do not assert that copyrighted footage can be reused.
"""

    url = (
        "https://generativelanguage.googleapis.com/"
        "v1beta/models/"
        + AI_MODEL
        + ":generateContent"
    )

    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "tools": [{
            "google_search": {}
        }],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 1400
        }
    }

    try:
        response = fetch_json(
            url,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": GEMINI_API_KEY
            },
            payload=payload
        )

        candidates = response.get(
            "candidates", []
        )

        if not candidates:
            return "AI returned no draft."

        parts = candidates[0].get(
            "content", {}
        ).get("parts", [])

        return "\n".join(
            part.get("text", "")
            for part in parts
        )

    except Exception as error:
        return f"AI research unavailable: {error}"


def collect_topics():
    pool = {}
    for category, searches in SEARCHES.items():
        for search in searches:
            for lead in fetch_news(category, search):
                key = re.sub(
                    r"[^a-z0-9]+",
                    " ",
                    lead["headline"].lower()
                ).strip()

                if key in pool:
                    continue

                score = score_topic(lead)

                if score < 3:
                    continue

                lead["score"] = score
                lead["research_question"] = ANGLES[
                    category
                ]

                pool[key] = lead

    ranked = sorted(
        pool.values(),
        key=lambda x: x["score"],
        reverse=True
    )

    counts = {
        category: 0
        for category in SEARCHES
    }

    selected = []

    for lead in ranked:
        category = lead["category"]

        if counts[category] >= MAX_PER_CATEGORY:
            continue

        selected.append(lead)
        counts[category] += 1

        if len(selected) >= DAILY_TARGET:
            break

    return selected


def write_radar():
    REPORT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    topics = collect_topics()

    lines = [
        "# ZENTR PULSE VIRAL RADAR",
        "",
        "Generated: "
        + datetime.now(timezone.utc).isoformat(),
        "",
        f"Research candidates: {len(topics)}",
        "",
        "## Editorial requirements",
        "",
        "- Every video must reveal something non-obvious.",
        "- No generic AI summaries.",
        "- No fake or unsupported explanations.",
        "- Verify sources before approving scripts.",
        "- Keep different content categories.",
        "- No automatic publishing.",
        "- Only use footage with appropriate rights.",
        "",
        "## Research Queue"
    ]

    for number, lead in enumerate(topics, 1):
        title = lead["headline"]

        lead["youtube_signal"] = (
            youtube_check(title)
            if number <= 9
            else {"status": "NOT CHECKED"}
        )

        lead["ai_draft"] = (
            ai_research(lead)
            if number <= 3
            else None
        )

        youtube_url = (
            "https://www.youtube.com/results?"
            "search_query="
            + quote_plus(title)
        )

        lines.extend([
            "",
            f"## {number}. {title}",
            "",
            f"Category: {lead['category']}",
            f"Relevance score: {lead['score']}",
            f"News source: {lead['source_link']}",
            f"YouTube search: {youtube_url}",
            (
                "YouTube check: "
                + json.dumps(
                    lead["youtube_signal"]
                )
            ),
            (
                "Research angle: "
                + lead["research_question"]
            ),
            "",
            "Status: UNVERIFIED — REVIEW REQUIRED"
        ])

        if lead["ai_draft"]:
            lines.extend([
                "",
                "### AI research draft",
                "",
                lead["ai_draft"]
            ])

    if not topics:
        lines.append(
            "No strong topics found. "
            "Do not fill the quota with weak stories."
        )

    (REPORT_DIR / "viral_radar.md").write_text(
        "\n".join(lines),
        encoding="utf-8"
    )

    (REPORT_DIR / "viral_radar.json").write_text(
        json.dumps(topics, indent=2),
        encoding="utf-8"
    )

    print(
        f"ZENTR Radar created: {len(topics)} topics"
    )


def render_videos():
    INPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)

    videos = sorted(INPUT_DIR.glob("*.mp4"))

    if not videos:
        print("No input videos to render.")
        return

    if which("ffmpeg") is None:
        print("FFmpeg unavailable.")
        return

    video_filter = (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:"
        "force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "boxblur=30:10[blur];"
        "[fg]scale=1080:1920:"
        "force_original_aspect_ratio=decrease[front];"
        "[blur][front]overlay="
        "(W-w)/2:(H-h)/2,"
        "format=yuv420p[v]"
    )

    for index, video in enumerate(videos, 1):
        output = OUTPUT_DIR / (
            f"pulse_{index}.mp4"
        )

        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-i", str(video),
            "-filter_complex", video_filter,
            "-map", "[v]",
            "-map", "0:a?",
            "-t", "60",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "22",
            "-c:a", "aac",
            "-movflags", "+faststart",
            str(output)
        ]

        try:
            subprocess.run(
                command,
                check=True
            )
            print("Rendered:", output)

        except Exception as error:
            print("Render failed:", error)


if __name__ == "__main__":
    write_radar()
    render_videos()
    print("ZENTR Pulse cycle completed.")
