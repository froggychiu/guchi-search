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
