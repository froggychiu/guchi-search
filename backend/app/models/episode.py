from datetime import datetime

from sqlalchemy import String, Text, Integer, DateTime, Float
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
    episode_id: Mapped[int] = mapped_column(Integer, index=True)
    speaker: Mapped[str | None] = mapped_column(String(100), nullable=True)
    start_time: Mapped[float] = mapped_column(Float)
    end_time: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)

    episode: Mapped["Episode"] = relationship(back_populates="segments")

    def to_search_doc(self, show: str) -> dict:
        """Convert to Meilisearch document."""
        return {
            "id": self.id,
            "episode_id": self.episode_id,
            "show": show,
            "speaker": self.speaker or "",
            "start_time": self.start_time,
            "end_time": self.end_time,
            "text": self.text,
        }
