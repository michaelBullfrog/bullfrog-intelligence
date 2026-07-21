from __future__ import annotations

from datetime import date, datetime, timezone
import uuid

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
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


class CcwrRenewalRecord(Base):
    __tablename__ = "ccwr_renewals"
    __table_args__ = (
        UniqueConstraint(
            "market",
            "subscription_id",
            name="uq_ccwr_renewal_market_subscription",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    subscription_id: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    subscription_status: Mapped[str] = mapped_column(String(80), index=True, default="")
    renewal_date: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    dashboard_renewal_date: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    renewal_bucket: Mapped[str] = mapped_column(String(80), index=True, default="")
    renewal_window: Mapped[str] = mapped_column(String(80), default="")
    renewal_risk: Mapped[str] = mapped_column(String(40), index=True, default="")
    days_until_renewal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_customer_name: Mapped[str] = mapped_column(String(255), index=True, default="")
    end_customer_id: Mapped[str] = mapped_column(String(160), default="")
    reseller_name: Mapped[str] = mapped_column(String(255), index=True, default="")
    reseller_id: Mapped[str] = mapped_column(String(160), default="")
    bill_to_name: Mapped[str] = mapped_column(String(255), index=True, default="")
    bill_to_id: Mapped[str] = mapped_column(String(160), default="")
    has_auto_renewal: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    provisioning_status: Mapped[str] = mapped_column(String(100), default="")
    billing_model: Mapped[str] = mapped_column(String(100), default="")
    last_refreshed: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    sync_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON_DOCUMENT, nullable=False, default=dict)


class CcwrSyncRun(Base):
    __tablename__ = "ccwr_sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sync_id: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_records: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    us_records: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    canada_records: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
