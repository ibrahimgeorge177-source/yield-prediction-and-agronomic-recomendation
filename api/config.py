"""Runtime configuration, read from the environment.

Every value has a working default so the service starts with no environment
set at all. Deployment overrides them; nothing here is a secret.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


def _root() -> Path:
    p = Path(__file__).resolve()
    for cand in p.parents:
        if (cand / "data" / "features").exists():
            return cand
    return p.parents[1]


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """Resolved once per process."""

    def __init__(self) -> None:
        self.root = Path(os.getenv("PROJECT_ROOT", str(_root()))).resolve()

        # --- model artefact -------------------------------------------------
        # The fitted pipeline is ~90-125 MB and is not committed (see
        # .gitignore). Deployment supplies it: MODEL_DIR points at an unpacked
        # artefact directory, MODEL_URL at an archive to download on first use.
        self.model_dir = Path(os.getenv("MODEL_DIR",
                                        str(self.root / "data" / "models" / "district_v2")))
        if not self.model_dir.is_absolute():
            self.model_dir = (self.root / self.model_dir).resolve()
        self.model_url = os.getenv("MODEL_URL") or None
        self.model_version = os.getenv("MODEL_VERSION", self.model_dir.name)

        # Load the pipeline during startup rather than on the first request.
        # Off by default so the service still boots (and reports its state)
        # when the artefact is missing.
        self.eager_load = _flag("MODEL_EAGER_LOAD", False)

        self.defaults_path = Path(os.getenv(
            "FEATURE_DEFAULTS_PATH",
            str(self.root / "data" / "api" / "feature_defaults.json")))

        # --- request limits -------------------------------------------------
        self.max_plots_per_request = int(os.getenv("MAX_PLOTS_PER_REQUEST", "500"))

        # --- service --------------------------------------------------------
        self.port = int(os.getenv("PORT", "8000"))
        self.cors_origins = [o.strip() for o in
                             os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
        self.root_path = os.getenv("ROOT_PATH", "")
        self.env = os.getenv("ENVIRONMENT", "development")

    @property
    def model_metadata_path(self) -> Path:
        return self.model_dir / "metadata.json"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
