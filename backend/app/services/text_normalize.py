"""Chinese script normalization for transcript text.

Whisper emits Simplified Chinese, so ingest converts it. The config used for
that conversion matters more than it looks: OpenCC's generic `s2t` produces
Traditional variants that Taiwan does not use, and those variants became the
single largest source of human corrections on the site.

Measured against 1,500 approved corrections, the two most-corrected items were
喫→吃 (60×) and 纔→才 (39×) — neither is a transcription error. They are what
`s2t` produces from 吃 and 才.

Against 16 Simplified test phrases:

    s2t     14/16 wrong    喫飯 纔剛 羣組 着急 衆人 麪條 起牀 …
    s2tw     3/16 wrong
    s2twp    1/16 wrong

`s2tw` is used for ingest rather than `s2twp` because the extra `p` also
converts vocabulary (軟件→軟體, 視頻→影片). That reads more naturally to a
Taiwanese audience but rewrites what the speaker actually said, which a
transcript should not do. Nobody among the 1,500 corrections asked for it.
"""

from opencc import OpenCC

# Simplified -> Taiwan Traditional. Used on fresh Whisper output at ingest.
_s2tw = OpenCC("s2tw")

# Traditional -> Taiwan Traditional. Used to repair text that is ALREADY
# Traditional but carries the wrong variants, i.e. everything transcribed
# before the config was fixed. Idempotent on correct text.
#
# It must never be swapped for s2tw here: s2tw treats its input as Simplified,
# and Simplification merged distinct Traditional characters, so running it over
# Traditional text rewrites correct words — 采翎 would become 採翎.
_t2tw = OpenCC("t2tw")


def to_taiwan_traditional(text: str) -> str:
    """Normalize fresh Simplified transcription output."""
    return _s2tw.convert(text or "")


def normalize_variants(text: str) -> str:
    """Repair Traditional text that uses non-Taiwan variant characters.

    Dry-run over 37,494 production segments changed 7.4% of them, entirely
    through substitutions like 爲→為 (1545), 裏→裡 (436), 喫→吃 (403),
    着→著 (192), 纔→才 (95), 衆→眾 (63), 羣→群 (47). No personal name in the
    show was affected.
    """
    return _t2tw.convert(text or "")
