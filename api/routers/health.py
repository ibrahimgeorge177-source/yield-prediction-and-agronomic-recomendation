"""Liveness and readiness.

/health answers whether the process is up and is what a platform health check
should poll. /ready additionally requires the model to be loadable, which makes
it the right gate for sending traffic.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from api import __version__
from api.config import get_settings
from api.defaults import DefaultsUnavailable, get_defaults
from api.registry import ModelUnavailable, get_registry
from api.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness")
def health() -> HealthResponse:
    registry = get_registry()
    try:
        get_defaults()
        defaults_ok, detail = True, None
    except DefaultsUnavailable as exc:
        defaults_ok, detail = False, str(exc)

    if not defaults_ok:
        state = "degraded"
    elif not registry.is_loaded:
        state = "degraded" if registry.error else "ok"
        detail = detail or registry.error or "model not loaded yet (loads on first request)"
    else:
        state = "ok"

    return HealthResponse(status=state, version=__version__,
                          environment=get_settings().env,
                          model_loaded=registry.is_loaded,
                          defaults_loaded=defaults_ok, detail=detail)


@router.get("/ready", summary="Readiness: model loadable and defaults present")
def ready(response: Response) -> dict:
    registry = get_registry()
    try:
        get_defaults()
        registry.require()
    except (DefaultsUnavailable, ModelUnavailable) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    response.status_code = status.HTTP_200_OK
    return {"status": "ready", "model": registry.status()}
