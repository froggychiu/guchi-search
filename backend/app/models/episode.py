from datetime import datetime

from sqlalchemy import String, Text, Integer, DateTime, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Episode(Base):
    __tablename__ = "episodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    show: Mapped[str] = mapped_column(String(100), index=True)  # "新資料夾" or "直播"
    audio_url: Mapped[str] = mapped_column(String(1000))
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transcription_status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending, processing, done, error

    segments: Mapped[list["Segment"]] = relationship(back_populates="episode", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Episode {self.id}: {self.title}>"


class Segment(Base):
    __tablename__ = "segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(Integer, ForeignKey("episodes.id"), index=True)
    speaker: Mapped[str | None] = mapped_column(String(100), nullable=True)
    start_time: Mapped[float] = mapped_column(Float)
    end_time: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)

    episode: Mapped["Episode"] = relationship(back_populates="segments")

    corrections: Mapped[list["Correction"]] = relationship(back_populates="segment")

    def to_search_doc(self, show: str, episode_title: str = "") -> dict:
        """Convert to Meilisearch document."""
        return {
            "id": self.id,
            "episode_id": self.episode_id,
            "episode_title": episode_title,
            "show": show,
            "speaker": self.speaker or "",
            "start_time": self.start_time,
            "end_time": self.end_time,
            "text": self.text,
        }


class SearchLog(Base):
    """Records every /api/search call for popular-keyword analytics.

    No user identifier stored — only the query text and timestamp.
    Used to compute trending keywords over recent time windows.
    """
    __tablename__ = "search_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    query: Mapped[str] = mapped_column(String(200), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class VocabRule(Base):
    """A recurring transcription mistake and its fix, applied at ingest.

    Whisper mishears the show's proper nouns the same way over and over —
    彩玲 for 采翎, 瓜子 for 呱吉, 全智隆 for 權志龍, 貝巴尼 for Bad Bunny.
    These cannot be solved with a better prompt: Whisper's prompt is capped at
    224 tokens (~200 Chinese characters) and the existing one already spends
    most of that on five names. So the glossary lives here and is applied
    after transcription instead.

    Rules are NOT auto-applied on creation. `wrong_text` is often a real word
    in its own right — the corpus has 519 segments containing 瓜子 (melon
    seeds) and 126 containing 里昂 (Lyon), so blanket replacement would
    corrupt legitimate text. An admin reviews each rule with its corpus
    occurrence count in front of them and decides. This is the same judgement
    recorded in HANDOFF 10.2, where 又先/右先/有先 → 祐先 was rejected for
    being too destructive.
    """
    __tablename__ = "vocab_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    wrong_text: Mapped[str] = mapped_column(String(100), index=True)
    right_text: Mapped[str] = mapped_column(String(100))
    # pending -> an admin has not judged it yet; only "active" rules are applied
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    submitter_name: Mapped[str] = mapped_column(String(100), default="匿名")
    # Segments rewritten the last time this rule ran, for spotting a rule that
    # is matching far more than its author expected.
    applied_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Correction(Base):
    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(Integer, ForeignKey("segments.id"), index=True)
    original_text: Mapped[str] = mapped_column(Text)
    suggested_text: Mapped[str] = mapped_column(Text)
    submitter_name: Mapped[str] = mapped_column(String(100), default="匿名")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, approved, rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    segment: Mapped["Segment"] = relationship(back_populates="corrections")
