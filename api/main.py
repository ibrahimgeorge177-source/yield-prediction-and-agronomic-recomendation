"""Kenya maize yield prediction API.

    uvicorn api.main:app --reload

The service exposes the district-level yield model documented in
Documentation/kenya_maize_district_model_deployment.md. It boots without the
model artefact present -- the prediction endpoints then return 503 with an
actionable message while /health and /api/v1/model/summary still describe what
the deployment is meant to be serving.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api import __version__
from api.config import get_settings
from api.curves import CurvesUnavailable, get_curves
from api.defaults import DefaultsUnavailable, get_defaults
from api.registry import ModelUnavailable, get_registry
from api.routers import health, model, predict, recommend, reference

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")

DESCRIPTION = """
Maize yield prediction for Kenyan districts, from the One Acre Fund MEL
Agronomic Survey (2016-2020).

**What it predicts.** Mean maize yield in kg/ha for a district-season, with a
prediction interval and the number of plots behind it. Per-plot estimates are
returned too, but they are much weaker (R2 ~0.16) than the district means the
model was validated on (R2 0.22-0.59, MAE 300-700 kg/ha out-of-time).

**What it recommends.** `POST /api/v1/recommend` ranks the changes available to
one plot -- planting date, DAP, CAN topdress, hybrid seed share, variety,
compost, lime, intercropping -- each with an expected lift in kg/ha, a
district-clustered interval, and the evidence behind it. Everything is in yield
units: this survey carries no price data, so the layer does not cost anything or
budget anything. Lifts come from fixed-effects response curves, not from
inverting the yield model, so this endpoint answers even when the model artefact
is absent.

**How to call it.** Send only what you know -- district, season, seed choice,
fertiliser rates, planting date. Anything omitted is filled from that
district's own history, so a sparse request still scores. `GET
/api/v1/reference/input-schema` describes every accepted field with its allowed
values and observed range.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info("starting %s in %s (root=%s)", app.title, settings.env, settings.root)

    try:
        defaults = get_defaults()
        log.info("feature defaults: %d districts, %d model columns, seasons %s",
                 len(defaults.districts), len(defaults.model_columns), defaults.years)
    except DefaultsUnavailable as exc:
        log.error("feature defaults unavailable: %s", exc)

    try:
        curves = get_curves()
        log.info("lever curves: %d levers fitted %s",
                 len(curves.levers), curves.generated_at)
    except CurvesUnavailable as exc:
        log.error("lever curves unavailable, /recommend will 503: %s", exc)

    if settings.eager_load:
        try:
            get_registry().load()
        except ModelUnavailable as exc:
            log.error("eager model load failed, continuing degraded: %s", exc)
    else:
        status = get_registry().status()
        log.info("model artefact present=%s at %s (loads on first prediction)",
                 status["artefact_present"], status["artefact_dir"])
    yield
    log.info("shutting down")


settings = get_settings()

app = FastAPI(
    title="Kenya Maize Yield Prediction API",
    description=DESCRIPTION,
    version=__version__,
    lifespan=lifespan,
    root_path=settings.root_path,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

@app.middleware("http")
async def catch_unhandled(request: Request, call_next):
    """Turn an unhandled exception into a JSON response, from inside CORS.

    Starlette's default 500 is a bare text body produced outside the CORS
    middleware, so a browser never sees the response at all — it reports a
    network failure and the caller is told the API is unreachable when in fact
    it answered. Handling it here keeps the CORS headers on the response, so the
    real error reaches the client instead of a misleading one.
    """
    try:
        return await call_next(request)
    except Exception as exc:                              # noqa: BLE001 - deliberate boundary
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": f"internal error: {type(exc).__name__}: {exc}"},
        )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(predict.router)
app.include_router(recommend.router)
app.include_router(model.router)
app.include_router(reference.router)


@app.exception_handler(DefaultsUnavailable)
async def _defaults_unavailable(request: Request, exc: DefaultsUnavailable) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(CurvesUnavailable)
async def _curves_unavailable(request: Request, exc: CurvesUnavailable) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(ModelUnavailable)
async def _model_unavailable(request: Request, exc: ModelUnavailable) -> JSONResponse:
    return JSONResponse(status_code=503,
                        content={"detail": f"the prediction model is not available: {exc}"})


@app.get("/", tags=["health"], summary="Service index")
def index() -> dict:
    return {
        "service": app.title,
        "version": __version__,
        "model_version": settings.model_version,
        "docs": "/docs",
        "endpoints": {
            "predict": "POST /api/v1/predict",
            "recommend": "POST /api/v1/recommend",
            "model_summary": "GET /api/v1/model/summary",
            "districts": "GET /api/v1/reference/districts",
            "seed_types": "GET /api/v1/reference/seed-types",
            "levers": "GET /api/v1/reference/levers",
            "input_schema": "GET /api/v1/reference/input-schema",
            "health": "GET /health",
            "ready": "GET /ready",
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.main:app", host="0.0.0.0", port=settings.port)   # noqa: S104
