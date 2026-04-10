from __future__ import annotations

import re

CLIENT_CODE_PATTERN = re.compile(r"\bJ-\d{4,}\b", re.IGNORECASE)
TRACK_CODE_PATTERN = re.compile(r"\b(?:[A-Z]{1,3}\d{8,20}|\d{10,20})\b", re.IGNORECASE)
MULTISPACE_PATTERN = re.compile(r"\s+")


def normalize_whitespace(value: str) -> str:
    return MULTISPACE_PATTERN.sub(" ", value).strip()


def normalize_name(value: str) -> str:
    return normalize_whitespace(value).upper()


def normalize_client_code(value: str) -> str:
    prepared = normalize_whitespace(value).upper().replace(" ", "")
    if prepared.startswith("J") and "-" not in prepared and len(prepared) > 1:
        prepared = f"J-{prepared[1:]}"
    return prepared


def normalize_track_code(value: str) -> str:
    return normalize_whitespace(value).upper().replace(" ", "")


def unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def extract_client_code_candidates(text: str) -> list[str]:
    candidates = [normalize_client_code(match.group(0)) for match in CLIENT_CODE_PATTERN.finditer(text)]
    return unique_preserving_order(candidates)


def extract_track_code_candidates(text: str) -> list[str]:
    raw_candidates = [normalize_track_code(match.group(0)) for match in TRACK_CODE_PATTERN.finditer(text)]
    filtered = [
        candidate
        for candidate in raw_candidates
        if not candidate.startswith("J-") and len(candidate.replace("-", "")) >= 10
    ]
    return unique_preserving_order(filtered)

