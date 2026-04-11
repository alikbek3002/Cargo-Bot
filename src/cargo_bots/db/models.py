from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cargo_bots.db.base import Base, TimestampMixin


class ParcelStatus(str, enum.Enum):
    EMPTY = "EMPTY"
    IN_TRANSIT = "IN_TRANSIT"
    READY = "READY"
    ISSUED = "ISSUED"


class ImportStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class NotificationStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class LegacyClient(TimestampMixin, Base):
    __tablename__ = "legacy_clients"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    client_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), index=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    bound_client: Mapped["Client | None"] = relationship(back_populates="legacy_client", uselist=False)


class Client(TimestampMixin, Base):
    __tablename__ = "clients"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    client_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), index=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_legacy_bound: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    legacy_client_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("legacy_clients.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )

    legacy_client: Mapped[LegacyClient | None] = relationship(back_populates="bound_client")
    parcels: Mapped[list["Parcel"]] = relationship(back_populates="client")
    notifications: Mapped[list["NotificationOutbox"]] = relationship(back_populates="client")


class ImportJob(TimestampMixin, Base):
    __tablename__ = "imports"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    uploaded_by_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[ImportStatus] = mapped_column(Enum(ImportStatus), default=ImportStatus.PENDING, nullable=False)
    total_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    matched_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unmatched_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    new_parcels: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_parcels: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    parcels: Mapped[list["Parcel"]] = relationship(back_populates="last_import_job")
    unmatched_rows_rel: Mapped[list["UnmatchedImportRow"]] = relationship(back_populates="import_job")


class UnmatchedImportRow(TimestampMixin, Base):
    __tablename__ = "unmatched_import_rows"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    import_job_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("imports.id", ondelete="CASCADE"), index=True)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_row: Mapped[dict] = mapped_column(JSON, nullable=False)

    import_job: Mapped[ImportJob] = relationship(back_populates="unmatched_rows_rel")


class Parcel(TimestampMixin, Base):
    __tablename__ = "parcels"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    track_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    client_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    last_import_job_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("imports.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[ParcelStatus] = mapped_column(
        Enum(ParcelStatus),
        default=ParcelStatus.IN_TRANSIT,
        nullable=False,
    )
    raw_row: Mapped[dict] = mapped_column(JSON, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    client: Mapped[Client] = relationship(back_populates="parcels")
    last_import_job: Mapped[ImportJob | None] = relationship(back_populates="parcels")
    events: Mapped[list["ParcelEvent"]] = relationship(back_populates="parcel")
    notifications: Mapped[list["NotificationOutbox"]] = relationship(back_populates="parcel")


class ParcelEvent(TimestampMixin, Base):
    __tablename__ = "parcel_events"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    parcel_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("parcels.id", ondelete="CASCADE"), index=True)
    import_job_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("imports.id", ondelete="SET NULL"),
        nullable=True,
    )
    old_status: Mapped[ParcelStatus | None] = mapped_column(Enum(ParcelStatus), nullable=True)
    new_status: Mapped[ParcelStatus] = mapped_column(Enum(ParcelStatus), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    parcel: Mapped[Parcel] = relationship(back_populates="events")


class NotificationOutbox(TimestampMixin, Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_notification_outbox_dedupe_key"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    client_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    parcel_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("parcels.id", ondelete="SET NULL"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[NotificationStatus] = mapped_column(
        Enum(NotificationStatus),
        default=NotificationStatus.PENDING,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    client: Mapped[Client] = relationship(back_populates="notifications")
    parcel: Mapped[Parcel | None] = relationship(back_populates="notifications")


class SystemCounter(Base):
    __tablename__ = "system_counters"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

