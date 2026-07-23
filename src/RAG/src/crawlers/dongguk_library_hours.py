"""Network boundary for Dongguk library operation-time JSON.

Normalization intentionally lives in ``src.services.library_hours`` so this
module can be tested with an injected HTTP client and never needs to write an
artifact or database row.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import requests


LIBRARY_OPERATION_TIME_URL = "https://lib.dongguk.edu/pyxis-api/1/branches/operation-time"
LIBRARY_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "DongttokLibraryHoursConnector/1.0",
}
DEFAULT_TIMEOUT_SECONDS = 10.0


class LibraryHoursFetchError(RuntimeError):
    """The official endpoint did not return a usable JSON response."""


class LibraryHoursFetchTimeout(LibraryHoursFetchError):
    """The official endpoint exceeded the configured timeout."""


@dataclass(frozen=True)
class LibraryHoursFetchResult:
    payload: Any
    source_url: str
    fetched_at: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("fetch clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_library_operation_times(
    *,
    http_client: Any = requests,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    clock: Callable[[], datetime] = _utc_now,
) -> LibraryHoursFetchResult:
    """Fetch official JSON once; callers decide retry and persistence policy."""
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    try:
        response = http_client.get(
            LIBRARY_OPERATION_TIME_URL,
            headers=LIBRARY_HEADERS,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.exceptions.Timeout as exc:
        raise LibraryHoursFetchTimeout(
            f"library operation-time request timed out after {timeout:g}s"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise LibraryHoursFetchError(
            f"library operation-time request failed: {type(exc).__name__}"
        ) from exc
    except (TypeError, ValueError) as exc:
        raise LibraryHoursFetchError("library operation-time response is not valid JSON") from exc

    return LibraryHoursFetchResult(
        payload=payload,
        source_url=LIBRARY_OPERATION_TIME_URL,
        fetched_at=_iso_utc(clock()),
    )


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "LIBRARY_OPERATION_TIME_URL",
    "LibraryHoursFetchError",
    "LibraryHoursFetchResult",
    "LibraryHoursFetchTimeout",
    "fetch_library_operation_times",
]
