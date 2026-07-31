"""Unit tests for snippet highlighting and cropping.

Run with:  python backend/tests/test_highlight.py

_highlight is the whole of SEC-01 now: it escapes the segment and emits the
only markup the frontend will ever see. A community correction can put
arbitrary text into a segment, so the escaping matters more than the marking.
"""

import os
import sys

os.environ["GUCHI_DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.api.search import _highlight, _crop

P, F = "\033[32m  PASS\033[0m", "\033[31m  FAIL\033[0m"
fails = []
def ck(label, got, want):
    ok = got == want
    print(f"{P if ok else F}  {label}\n        got : {got!r}")
    if not ok:
        print(f"        want: {want!r}")
        fails.append(label)

# SEC-01: a stored XSS payload approved through the correction flow.
ck("script tag is inert",
   _highlight('<img src=x onerror="alert(1)">呱吉', ["呱吉"]),
   '&lt;img src=x onerror="alert(1)"&gt;<mark>呱吉</mark>')

ck("angle brackets in the MATCH are escaped too",
   _highlight('say <b>hi</b> now', ["<b>"]),
   'say <mark>&lt;b&gt;</mark>hi&lt;/b&gt; now')

ck("a literal <mark> in the text cannot forge a highlight",
   _highlight('<mark>fake</mark> 呱吉', ["呱吉"]),
   '&lt;mark&gt;fake&lt;/mark&gt; <mark>呱吉</mark>')

ck("ampersands escaped once, not twice",
   _highlight('A & B 呱吉', ["呱吉"]),
   'A &amp; B <mark>呱吉</mark>')

# Correctness
ck("every occurrence marked",
   _highlight('貓 狗 貓', ["貓"]),
   '<mark>貓</mark> 狗 <mark>貓</mark>')

ck("case-insensitive, original casing preserved",
   _highlight('OpenAI and openai', ["openai"]),
   '<mark>OpenAI</mark> and <mark>openai</mark>')

ck("multiple terms, longest wins at same position",
   _highlight('電腦椅很貴', ["電腦", "電腦椅"]),
   '<mark>電腦椅</mark>很貴')

ck("no terms -> plain escape",
   _highlight('<b>x</b>', []),
   '&lt;b&gt;x&lt;/b&gt;')

ck("empty text is safe", _highlight('', ["x"]), '')

# No unbalanced or nested tags can ever be emitted
out = _highlight('呱吉呱吉呱吉', ["呱吉"])
ck("tags balanced", out.count("<mark>"), out.count("</mark>"))

# Crop keeps the match visible
long = "前" * 200 + "關鍵字" + "後" * 200
cropped = _crop(long, ["關鍵字"])
ck("crop keeps the match inside the window", "關鍵字" in cropped, True)
ck("crop respects the limit", len(cropped) <= 142, True)
ck("short text untouched", _crop("短句", ["短"]), "短句")

print("\n" + "="*56)
print(f"\033[31m{len(fails)} FAILURES\033[0m" if fails else "\033[32mall highlight checks passed\033[0m")
sys.exit(1 if fails else 0)
