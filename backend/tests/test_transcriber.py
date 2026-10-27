"""Tests for how audio is cut up before upload (services/transcriber.py).

Run with:  python backend/tests/test_transcriber.py

新資料夾(256) failed to transcribe on every attempt from 2025-10 to 2026-10
with a 413 from Groq. The file was exported from an Adobe tool with ~15 MB of
XMP edit history in its ID3 tag, and ffmpeg copied that tag into each
20-minute chunk, making every chunk 29 MB against a 25 MB limit. These checks
pin the two things that went wrong:

  1. A chunk carries no metadata from its source, however large.
  2. The "is this file too big to upload whole" threshold is in bytes. It used
     to be 24 MiB, which is 25.2 MB, past the limit it was guarding.

The ffmpeg checks are skipped when ffmpeg is not installed. Production has it
(see Dockerfile); the API is never called.
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("GUCHI_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.services import transcriber  # noqa: E402

PASS, FAIL = "\033[32m  PASS\033[0m", "\033[31m  FAIL\033[0m"
SKIP = "\033[33m  SKIP\033[0m"
failures = []


def check(label, got, want):
    ok = got == want
    print(f"{PASS if ok else FAIL}  {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


print("--- the upload threshold is in bytes, under the 25 MB limit ---")
check("threshold", transcriber.MAX_UPLOAD_BYTES <= 25_000_000, True)

calls = []
transcriber.get_transcription_client = lambda: (None, "whisper")
transcriber._transcribe_single = lambda c, m, path: calls.append("single") or []
transcriber._transcribe_chunked = lambda c, m, path: calls.append("chunked") or []

with tempfile.TemporaryDirectory() as tmp:
    for size, want in [(transcriber.MAX_UPLOAD_BYTES, "single"),
                       (transcriber.MAX_UPLOAD_BYTES + 1, "chunked"),
                       # 24 MiB: under the old threshold, over the real limit.
                       (24 * 1024 * 1024, "chunked")]:
        path = os.path.join(tmp, f"{size}.mp3")
        with open(path, "wb") as f:
            f.truncate(size)  # sparse: no real 25 MB written
        calls.clear()
        transcriber.transcribe_audio(path)
        check(f"{size:,} bytes goes up {want}", calls, [want])

print("\n--- a chunk does not inherit its source's tags ---")
if not shutil.which("ffmpeg"):
    print(f"{SKIP}  ffmpeg not installed")
else:
    with tempfile.TemporaryDirectory() as tmp:
        # 60 s of tone carrying a 2 MB comment, standing in for the XMP blob.
        meta = os.path.join(tmp, "meta.txt")
        with open(meta, "w") as f:
            f.write(";FFMETADATA1\ncomment=" + "x" * 2_000_000 + "\n")
        src = os.path.join(tmp, "src.mp3")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=60",
             "-i", meta, "-map_metadata", "1", "-ac", "2", "-ar", "48000",
             "-acodec", "libmp3lame", "-b:a", "192k", "-y", src],
            check=True, capture_output=True,
        )
        check("the fixture really carries the tag", os.path.getsize(src) > 2_000_000, True)

        dst = os.path.join(tmp, "chunk.mp3")
        subprocess.run(transcriber.chunk_command(src, 0, 30, dst),
                       check=True, capture_output=True)
        size = os.path.getsize(dst)
        # 30 s at 64 kbps is 240 KB; the tag alone would be 2 MB.
        check(f"chunk is audio only ({size:,} bytes)", size < 400_000, True)

        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=channels,sample_rate",
             "-of", "default=noprint_wrappers=1", dst],
            check=True, capture_output=True, text=True,
        ).stdout.split()
        check("16 kHz mono, what Whisper listens to", sorted(probe),
              ["channels=1", "sample_rate=16000"])

print("\n" + "=" * 60)
if failures:
    print(f"\033[31m{len(failures)} FAILURES: {failures}\033[0m")
    sys.exit(1)
print("\033[32mall transcriber checks passed\033[0m")
