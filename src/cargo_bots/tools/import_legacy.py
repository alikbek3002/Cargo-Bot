from __future__ import annotations

import argparse
import asyncio
import csv
from pathlib import Path

from sqlalchemy import select

from cargo_bots.core.config import get_settings
from cargo_bots.db.models import LegacyClient
from cargo_bots.db.session import Database


async def import_legacy_clients(csv_path: str) -> None:
    database = Database(get_settings())
    path = Path(csv_path)

    async with database.session() as session:
        async with session.begin():
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    client_code = (row.get("client_code") or "").strip().upper()
                    full_name = (row.get("full_name") or "").strip()
                    if not client_code or not full_name:
                        continue

                    legacy = await session.scalar(
                        select(LegacyClient).where(LegacyClient.client_code == client_code)
                    )
                    if legacy:
                        legacy.full_name = full_name
                        legacy.phone = (row.get("phone") or "").strip() or None
                        legacy.notes = (row.get("notes") or "").strip() or None
                    else:
                        session.add(
                            LegacyClient(
                                client_code=client_code,
                                full_name=full_name,
                                phone=(row.get("phone") or "").strip() or None,
                                notes=(row.get("notes") or "").strip() or None,
                            )
                        )

    await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import legacy clients into PostgreSQL.")
    parser.add_argument("csv_path", help="CSV with columns: client_code,full_name,phone,notes")
    args = parser.parse_args()
    asyncio.run(import_legacy_clients(args.csv_path))


if __name__ == "__main__":
    main()
