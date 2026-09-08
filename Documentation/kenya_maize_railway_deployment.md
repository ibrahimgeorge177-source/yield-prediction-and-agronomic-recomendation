# Deploying the API to Railway

**Kenya maize yield prediction API — container build, model delivery, and the pipeline
around them.**

This covers the Docker image, how the fitted model reaches a running container, and the
GitHub Actions workflows that build and publish both. For what the API *does*, see
`Documentation/api_reference.md`; for what the model is and how accurate it is, see
`Documentation/kenya_maize_district_model_deployment.md`.

---

## 1 · The one thing that makes this deployment unusual

**The fitted pipeline is not in the repository.** It is 90–125 MB — above GitHub's
per-file limit — so `data/models/**/pipeline.joblib` is gitignored. Everything else the
service needs *is* committed: the two JSON files under `data/api/` (1.1 MB), the model
code in `scripts/`, and each build's `metadata.json`.

So a deployment has to be told where the model is. Three supported routes:

| Route | How | When to use it |
|---|---|---|
| **Baked at build time** | `--build-arg MODEL_URL=https://…/district_v2.tar.gz` | **Recommended.** Self-contained image, no cold-start download, no volume. |
| Fetched at run time | `MODEL_URL` as a service variable | Model changes more often than the code. Re-downloads after every restart. |
| Mounted volume | `MODEL_DIR=/models` + a Railway volume | Large artefacts you do not want in the image, or several services sharing one. |

With none of them the service still **boots and explains itself**: `/health`,
`/api/v1/model/summary`, the reference endpoints and `/api/v1/recommend` all work, and the
prediction endpoints return 503 with the reason. That is deliberate — a deployment missing
its weights should be diagnosable, not a crash loop.

---

## 2 · Sizing — read this before choosing a plan

Measured on the shipped `district_v2` artefact (five families, five configurations each,
neural member included):

| | |
|---|---|
| Resident memory after the model loads | **~720 MB** (143 MB imports + 559 MB artefact) |
| Model load time | ~5 s |
| Image size, torch included | ~1.6 GB |
| Image size, `--build-arg INCLUDE_TORCH=false` | ~700 MB |

**Provision at least 2 GB of memory** for the default artefact, and keep
`WEB_CONCURRENCY=1` — every uvicorn worker loads its own copy of the pipeline, so two
workers means ~1.4 GB before serving a single request. Scale with replicas, not workers.

If you are memory- or size-constrained, build a lighter artefact. Dropping the neural
member removes torch from the image entirely:

```bash
python scripts/train_district_model.py --train 2016-2020 --val 2019,2020 --test none \
    --no-nn --top-configs 3 --out data/models/district_lite
```

That costs roughly 0.01–0.03 R² at district level (see the per-member table in the model
deployment guide) and produces a much smaller artefact. Build the image with
`--build-arg INCLUDE_TORCH=false` to match.

---

## 3 · First deployment

### 3.1 · Publish a model artefact

From the Actions tab, run **Publish model artefact** (`.github/workflows/publish-model-artifact.yml`).
It trains, packages, verifies the archive loads, and attaches it to a GitHub Release. The
run summary prints the `MODEL_URL` to use.

Locally, the same thing:

```bash
python scripts/train_district_model.py --train 2016-2020 --val 2019,2020 --test none \
    --out data/models/district_v2
python scripts/package_model_artifact.py --model-dir data/models/district_v2
gh release create model-v2 dist/district_v2.tar.gz --title "District model v2"
```

A release asset on a **public** repository is directly downloadable, which is all the
container needs. For a private repository, use object storage with a signed URL instead —
`MODEL_URL` is fetched with a plain unauthenticated GET.

### 3.2 · Create the Railway service

1. **New Project → Deploy from GitHub repo**, and pick this repository.
2. Railway detects `railway.json` and builds with the `Dockerfile` (not Nixpacks — the
   `Procfile` is only there for local `honcho`-style runs).
3. Set the service variables in §4.
4. Deploy. The healthcheck at `/health` should pass within a minute or two.

To bake the model into the image, add `MODEL_URL` as a **build** variable — Railway passes
service variables as build arguments where the Dockerfile declares an `ARG` of the same
name, which this one does.

### 3.3 · Verify

```bash
curl -fsS https://<your-service>.up.railway.app/health
curl -fsS https://<your-service>.up.railway.app/ready          # 503 until the model loads
curl -fsS https://<your-service>.up.railway.app/api/v1/model/summary

curl -fsS -X POST https://<your-service>.up.railway.app/api/v1/predict \
  -H 'Content-Type: application/json' \
  -d '{"plots":[{"plot_id":"p1","district":"kabiyet","year":2021,
                 "dap_kg_ph":60,"can_kg_ph":40,"seed_category":"hybrid_branded"}]}'
```

`/health` is the platform healthcheck: it stays 200 while the model is still loading, so
Railway never kills a container that is merely warming up. `/ready` is the stricter gate —
it requires the artefact to be loadable, which makes it the right check for "should this
receive traffic".

---

## 4 · Service variables

| Variable | Default | Notes |
|---|---|---|
| `PORT` | injected by Railway | The start command reads it; do not hard-code it. |
| `MODEL_URL` | — | Artefact archive (`.tar.gz`, `.zip` or bare `.joblib`). Works as a build argument *and* at run time. |
| `MODEL_DIR` | `/app/data/models/district_v2` | Point at a mounted volume to serve from one. |
| `MODEL_VERSION` | `district_v2` | Reported by `/` and `/api/v1/model/summary`. |
| `MODEL_EAGER_LOAD` | `false` | `true` loads the model during startup: slower boot, no 5 s penalty on the first prediction. Sensible once the artefact is baked in. |
| `WEB_CONCURRENCY` | `1` | **Leave at 1** unless you have memory to spare — see §2. |
| `CORS_ORIGINS` | `*` | Comma-separated list. Narrow it before anything public depends on the service. |
| `ENVIRONMENT` | `production` | Reported by `/health`. |
| `LOG_LEVEL` | `INFO` | |
| `MAX_PLOTS_PER_REQUEST` | `500` | Guards the batch endpoint. |
| `MIN_LIFT_KG_PH` / `MIN_LIFT_Z` | `25` / `1.645` | Gates a recommendation must clear before it is offered. |
| `ROOT_PATH` | empty | Set when serving under a path prefix behind a proxy. |

---

## 5 · The pipeline

Two workflows, each doing one job:

**`.github/workflows/api-image.yml`** — on every push to `main` and every PR touching the
API, builds the image and starts it. The smoke test deliberately runs **without** a model
artefact, because that is the state a fresh deployment begins in: it asserts that health,
reference and summary endpoints answer, and that `POST /api/v1/predict` returns 503 with an
actionable message rather than a stack trace. Layer caching is via GitHub's cache backend,
so repeat builds skip the torch download.

**`.github/workflows/publish-model-artifact.yml`** — manual. Trains with the parameters you
give it, packages the archive, verifies it unpacks and loads, and attaches it to a release.

Railway's own GitHub integration handles deployment: it watches the branch and rebuilds on
push. Nothing in CI needs a Railway token for that. If you would rather deploy explicitly
from CI, add `railway up --service <name>` with a `RAILWAY_TOKEN` secret — but the native
integration is fewer moving parts.

**Recommended flow when the model changes:** publish a new artefact → update `MODEL_URL` on
the Railway service → redeploy. When only the code changes, push and let Railway rebuild;
the baked artefact comes from the same `MODEL_URL` build argument and does not need
retraining.

---

## 6 · Local equivalents

```bash
# build (no model baked)
docker build -t maize-api .

# serve, mounting a locally trained artefact read-only
docker run --rm -p 8000:8000 \
  -e MODEL_DIR=/models -e MODEL_EAGER_LOAD=true \
  -v "$PWD/data/models/district_v2:/models:ro" \
  maize-api

# or bake it in, exactly as Railway will
docker build -t maize-api --build-arg MODEL_URL=https://…/district_v2.tar.gz .
```

Without Docker, `uvicorn api.main:app --reload` serves straight from the checkout, which
resolves `data/models/district_v2` on its own.

---

## 7 · Troubleshooting

| Symptom | Cause and fix |
|---|---|
| **`502 "Application failed to respond"` on every path, ~15 s** | Nothing is listening on the port Railway routes to. In order of likelihood: (1) a `startCommand` in `railway.json` or in the service settings is overriding the image `CMD` — remove it and let the Dockerfile run, since a start command is not guaranteed to be shell-evaluated and `$PORT` then arrives at uvicorn as a literal string; (2) the container crashed at boot — read the **Deploy Logs**, not the HTTP logs, and look for the `starting uvicorn on 0.0.0.0:<port>` line the image prints; (3) the process was OOM-killed — see §2 sizing. |
| Deploy logs show `Error: Invalid value for '--port': '$PORT' is not a valid integer` | Exactly the override above. Delete the start command. |
| `503` from `/api/v1/predict`, `/health` says `degraded` | No artefact. `GET /api/v1/model/summary` reports `artefact_dir`; set `MODEL_URL` or fix `MODEL_DIR`. |
| Container OOM-killed shortly after the first prediction | The model needs ~720 MB resident. Raise memory to 2 GB, set `WEB_CONCURRENCY=1`, or build the `--no-nn` artefact. |
| `ModuleNotFoundError: district_model` while loading | The artefact was pickled with `scripts/` importable. The image copies `scripts/district_model.py` and `district_nn.py`; keep both when trimming. |
| `ModuleNotFoundError: torch` while loading | The artefact contains a neural member but the image was built with `INCLUDE_TORCH=false`. Rebuild with torch, or publish a `--no-nn` artefact. |
| `libgomp.so.1: cannot open shared object file` | `libgomp1` missing — the runtime stage installs it; it is required by LightGBM, XGBoost and scikit-learn. |
| Image build pulls gigabytes of NVIDIA packages | torch resolved from PyPI instead of the CPU index. The Dockerfile installs torch first from `download.pytorch.org/whl/cpu`; keep that ordering. |
| Predictions differ from the notebook | Check `pandas` is 2.x. `requirements.txt` pins `<3.0` because the pipeline detects categorical columns with `dtype == object`, which pandas 3 breaks silently. |
| Healthcheck fails during a cold start | Raise `healthcheckTimeout` in `railway.json`, or leave `MODEL_EAGER_LOAD=false` so boot does not wait on the artefact. |
