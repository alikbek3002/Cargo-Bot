from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cargo_bots.services.normalization import (
    extract_client_code_candidates,
    extract_track_code_candidates,
    normalize_whitespace,
    unique_preserving_order,
)


@dataclass(slots=True)
class ParsedImportRow:
    row_number: int
    client_code: str
    track_code: str
    raw_row: dict[str, Any]


@dataclass(slots=True)
class FailedImportRow:
    row_number: int
    reason: str
    raw_row: dict[str, Any]


@dataclass(slots=True)
class ImportParseResult:
    parsed_rows: list[ParsedImportRow]
    failed_rows: list[FailedImportRow]
    total_rows: int


@dataclass(slots=True)
class SupplierTemplate:
    sheet_name: int | str
    client_code_aliases: list[str]
    track_code_aliases: list[str]


class SupplierWorkbookParser:
    def __init__(self, template_path: str | None = None) -> None:
        self.template = self._load_template(template_path)

    def _load_template(self, template_path: str | None) -> SupplierTemplate:
        default_path = Path(__file__).resolve().parent.parent / "resources" / "supplier_template.json"
        raw = json.loads(Path(template_path or default_path).read_text(encoding="utf-8"))
        return SupplierTemplate(
            sheet_name=raw.get("sheet_name", 0),
            client_code_aliases=[item.lower() for item in raw.get("client_code_aliases", [])],
            track_code_aliases=[item.lower() for item in raw.get("track_code_aliases", [])],
        )

    def parse_bytes(self, payload: bytes) -> ImportParseResult:
        pandas = self._import_pandas()

        # Первая попытка: читаем с заголовками
        dataframe = pandas.read_excel(
            io.BytesIO(payload),
            sheet_name=self.template.sheet_name,
            engine="calamine",
            dtype=str,
        ).fillna("")

        # Проверяем, есть ли среди колонок хотя бы один известный alias
        all_aliases = set(self.template.client_code_aliases + self.template.track_code_aliases)
        col_names_lower = {str(c).strip().lower() for c in dataframe.columns}
        has_known_headers = bool(col_names_lower & all_aliases)

        if not has_known_headers:
            # Файл без заголовков — перечитываем с header=None
            dataframe = pandas.read_excel(
                io.BytesIO(payload),
                sheet_name=self.template.sheet_name,
                engine="calamine",
                dtype=str,
                header=None,
            ).fillna("")

            # Назначаем колонки по позиции:
            # Колонка 0 = трек-код, Колонка 1 = код клиента
            if len(dataframe.columns) >= 2:
                dataframe.columns = ["track_code"] + ["client_code"] + [
                    f"col_{i}" for i in range(2, len(dataframe.columns))
                ]
            elif len(dataframe.columns) == 1:
                dataframe.columns = ["track_code"]

        parsed_rows: list[ParsedImportRow] = []
        failed_rows: list[FailedImportRow] = []
        total_rows = 0

        for offset, row in enumerate(dataframe.to_dict(orient="records"), start=2):
            normalized_row = self._normalize_row(row)
            if not any(normalized_row.values()):
                continue

            total_rows += 1
            parsed, failed = self._parse_row(offset, normalized_row)
            if parsed:
                parsed_rows.append(parsed)
            if failed:
                failed_rows.append(failed)

        return ImportParseResult(
            parsed_rows=parsed_rows,
            failed_rows=failed_rows,
            total_rows=total_rows,
        )

    def _parse_row(
        self,
        row_number: int,
        row: dict[str, str],
    ) -> tuple[ParsedImportRow | None, FailedImportRow | None]:
        client_candidates = self._extract_candidates(row, self.template.client_code_aliases, is_track=False)
        track_candidates = self._extract_candidates(row, self.template.track_code_aliases, is_track=True)

        if len(client_candidates) != 1:
            return None, FailedImportRow(
                row_number=row_number,
                reason=f"Expected exactly one client code, got {len(client_candidates)}",
                raw_row=row,
            )

        if len(track_candidates) != 1:
            return None, FailedImportRow(
                row_number=row_number,
                reason=f"Expected exactly one track code, got {len(track_candidates)}",
                raw_row=row,
            )

        return (
            ParsedImportRow(
                row_number=row_number,
                client_code=client_candidates[0],
                track_code=track_candidates[0],
                raw_row=row,
            ),
            None,
        )

    def _extract_candidates(
        self,
        row: dict[str, str],
        aliases: list[str],
        *,
        is_track: bool,
    ) -> list[str]:
        alias_hits = self._values_by_alias(row, aliases)
        value_pool = alias_hits or [value for value in row.values() if value]
        scan_text = "\n".join(value_pool)

        if is_track:
            candidates = extract_track_code_candidates(scan_text)
        else:
            candidates = extract_client_code_candidates(scan_text)

        if candidates:
            return unique_preserving_order(candidates)

        fallback_candidates: list[str] = []
        for value in value_pool:
            if is_track:
                fallback_candidates.extend(extract_track_code_candidates(value))
            else:
                fallback_candidates.extend(extract_client_code_candidates(value))
        return unique_preserving_order(fallback_candidates)

    def _values_by_alias(self, row: dict[str, str], aliases: list[str]) -> list[str]:
        normalized_aliases = {normalize_whitespace(alias).lower() for alias in aliases}
        collected: list[str] = []
        for key, value in row.items():
            normalized_key = normalize_whitespace(key).lower()
            if normalized_key in normalized_aliases and value:
                collected.append(value)
        return collected

    def _normalize_row(self, row: dict[str, Any]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, value in row.items():
            cleaned_key = normalize_whitespace(str(key))
            if not cleaned_key:
                continue

            if value is None:
                cleaned_value = ""
            else:
                cleaned_value = normalize_whitespace(str(value))
            normalized[cleaned_key] = cleaned_value
        return normalized

    @staticmethod
    def _import_pandas() -> Any:
        import pandas

        return pandas

