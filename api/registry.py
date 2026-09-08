"""Loading and holding the fitted DistrictPipeline.

The artefact is 90-125 MB of fitted forests and network weights and is not in
git (see .gitignore) -- deployment supplies it via MODEL_DIR, or MODEL_URL for
an archive fetched on first use.

The service is designed to boot without it. A missing artefact leaves the
prediction endpoints returning 503 with an actionable message while
/health, /api/v1/model/summary and the reference endpoints keep working, so a
deployment can be diagnosed rather than merely failing.
"""
from __future__ import annotations

import io
import json
import logging
import sys
import tarfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from api.config import get_settings

log = logging.getLogger(__name__)

# The artefact was pickled with scripts/ on sys.path, so it refers to the
# `district_model` module by that bare name. Put scripts/ on the path before
# unpickling or joblib.load raises ModuleNotFoundError.
_SCRIPTS_ON_PATH = False


def _ensure_scripts_importable() -> None:
    global _SCRIPTS_ON_PATH
    if _SCRIPTS_ON_PATH:
        return
    scripts = get_settings().root / "scripts"
    if scripts.exists() and str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    _SCRIPTS_ON_PATH = True


class ModelUnavailable(RuntimeError):
    """The fitted pipeline could not be loaded."""


class ModelRegistry:
    """Holds the pipeline and everything the summary endpoint reports.

    Thread-safe and idempotent: concurrent first requests load the artefact
    once. FastAPI runs sync endpoints in a threadpool, so this matters.
    """

    def __init__(self) -> None:
        self._pipeline: Any = None
        self._lock = threading.Lock()
        self._error: str | None = None
        self._loaded_at: float | None = None
        self._load_seconds: float | None = None

    # --- state ---------------------------------------------------------------
    @property
    def is_loaded(self) -> bool:
        return self._pipeline is not None

    @property
    def error(self) -> str | None:
        return self._error

    def status(self) -> dict:
        settings = get_settings()
        return {
            "loaded": self.is_loaded,
            "version": settings.model_version,
            "artefact_dir": str(settings.model_dir),
            "artefact_present": (settings.model_dir / "pipeline.joblib").exists(),
            "metadata_present": settings.model_metadata_path.exists(),
            "loaded_at": self._loaded_at,
            "load_seconds": self._load_seconds,
            "error": self._error,
        }

    # --- metadata, readable with or without the pipeline ----------------------
    def metadata(self) -> dict:
        """Training seasons and measured accuracy.

        Read from the loaded pipeline when there is one, otherwise from the
        metadata.json committed beside the artefact -- which is why the summary
        endpoint works on a deployment whose weights have not arrived yet.
        """
        if self._pipeline is not None and getattr(self._pipeline, "metadata_", None):
            return dict(self._pipeline.metadata_)
        path = get_settings().model_metadata_path
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("could not read %s: %s", path, exc)
        return {}

    # --- loading -------------------------------------------------------------
    def load(self, force: bool = False) -> Any:
        if self._pipeline is not None and not force:
            return self._pipeline
        with self._lock:
            if self._pipeline is not None and not force:
                return self._pipeline
            started = time.monotonic()
            try:
                self._pipeline = self._load_pipeline()
            except Exception as exc:                      # surfaced as 503, not a crash
                self._error = f"{type(exc).__name__}: {exc}"
                log.error("model load failed: %s", self._error)
                raise ModelUnavailable(self._error) from exc
            self._error = None
            self._loaded_at = time.time()
            self._load_seconds = round(time.monotonic() - started, 2)
            log.info("model loaded from %s in %.2fs",
                     get_settings().model_dir, self._load_seconds)
            return self._pipeline

    def _load_pipeline(self) -> Any:
        settings = get_settings()
        artefact = settings.model_dir / "pipeline.joblib"
        if not artefact.exists() and settings.model_url:
            _download_artefact(settings.model_url, settings.model_dir)
        if not artefact.exists():
            raise FileNotFoundError(
                f"no pipeline.joblib in {settings.model_dir}. The fitted artefact is "
                "not committed (it exceeds GitHub's file limit). Point MODEL_DIR at an "
                "unpacked artefact, set MODEL_URL to an archive to download, or build "
                "one with: python scripts/train_district_model.py "
                f"--out {settings.model_dir.relative_to(settings.root)}")

        _ensure_scripts_importable()
        from district_model import DistrictPipeline      # noqa: PLC0415  (needs sys.path)

        pipeline = DistrictPipeline.load(settings.model_dir)
        if getattr(pipeline, "ensemble_", None) is None:
            raise ValueError(f"the artefact in {settings.model_dir} is not fitted")
        return pipeline

    def require(self) -> Any:
        """The pipeline, or ModelUnavailable with the reason."""
        if self._pipeline is not None:
            return self._pipeline
        return self.load()


def _download_artefact(url: str, target: Path) -> None:
    """Fetch the artefact archive named by MODEL_URL into `target`.

    Accepts a bare pipeline.joblib, or a .zip / .tar.gz containing one.
    """
    log.info("downloading model artefact from %s", url)
    target.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=600) as response:   # noqa: S310
        payload = response.read()

    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            _extract_member(archive.namelist(), archive.read, target)
    elif url.endswith((".tar.gz", ".tgz")):
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            names = archive.getnames()
            _extract_member(names, lambda n: archive.extractfile(n).read(), target)
    else:
        (target / "pipeline.joblib").write_bytes(payload)
    log.info("model artefact written to %s", target)


def _extract_member(names: list[str], read, target: Path) -> None:
    joblibs = [n for n in names if n.endswith("pipeline.joblib")]
    if not joblibs:
        raise ValueError("the downloaded archive contains no pipeline.joblib")
    (target / "pipeline.joblib").write_bytes(read(joblibs[0]))
    for name in names:
        if name.endswith("metadata.json"):
            (target / "metadata.json").write_bytes(read(name))
            break


_registry = ModelRegistry()


def get_registry() -> ModelRegistry:
    return _registry
