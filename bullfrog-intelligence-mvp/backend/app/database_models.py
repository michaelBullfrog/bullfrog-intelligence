from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


JSON_DOCUMENT = JSON().with_variant(JSONB, "postgresql")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ConversationRecord(Base):
    __tablename__ = "ribbit_conversations"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )
    title: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    datasets: Mapped[list["DatasetRecord"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
    )
    reports: Mapped[list["ReportRecord"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
    )


class DatasetRecord(Base):
    __tablename__ = "ribbit_datasets"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey(
            "ribbit_conversations.id",
            ondelete="CASCADE",
        ),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    data_type: Mapped[str] = mapped_column(
        String(80),
        index=True,
        nullable=False,
    )
    source: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="ribbit",
    )
    query: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    intent: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default="",
    )
    record_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    data_json: Mapped[dict] = mapped_column(
        JSON_DOCUMENT,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        index=True,
        nullable=False,
    )

    conversation: Mapped[ConversationRecord] = relationship(
        back_populates="datasets",
    )


class ReportRecord(Base):
    __tablename__ = "ribbit_reports"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey(
            "ribbit_conversations.id",
            ondelete="CASCADE",
        ),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    report_format: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    template: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    dataset_ids: Mapped[list] = mapped_column(
        JSON_DOCUMENT,
        nullable=False,
        default=list,
    )
    download_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    download_url: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        index=True,
        nullable=False,
    )

    conversation: Mapped[ConversationRecord] = relationship(
        back_populates="reports",
    )
