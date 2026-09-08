"""Access to the empirical fill values in data/api/feature_defaults.json.

The artefact is produced by `python scripts/build_api_defaults.py` and holds
per-district and per-district-season medians and modes for all 143 columns the
model consumes, the category vocabularies, observed numeric ranges and the
winsorization ceilings used during cleaning.

Loaded once per process and treated as immutable.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from api.config import get_settings


class DefaultsUnavailable(RuntimeError):
    """The fill-value artefact is missing or unreadable."""


class FeatureDefaults:
    """Read-only view over the fill-value artefact."""

    def __init__(self, payload: dict) -> None:
        self._d = payload
        self.model_columns: list[str] = payload["model_columns"]
        self.recommended_features: list[str] = payload["recommended_features"]
        self.categorical_features: list[str] = payload["categorical_features"]
        self.weather_features: list[str] = payload["weather_features"]
        self.kinds: dict[str, str] = payload["kinds"]
        self.categories: dict[str, list[str]] = payload["categories"]
        self.ranges: dict[str, dict] = payload["ranges"]
        self.caps: dict[str, float] = payload["caps"]
        self.districts: list[str] = payload["districts"]
        self.years: list[int] = payload["years"]
        self.wealth: dict[str, Any] = payload.get("wealth", {})
        self.generated_at: str = payload.get("generated_at", "")
        self.source: dict = payload.get("source", {})
        self._global: dict = payload["global"]
        self._by_district: dict = payload["by_district"]
        self._w_by_dy: dict = payload["weather_by_district_year"]
        self._w_by_d: dict = payload["weather_by_district"]
        self._district_lookup = {d.strip().lower(): d for d in self.districts}

    # --- districts ----------------------------------------------------------
    def resolve_district(self, name: str) -> str | None:
        """Match a caller's district name case-insensitively. Returns the
        canonical spelling, or None if the district is unknown to the model."""
        if name is None:
            return None
        return self._district_lookup.get(str(name).strip().lower())

    def is_known_district(self, name: str) -> bool:
        return self.resolve_district(name) is not None

    def latest_year(self) -> int | None:
        return max(self.years) if self.years else None

    # --- fill values --------------------------------------------------------
    def base_row(self, district: str | None, year: int | None) -> dict:
        """A complete, in-distribution row for a district-season, before any
        caller input is applied.

        Precedence: observed weather for that district-season, then the
        district's own medians, then the national medians. A district the model
        has never seen falls back to the national row throughout -- the
        deployment guide records that such districts carry roughly twice the
        level error.
        """
        row = dict(self._global)
        canonical = self.resolve_district(district) if district else None
        if canonical:
            row.update(self._by_district.get(canonical, {}))
            weather = None
            if year is not None:
                weather = self._w_by_dy.get(f"{canonical}|{int(year)}")
            if weather is None:
                weather = self._w_by_d.get(canonical)
            if weather:
                row.update(weather)
            row["district"] = canonical
        return row

    def has_season_weather(self, district: str | None, year: int | None) -> bool:
        canonical = self.resolve_district(district) if district else None
        if not canonical or year is None:
            return False
        return f"{canonical}|{int(year)}" in self._w_by_dy

    def category_values(self, column: str) -> list[str]:
        return self.categories.get(column, [])

    def cap(self, column: str) -> float | None:
        return self.caps.get(column)

    def range_of(self, column: str) -> dict | None:
        return self.ranges.get(column)


@lru_cache(maxsize=1)
def get_defaults() -> FeatureDefaults:
    path: Path = get_settings().defaults_path
    if not path.exists():
        raise DefaultsUnavailable(
            f"feature defaults not found at {path}. "
            "Build them with: python scripts/build_api_defaults.py")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DefaultsUnavailable(f"could not read feature defaults at {path}: {exc}") from exc
    return FeatureDefaults(payload)
