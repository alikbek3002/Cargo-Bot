from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from cargo_bots.db.models import (
    Client,
    ImportJob,
    ImportStatus,
    NotificationOutbox,
    NotificationStatus,
    Parcel,
    ParcelEvent,
    ParcelStatus,
    UnmatchedImportRow,
)
from cargo_bots.db.session import Database
from cargo_bots.services.excel_parser import FailedImportRow, ParsedImportRow, SupplierWorkbookParser
from cargo_bots.services.storage import StorageBackend

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AdminStats:
    clients: int
    parcels: int
    imports: int
    unmatched_rows: int


class ImportService:
    def __init__(
        self,
        database: Database,
        storage: StorageBackend,
        parser: SupplierWorkbookParser,
        storage_prefix: str = "imports",
    ) -> None:
        self.database = database
        self.storage = storage
        self.parser = parser
        self.storage_prefix = storage_prefix.strip("/") or "imports"

    async def create_import_job(
        self,
        *,
        uploaded_by_telegram_id: int,
        filename: str,
        payload: bytes,
    ) -> ImportJob:
        checksum = hashlib.sha256(payload).hexdigest()
        import_job = ImportJob(
            uploaded_by_telegram_id=uploaded_by_telegram_id,
            filename=filename,
            checksum=checksum,
            storage_key=self._storage_key(filename, checksum),
            status=ImportStatus.PENDING,
        )
        await self.storage.save_bytes(import_job.storage_key, payload)

        async with self.database.session() as session:
            async with session.begin():
                session.add(import_job)
                await session.flush()
                return import_job

    async def list_recent_imports(self, limit: int = 5) -> list[ImportJob]:
        async with self.database.session() as session:
            result = await session.scalars(
                select(ImportJob).order_by(ImportJob.created_at.desc()).limit(limit)
            )
            return list(result.all())

    async def list_recent_unmatched_rows(self, limit: int = 10) -> list[UnmatchedImportRow]:
        async with self.database.session() as session:
            result = await session.scalars(
                select(UnmatchedImportRow)
                .order_by(UnmatchedImportRow.created_at.desc())
                .limit(limit)
            )
            return list(result.all())

    async def get_admin_stats(self) -> AdminStats:
        async with self.database.session() as session:
            clients = await session.scalar(select(func.count()).select_from(Client)) or 0
            parcels = await session.scalar(select(func.count()).select_from(Parcel)) or 0
            imports = await session.scalar(select(func.count()).select_from(ImportJob)) or 0
            unmatched_rows = await session.scalar(select(func.count()).select_from(UnmatchedImportRow)) or 0
            return AdminStats(
                clients=int(clients),
                parcels=int(parcels),
                imports=int(imports),
                unmatched_rows=int(unmatched_rows),
            )

    async def process_import_job(self, import_job_id: UUID) -> ImportJob:
        async with self.database.session() as session:
            job = await session.get(ImportJob, import_job_id)
            if not job:
                raise ValueError(f"Import job {import_job_id} was not found.")

            job.status = ImportStatus.PROCESSING
            job.error_message = None
            await session.commit()

        try:
            async with self.database.session() as session:
                job = await session.get(ImportJob, import_job_id)
                if not job:
                    raise ValueError(f"Import job {import_job_id} was not found.")

                payload = await self.storage.fetch_bytes(job.storage_key)
                parse_result = self.parser.parse_bytes(payload)
                await self._materialize_result(session, job, parse_result.parsed_rows, parse_result.failed_rows, parse_result.total_rows)
                await session.commit()
                return job
        except Exception as exc:  # pragma: no cover - defensive path
            logger.exception("Import job %s failed", import_job_id)
            async with self.database.session() as session:
                job = await session.get(ImportJob, import_job_id)
                if job:
                    job.status = ImportStatus.FAILED
                    job.error_message = str(exc)
                    job.completed_at = datetime.now(tz=UTC)
                    await session.commit()
            raise

    async def _materialize_result(
        self,
        session,
        job: ImportJob,
        parsed_rows: list[ParsedImportRow],
        failed_rows: list[FailedImportRow],
        total_rows: int,
    ) -> None:
        client_codes = sorted({row.client_code for row in parsed_rows})
        track_codes = sorted({row.track_code for row in parsed_rows})
        notification_keys = [self._notification_dedupe_key(track_code) for track_code in track_codes]

        clients = {
            client.client_code: client
            for client in (
                await session.scalars(select(Client).where(Client.client_code.in_(client_codes)))
            ).all()
        }
        parcels = {
            parcel.track_code: parcel
            for parcel in (
                await session.scalars(select(Parcel).where(Parcel.track_code.in_(track_codes)))
            ).all()
        }
        existing_notifications = set(
            (
                await session.scalars(
                    select(NotificationOutbox.dedupe_key).where(
                        NotificationOutbox.dedupe_key.in_(notification_keys)
                    )
                )
            ).all()
        )

        unresolved_rows: list[FailedImportRow] = list(failed_rows)
        new_parcels = 0
        updated_parcels = 0
        matched_rows = 0
        unmatched_rows = 0

        for row in parsed_rows:
            client = clients.get(row.client_code)
            if not client:
                unresolved_rows.append(
                    FailedImportRow(
                        row_number=row.row_number,
                        reason=f"Client {row.client_code} is not registered",
                        raw_row=row.raw_row,
                    )
                )
                continue

            parcel = parcels.get(row.track_code)
            old_status = parcel.status if parcel else None
            is_new = parcel is None

            if parcel is None:
                parcel = Parcel(
                    track_code=row.track_code,
                    client=client,
                    last_import_job=job,
                    status=ParcelStatus.IN_TRANSIT,
                    raw_row=row.raw_row,
                    last_seen_at=datetime.now(tz=UTC),
                )
                session.add(parcel)
                parcels[row.track_code] = parcel
                new_parcels += 1
            else:
                changed = (
                    parcel.client_id != client.id
                    or parcel.status != ParcelStatus.IN_TRANSIT
                    or parcel.raw_row != row.raw_row
                )
                parcel.client = client
                parcel.last_import_job = job
                parcel.raw_row = row.raw_row
                parcel.last_seen_at = datetime.now(tz=UTC)
                if parcel.status != ParcelStatus.IN_TRANSIT:
                    parcel.status = ParcelStatus.IN_TRANSIT
                if changed:
                    updated_parcels += 1

            if is_new or old_status != ParcelStatus.IN_TRANSIT:
                session.add(
                    ParcelEvent(
                        parcel=parcel,
                        import_job_id=job.id,
                        old_status=old_status,
                        new_status=ParcelStatus.IN_TRANSIT,
                        payload={
                            "track_code": row.track_code,
                            "client_code": row.client_code,
                            "raw_row": row.raw_row,
                        },
                    )
                )

            if client.telegram_chat_id:
                dedupe_key = self._notification_dedupe_key(row.track_code)
                if dedupe_key not in existing_notifications:
                    session.add(
                        NotificationOutbox(
                            client=client,
                            parcel=parcel,
                            kind="parcel_status_updated",
                            dedupe_key=dedupe_key,
                            payload={
                                "track_code": row.track_code,
                                "status": ParcelStatus.IN_TRANSIT.value,
                                "client_code": row.client_code,
                            },
                            status=NotificationStatus.PENDING,
                        )
                    )
                    existing_notifications.add(dedupe_key)

            matched_rows += 1

        for failed in unresolved_rows:
            session.add(
                UnmatchedImportRow(
                    import_job=job,
                    row_number=failed.row_number,
                    reason=failed.reason,
                    raw_row=failed.raw_row,
                )
            )
            unmatched_rows += 1

        job.total_rows = total_rows
        job.matched_rows = matched_rows
        job.unmatched_rows = unmatched_rows
        job.new_parcels = new_parcels
        job.updated_parcels = updated_parcels
        job.status = ImportStatus.PARTIAL if unmatched_rows else ImportStatus.COMPLETED
        job.error_message = None
        job.completed_at = datetime.now(tz=UTC)

    def _notification_dedupe_key(self, track_code: str) -> str:
        return f"parcel:{track_code}:IN_TRANSIT"

    def _storage_key(self, filename: str, checksum: str) -> str:
        safe_name = Path(filename).name.replace(" ", "_")
        return f"{self.storage_prefix}/{checksum[:12]}-{safe_name}"
