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
        _warn_on_version_skew(getattr(pipeline, "metadata_", {}) or {})
        return pipeline

    def require(self) -> Any:
        """The pipeline, or ModelUnavailable with the reason."""
        if self._pipeline is not None:
            return self._pipeline
        return self.load()


def _warn_on_version_skew(metadata: dict) -> None:
    """Say so at load time when the serving libraries differ from the fitting ones.

    A pickled estimator is only guaranteed to load in the version that wrote it.
    scikit-learn 1.6 → 1.7 is the case that bit this service: the artefact
    unpickles cleanly and then every prediction fails with
    "AttributeError: 'SimpleImputer' object has no attribute '_fill_dtype'".
    Artefacts built before versions were recorded simply skip this check.
    """
    fitted = metadata.get("library_versions")
    if not fitted:
        return
    try:
        from district_model import library_versions      # noqa: PLC0415
    except ImportError:
        return
    running = library_versions()
    skew = {k: (v, running.get(k)) for k, v in fitted.items()
            if k in running and running[k] != v}
    if skew:
        detail = ", ".join(f"{k}: fitted {a}, running {b}" for k, (a, b) in sorted(skew.items()))
        log.warning("library version skew between the artefact and this service — %s. "
                    "Predictions may fail; pin these in requirements.txt.", detail)


GZIP_MAGIC, ZIP_MAGIC = b"\x1f\x8b", b"PK\x03\x04"
PICKLE_MAGIC = (b"\x80", b"\x78", b"ZL", b"\x04\x22\x4d\x18")   # pickle, zlib, joblib, lz4
MIN_ARTEFACT_BYTES = 1_000_000                                   # smallest real build is tens of MB


def _download_artefact(url: str, target: Path) -> None:
    """Fetch the artefact named by MODEL_URL into `target`.

    Accepts a bare pipeline.joblib, or a .zip / .tar.gz containing one. The
    format is decided by the payload's magic bytes rather than by the URL
    suffix, because the common misconfiguration is a URL that returns something
    else entirely -- a GitHub release *page* (HTML), an API URL (JSON), or a
    login redirect. Storing one of those as pipeline.joblib produces a far worse
    symptom later: joblib.load fails deep inside the unpickler with
    "IndexError: pop from empty list", which says nothing about the real cause.
    """
    log.info("downloading model artefact from %s", url)
    target.mkdir(parents=True, exist_ok=True)

    # GitHub serves an asset's metadata as JSON for api.github.com URLs unless
    # the caller asks for the bytes; with this header both that URL and the
    # browser download URL return the artefact itself.
    request = urllib.request.Request(url, headers={
        "Accept": "application/octet-stream",
        "User-Agent": "kenya-maize-yield-api",
    })
    with urllib.request.urlopen(request, timeout=600) as response:   # noqa: S310
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
        payload = response.read()

    _reject_if_not_an_artefact(url, payload, content_type)

    if payload.startswith(ZIP_MAGIC):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            _extract_member(archive.namelist(), archive.read, target)
    elif payload.startswith(GZIP_MAGIC):
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            names = archive.getnames()
            _extract_member(names, lambda n: archive.extractfile(n).read(), target)
    else:
        (target / "pipeline.joblib").write_bytes(payload)

    artefact = target / "pipeline.joblib"
    log.info("model artefact written to %s (%.1f MB)", artefact,
             artefact.stat().st_size / 1e6)


def _reject_if_not_an_artefact(url: str, payload: bytes, content_type: str) -> None:
    """Fail with the actual problem, before anything unloadable reaches disk."""
    head = payload[:16].lstrip()
    looks_like_text = (content_type in {"text/html", "application/json", "text/plain"}
                       or head[:1] in (b"<", b"{", b"["))
    if looks_like_text:
        excerpt = payload[:200].decode("utf-8", "replace").replace("\n", " ")
        hint = ""
        if "api.github.com" in url:
            hint = (" This looks like a GitHub API URL, which returns the asset's "
                    "metadata; use the release's browser_download_url instead.")
        elif "/releases/tag/" in url or url.rstrip("/").endswith("/releases"):
            hint = (" This is the release *page*, not the asset; use the "
                    "browser_download_url, which ends in the file name.")
        raise ValueError(
            f"MODEL_URL returned {content_type or 'text'} rather than a model artefact."
            f"{hint} First bytes: {excerpt!r}")

    if len(payload) < MIN_ARTEFACT_BYTES:
        raise ValueError(
            f"MODEL_URL returned only {len(payload):,} bytes, far smaller than any real "
            "artefact (tens of MB). The download was probably truncated or redirected.")

    if not (payload.startswith(ZIP_MAGIC) or payload.startswith(GZIP_MAGIC)
            or any(payload.startswith(m) for m in PICKLE_MAGIC)):
        raise ValueError(
            f"MODEL_URL returned {len(payload):,} bytes that are neither a zip, a gzip "
            f"archive, nor a joblib file (first bytes: {payload[:8]!r}). Point MODEL_URL "
            "at a .tar.gz produced by scripts/package_model_artifact.py.")


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
