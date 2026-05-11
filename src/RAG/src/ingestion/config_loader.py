from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml

from src.config import SOURCES_CONFIG_PATH


@lru_cache(maxsize=1)
def load_sources_config() -> dict[str, Any]:
    with SOURCES_CONFIG_PATH.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def get_source_settings(source_name: str) -> dict[str, Any]:
    config = load_sources_config()
    defaults = config.get("defaults", {})
    source = (config.get("sources", {}) or {}).get(source_name)
    if source is None:
        raise KeyError(f"Source '{source_name}' is not configured.")
    merged = {**defaults, **source}
    merged["source_name"] = source_name
    return merged


def list_enabled_sources() -> list[str]:
    config = load_sources_config()
    sources = config.get("sources", {}) or {}
    return [name for name, value in sources.items() if value.get("enabled", True)]
