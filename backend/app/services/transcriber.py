import os
import math
from openai import OpenAI

from app.core.config import settings

# Whisper API has a 25MB file size limit, so we may need to split audio
MAX_FILE_SIZE_MB = 24


def get_openai_client() -> OpenAI:
    return OpenAI(api_key=settings.openai_api_key)


def transcribe_audio(file_path: str) -> list[dict]:
    """
    Transcribe an audio file using OpenAI Whisper API.
    Returns a list of segments with start_time, end_time, and text.
    """
    client = get_openai_client()
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

    if file_size_mb > MAX_FILE_SIZE_MB:
        return _transcribe_chunked(client, file_path)

    return _transcribe_single(client, file_path)


def _transcribe_single(client: OpenAI, file_path: str) -> list[dict]:
    """Transcribe a single audio file."""
    with open(file_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language="zh",
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )

    segments = []
    if hasattr(response, "segments") and response.segments:
        for seg in response.segments:
            segments.append({
                "start_time": seg.get("start", seg.get("start_time", 0)),
                "end_time": seg.get("end", seg.get("end_time", 0)),
                "text": seg.get("text", "").strip(),
            })
    else:
        # Fallback: treat the whole transcription as one segment
        segments.append({
            "start_time": 0.0,
            "end_time": 0.0,
            "text": response.text.strip(),
        })

    return segments


def _transcribe_chunked(client: OpenAI, file_path: str) -> list[dict]:
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

            chunk_segments = _transcribe_single(client, chunk_path)

            # Offset timestamps by the chunk's start time
            for seg in chunk_segments:
                seg["start_time"] += start
                seg["end_time"] += start
                all_segments.append(seg)

    return all_segments
