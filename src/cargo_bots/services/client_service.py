from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Integer, cast, func, select

from cargo_bots.db.models import Client, LegacyClient, Parcel, SystemCounter
from cargo_bots.db.session import Database
from cargo_bots.services.address_book import AddressTemplateService
from cargo_bots.services.normalization import normalize_client_code, normalize_name


class ClientServiceError(Exception):
    pass


class ClientAlreadyBoundError(ClientServiceError):
    pass


class ClientValidationError(ClientServiceError):
    pass


class ClientNotRegisteredError(ClientServiceError):
    pass


@dataclass(slots=True)
class ClientProfile:
    client_code: str
    full_name: str
    registered_at: object
    address: str


class ClientService:
    def __init__(self, database: Database, address_service: AddressTemplateService) -> None:
        self.database = database
        self.address_service = address_service

    async def get_client_by_telegram_user(self, telegram_user_id: int) -> Client | None:
        async with self.database.session() as session:
            result = await session.scalar(
                select(Client).where(Client.telegram_user_id == telegram_user_id)
            )
            return result

    async def get_profile(self, telegram_user_id: int) -> ClientProfile:
        client = await self.get_client_by_telegram_user(telegram_user_id)
        if not client:
            raise ClientNotRegisteredError("Клиент ещё не зарегистрирован.")
        return ClientProfile(
            client_code=client.client_code,
            full_name=client.full_name,
            registered_at=client.registered_at,
            address=self.address_service.render(client.client_code),
        )

    async def list_issued_parcels(self, telegram_user_id: int) -> list[Parcel]:
        async with self.database.session() as session:
            client = await session.scalar(
                select(Client).where(Client.telegram_user_id == telegram_user_id)
            )
            if not client:
                raise ClientNotRegisteredError("Клиент ещё не зарегистрирован.")

            from cargo_bots.db.models import ParcelStatus
            result = await session.scalars(
                select(Parcel)
                .where(Parcel.client_id == client.id)
                .where(Parcel.status == ParcelStatus.ISSUED)
                .order_by(Parcel.last_seen_at.desc())
            )
            return list(result.all())

    async def list_client_parcels(self, telegram_user_id: int) -> list[Parcel]:
        async with self.database.session() as session:
            client = await session.scalar(
                select(Client).where(Client.telegram_user_id == telegram_user_id)
            )
            if not client:
                raise ClientNotRegisteredError("Клиент ещё не зарегистрирован.")

            from cargo_bots.db.models import ParcelStatus
            result = await session.scalars(
                select(Parcel)
                .where(Parcel.client_id == client.id)
                .where(Parcel.status != ParcelStatus.ISSUED)
                .order_by(Parcel.updated_at.desc(), Parcel.created_at.desc())
            )
            return list(result.all())

    async def get_ready_parcels_by_client_code(self, client_code: str) -> list[Parcel]:
        """Ищем клиента по коду и возвращаем его READY-посылки (для выдачи)."""
        from cargo_bots.db.models import ParcelStatus
        normalized_code = normalize_client_code(client_code)
        async with self.database.session() as session:
            # Сначала точное совпадение
            client = await session.scalar(
                select(Client).where(Client.client_code == normalized_code)
            )
            # Если не нашли — попробуем ilike (частичное)
            if not client:
                client = await session.scalar(
                    select(Client).where(Client.client_code.ilike(f"%{client_code.strip()}%"))
                )
            if not client:
                return []
            result = await session.scalars(
                select(Parcel)
                .where(Parcel.client_id == client.id)
                .where(Parcel.status == ParcelStatus.READY)
            )
            return list(result.all())

    async def get_all_parcels_by_client_code(self, client_code: str) -> tuple[Client | None, list[Parcel]]:
        """Возвращает клиента и ВСЕ его активные посылки (для отображения в админке)."""
        from cargo_bots.db.models import ParcelStatus
        normalized_code = normalize_client_code(client_code)
        async with self.database.session() as session:
            client = await session.scalar(
                select(Client).where(Client.client_code == normalized_code)
            )
            if not client:
                client = await session.scalar(
                    select(Client).where(Client.client_code.ilike(f"%{client_code.strip()}%"))
                )
            if not client:
                return None, []
            result = await session.scalars(
                select(Parcel)
                .where(Parcel.client_id == client.id)
                .where(Parcel.status != ParcelStatus.ISSUED)
                .order_by(Parcel.status.asc())
            )
            return client, list(result.all())

    async def get_parcel_by_track_code(self, track_code: str) -> Parcel | None:
        """Ищет посылку по трек-коду (точное совпадение, нормализованное)."""
        from cargo_bots.services.normalization import normalize_track_code
        normalized = normalize_track_code(track_code)
        async with self.database.session() as session:
            parcel = await session.scalar(
                select(Parcel).where(Parcel.track_code == normalized)
            )
            if parcel:
                return parcel
            # fallback: попробовать как есть
            parcel = await session.scalar(
                select(Parcel).where(Parcel.track_code == track_code.strip())
            )
            return parcel

    async def mark_parcels_as_issued(self, parcel_ids: list[object]) -> int:
        from cargo_bots.db.models import ParcelStatus, ParcelEvent, NotificationOutbox
        from datetime import datetime, UTC
        from sqlalchemy.orm import selectinload
        
        async with self.database.session() as session:
            result = await session.scalars(
                select(Parcel)
                .options(selectinload(Parcel.client))
                .where(Parcel.id.in_(parcel_ids))
                .where(Parcel.status == ParcelStatus.READY)
            )
            parcels = list(result.all())
            if not parcels:
                return 0
                
            updated_count = 0
            for parcel in parcels:
                parcel.status = ParcelStatus.ISSUED
                parcel.last_seen_at = datetime.now(tz=UTC)
                
                session.add(
                    ParcelEvent(
                        parcel=parcel,
                        old_status=ParcelStatus.READY,
                        new_status=ParcelStatus.ISSUED,
                        payload={
                            "track_code": parcel.track_code,
                            "client_code": parcel.client.client_code,
                        },
                    )
                )

                if parcel.client.telegram_chat_id:
                    session.add(
                        NotificationOutbox(
                            client=parcel.client,
                            parcel=parcel,
                            kind="parcel_status_updated",
                            dedupe_key=f"parcel:{parcel.track_code}:ISSUED",
                            payload={
                                "track_code": parcel.track_code,
                                "status": ParcelStatus.ISSUED.value,
                                "client_code": parcel.client.client_code,
                            },
                        )
                    )
                updated_count += 1

            await session.commit()
            return updated_count

    async def search_client_parcels(self, telegram_user_id: int, query: str) -> list[Parcel]:
        async with self.database.session() as session:
            client = await session.scalar(
                select(Client).where(Client.telegram_user_id == telegram_user_id)
            )
            if not client:
                raise ClientNotRegisteredError("Клиент ещё не зарегистрирован.")

            from cargo_bots.db.models import ParcelStatus
            result = await session.scalars(
                select(Parcel)
                .where(Parcel.client_id == client.id)
                .where(Parcel.track_code.ilike(f"%{query}%"))
                .where(Parcel.status != ParcelStatus.ISSUED)
                .order_by(Parcel.updated_at.desc(), Parcel.created_at.desc())
            )
            return list(result.all())

    async def bind_legacy_client(
        self,
        *,
        telegram_user_id: int,
        telegram_chat_id: int,
        client_code: str,
        full_name: str,
    ) -> Client:
        normalized_code = normalize_client_code(client_code)
        normalized_name = normalize_name(full_name)

        async with self.database.session() as session:
            async with session.begin():
                already_bound = await session.scalar(
                    select(Client).where(Client.telegram_user_id == telegram_user_id)
                )
                if already_bound and already_bound.client_code != normalized_code:
                    raise ClientAlreadyBoundError("Этот Telegram уже привязан к другому клиенту.")

                client = await session.scalar(
                    select(Client).where(Client.client_code == normalized_code)
                )
                if client and client.telegram_user_id not in (None, telegram_user_id):
                    raise ClientAlreadyBoundError("Этот старый код уже привязан к другому пользователю в новом боте.")

                if not client:
                    client = Client(
                        client_code=normalized_code,
                        full_name=full_name,
                        telegram_user_id=telegram_user_id,
                        telegram_chat_id=telegram_chat_id,
                        is_legacy_bound=False,
                    )
                    session.add(client)
                else:
                    client.full_name = full_name
                    client.telegram_user_id = telegram_user_id
                    client.telegram_chat_id = telegram_chat_id
                    client.is_legacy_bound = False

                await session.flush()
                return client

    async def register_new_client(
        self,
        *,
        telegram_user_id: int,
        telegram_chat_id: int,
        full_name: str,
    ) -> Client:
        async with self.database.session() as session:
            async with session.begin():
                existing = await session.scalar(
                    select(Client).where(Client.telegram_user_id == telegram_user_id)
                )
                if existing:
                    return existing

                counter = await session.scalar(
                    select(SystemCounter)
                    .where(SystemCounter.name == "client_code")
                    .with_for_update()
                )
                if not counter:
                    counter = SystemCounter(
                        name="client_code",
                        value=await self._detect_max_numeric_code(session),
                    )
                    session.add(counter)
                    await session.flush()

                counter.value += 1
                code = f"J-{counter.value:04d}"
                client = Client(
                    client_code=code,
                    full_name=full_name.strip(),
                    telegram_user_id=telegram_user_id,
                    telegram_chat_id=telegram_chat_id,
                    is_legacy_bound=False,
                )
                session.add(client)
                await session.flush()
                return client

    async def render_address_for_telegram_user(self, telegram_user_id: int) -> str:
        client = await self.get_client_by_telegram_user(telegram_user_id)
        if not client:
            raise ClientNotRegisteredError("Сначала нужно пройти регистрацию.")
        return self.address_service.render(client.client_code)

    async def _detect_max_numeric_code(self, session) -> int:
        extract_digits = lambda column: cast(
            func.nullif(func.regexp_replace(column, r"\D", "", "g"), ""),
            Integer,
        )
        current_max = await session.scalar(select(func.max(extract_digits(Client.client_code))))
        legacy_max = await session.scalar(select(func.max(extract_digits(LegacyClient.client_code))))
        return max(current_max or 0, legacy_max or 0)

