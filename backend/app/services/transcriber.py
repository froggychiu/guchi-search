import os
import math
from openai import OpenAI

from app.core.config import settings
from app.services.text_normalize import to_taiwan_traditional

# Groq supports up to 100MB via URL, but for file upload we keep 25MB limit
MAX_FILE_SIZE_MB = 24

# Known Whisper hallucination patterns (appears during silence/music).
# Each entry is either:
#   - a string: flagged if that substring appears in the segment text
#   - a tuple of strings: flagged ONLY if ALL substrings appear (AND condition)
HALLUCINATION_PATTERNS: list = [
    # YouTube-style subtitle captions
    "字幕提供",
    "字幕由",
    "字幕組",
    "字幕制作",
    "字幕製作",
    "請不吝點讚",
    "訂閱我的頻道",
    "感謝觀看",
    "感謝收看",
    "Thanks for watching",
    "Thank you for watching",
    "Subscribe",
    "Subtitles by",
    "Amara.org",
    # Music-intro hallucinations (common when audio starts with BGM/silence).
    # Strict combo — both must appear in the same segment to avoid false positives
    # on legitimate mentions of 作詞 / 作曲 / 李宗盛 in actual speech.
    ("作詞作曲", "李宗盛"),
    # English music hallucinations
    "i know you",
    "I know you",
    "I Know You",
]


def _matches_hallucination(text: str) -> bool:
    """Check if a segment text matches any hallucination pattern."""
    for pattern in HALLUCINATION_PATTERNS:
        if isinstance(pattern, tuple):
            if all(p in text for p in pattern):
                return True
        else:
            if pattern in text:
                return True
    return False


# Short-filler segments that are only hallucinations when they are the WHOLE segment
# (substring match would over-flag legitimate speech)
EXACT_MATCH_HALLUCINATIONS = {"嗯", "啊", "喔", "呃", "欸"}


def detect_hallucinations(segments: list[dict], check_minutes: float = 5.0) -> list[int]:
    """
    Detect likely hallucinated segments in the first N minutes.
    Returns list of segment indices that are suspicious.
    """
    suspicious = []
    threshold_seconds = check_minutes * 60
    # Exact-match fillers (嗯/啊/etc) only get flagged within the first 2 minutes
    # AND only if at least one other hallucination already fired for this track
    # (avoids flagging legitimate filler words at the start of real speech)
    early_threshold = 120  # 2 minutes

    for i, seg in enumerate(segments):
        if seg["start_time"] > threshold_seconds:
            break

        text = seg["text"].strip()
        if _matches_hallucination(text):
            suspicious.append(i)

    # Second pass: flag exact-match fillers in the first 2 minutes,
    # but ONLY if surrounded by already-flagged hallucinations (reduces false positives)
    if suspicious:
        for i, seg in enumerate(segments):
            if seg["start_time"] > early_threshold:
                break
            if i in suspicious:
                continue
            text = seg["text"].strip()
            if text in EXACT_MATCH_HALLUCINATIONS:
                suspicious.append(i)

    return sorted(suspicious)


# Whisper prompt — primes the model with proper-name spellings so it's
# less likely to mishear them. Limit ~224 tokens (≈200 zh chars).
# Written as continuous text (not a list) — Whisper biases better that way.
TRANSCRIPTION_PROMPT = (
    "這是呱吉 Podcast 的逐字稿，節目包含「新資料夾」「呱吉電台」等系列。"
    "新資料夾的主持人是呱吉與采翎，采翎的男友叫東燁，"
    "辦公室主任和呱吉的助理是佳佳，呱吉的寫手是祐先。"
    "節目中常常出現呱吉、采翎、東燁、佳佳、祐先這幾個名字。"
)


def get_transcription_client() -> tuple[OpenAI, str]:
    """
    Return (client, model_name).
    Prefer Groq if API key is set; fall back to OpenAI.
    """
    if settings.groq_api_key:
        client = OpenAI(
            api_key=settings.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
        )
        return client, "whisper-large-v3"
    elif settings.openai_api_key:
        client = OpenAI(api_key=settings.openai_api_key)
        return client, "whisper-1"
    else:
        raise RuntimeError("No transcription API key configured (set GUCHI_GROQ_API_KEY or GUCHI_OPENAI_API_KEY)")


def transcribe_audio(file_path: str) -> list[dict]:
    """
    Transcribe an audio file using Groq or OpenAI Whisper API.
    Returns a list of segments with start_time, end_time, and text.
    """
    client, model = get_transcription_client()
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

    if file_size_mb > MAX_FILE_SIZE_MB:
        return _transcribe_chunked(client, model, file_path)

    return _transcribe_single(client, model, file_path)


def _transcribe_single(client: OpenAI, model: str, file_path: str) -> list[dict]:
    """Transcribe a single audio file."""
    with open(file_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model=model,
            file=f,
            language="zh",
            response_format="verbose_json",
            timestamp_granularities=["segment"],
            prompt=TRANSCRIPTION_PROMPT,
        )

    segments = []
    if hasattr(response, "segments") and response.segments:
        for seg in response.segments:
            segments.append({
                "start_time": getattr(seg, "start", 0),
                "end_time": getattr(seg, "end", 0),
                "text": to_taiwan_traditional(getattr(seg, "text", "").strip()),
            })
    else:
        # Fallback: treat the whole transcription as one segment
        segments.append({
            "start_time": 0.0,
            "end_time": 0.0,
            "text": to_taiwan_traditional(response.text.strip()),
        })

    return segments


def _transcribe_chunked(client: OpenAI, model: str, file_path: str) -> list[dict]:
    """
    Split large audio files and transcribe each chunk.
    Requires ffmpeg to be available on the system.
    """
    import subprocess
    import tempfile

    # Get duration using ffprobe
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", file_path],
        capture_output=True, text=True
    )
    total_duration = float(result.stdout.strip())

    # Split into ~20 minute chunks (within 25MB for most podcasts)
    chunk_duration = 1200  # 20 minutes
    num_chunks = math.ceil(total_duration / chunk_duration)

    all_segments = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(num_chunks):
            start = i * chunk_duration
            chunk_path = os.path.join(tmpdir, f"chunk_{i}.mp3")

            subprocess.run([
                "ffmpeg", "-i", file_path,
                "-ss", str(start), "-t", str(chunk_duration),
                "-acodec", "libmp3lame", "-q:a", "5",
                "-y", chunk_path
            ], capture_output=True)

            chunk_segments = _transcribe_single(client, model, chunk_path)

            # Offset timestamps by the chunk's start time
            for seg in chunk_segments:
                seg["start_time"] += start
                seg["end_time"] += start
                all_segments.append(seg)

    return all_segments
