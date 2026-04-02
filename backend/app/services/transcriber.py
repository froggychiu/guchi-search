import os
import math
from openai import OpenAI

from app.core.config import settings

# Groq supports up to 100MB via URL, but for file upload we keep 25MB limit
MAX_FILE_SIZE_MB = 24


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
        )

    segments = []
    if hasattr(response, "segments") and response.segments:
        for seg in response.segments:
            segments.append({
                "start_time": getattr(seg, "start", 0),
                "end_time": getattr(seg, "end", 0),
                "text": getattr(seg, "text", "").strip(),
            })
    else:
        # Fallback: treat the whole transcription as one segment
        segments.append({
            "start_time": 0.0,
            "end_time": 0.0,
            "text": response.text.strip(),
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
