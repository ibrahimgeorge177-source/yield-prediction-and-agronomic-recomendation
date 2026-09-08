# Kenya maize yield prediction API — container image for Railway.
#
# Two things make this image unusual, and both are deliberate:
#
#   1. The fitted pipeline (90-125 MB) is NOT in git, so it is not in the build
#      context. Supply it in one of three ways:
#        build time   --build-arg MODEL_URL=https://.../district_v2.tar.gz
#                     (self-contained image, instant startup — recommended)
#        run time     MODEL_URL as a service variable (downloaded on first
#                     prediction, re-downloaded after every restart)
#        volume       MODEL_DIR pointing at a mounted Railway volume
#      With none of them the service still boots: /health, /api/v1/model/summary
#      and the reference and recommendation endpoints work, and the prediction
#      endpoints answer 503 with an actionable message.
#
#   2. torch is installed from PyTorch's CPU index. The default PyPI wheel drags
#      in the CUDA runtime — roughly 2.5 GB of GPU libraries that no Railway
#      container can use.
#
# Build:
#   docker build -t maize-api .
#   docker build -t maize-api --build-arg MODEL_URL=https://.../district_v2.tar.gz .
#   docker build -t maize-api --build-arg INCLUDE_TORCH=false .   # tree-only artefact
#
# Run:
#   docker run -p 8080:8080 -e MODEL_DIR=/models -v "$PWD/data/models/district_v2:/models:ro" maize-api

# ---------------------------------------------------------------------------
# builder — resolve dependencies into a virtualenv
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# build-essential is needed only while wheels are resolved; it does not travel
# to the runtime stage.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Set to false when the artefact was built with `train_district_model.py --no-nn`:
# it drops ~200 MB from the image and removes the torch dependency entirely.
ARG INCLUDE_TORCH=true

COPY requirements.txt ./

# torch first, pinned to the CPU index, so the requirements pass finds it
# already satisfied and never reaches the CUDA-flavoured PyPI wheel.
RUN python -m pip install --upgrade pip \
    && if [ "$INCLUDE_TORCH" = "true" ]; then \
         pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.2,<3.0"; \
       else \
         grep -v '^torch' requirements.txt > /tmp/requirements.txt \
         && mv /tmp/requirements.txt requirements.txt; \
       fi \
    && pip install -r requirements.txt

# ---------------------------------------------------------------------------
# runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# libgomp1 is the OpenMP runtime LightGBM, XGBoost and scikit-learn link
# against; without it the imports fail at startup. curl serves the healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PROJECT_ROOT=/app \
    MODEL_DIR=/app/data/models/district_v2 \
    MODEL_VERSION=district_v2 \
    ENVIRONMENT=production \
    PORT=8080 \
    WEB_CONCURRENCY=1

WORKDIR /app

# Only what the service reads at run time. The feature CSVs, notebooks and
# weather cache stay out of the image (see .dockerignore): the pipeline carries
# its own fitted transformers, so it scores requests without them.
COPY api/ /app/api/
COPY scripts/district_model.py scripts/district_nn.py scripts/fetch_model_artifact.py /app/scripts/
COPY data/api/ /app/data/api/

# Optional: bake the artefact into the image. Leave empty to supply it at run
# time instead. Railway passes a service variable of the same name as a build
# argument when the Dockerfile declares it.
ARG MODEL_URL=""
RUN mkdir -p "$MODEL_DIR" \
    && if [ -n "$MODEL_URL" ]; then \
         echo "baking model artefact from $MODEL_URL" \
         && python /app/scripts/fetch_model_artifact.py --url "$MODEL_URL" --dest "$MODEL_DIR" \
         && ls -lh "$MODEL_DIR"; \
       else \
         echo "no MODEL_URL at build time; the artefact must arrive via MODEL_URL or MODEL_DIR at run time"; \
       fi \
    && chown -R appuser:appuser /app

USER appuser

# 8080 is the port Railway targets when a service has no explicit PORT variable.
# The server still binds whatever $PORT the platform injects; this only decides
# what it falls back to, and it must agree with EXPOSE or the platform's proxy
# dials a port nothing is listening on ("connection dial timeout").
EXPOSE 8080

# /health reports liveness and stays 200 while the model is still loading, so a
# platform healthcheck never kills a container that is merely warming up.
# /ready is the stricter gate and requires the artefact.
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8080}/health" || exit 1

# Shell form so the platform's injected $PORT is expanded. One worker by
# default: each worker loads its own copy of the pipeline, about 700 MB resident.
#
# Do NOT duplicate this as a `startCommand` in railway.json. That overrides the
# CMD, and a start command is not guaranteed to be evaluated by a shell -- if it
# is not, uvicorn receives the literal string "$PORT", exits immediately, and
# the platform reports "Application failed to respond" with nothing listening.
# The echo puts the resolved port in the deploy logs, which is the first thing
# to check when a platform cannot reach the container.
CMD ["sh", "-c", "echo \"starting uvicorn on 0.0.0.0:${PORT:-8080} (workers=${WEB_CONCURRENCY:-1})\" && exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers ${WEB_CONCURRENCY:-1} --timeout-keep-alive 65 --proxy-headers --forwarded-allow-ips '*'"]
