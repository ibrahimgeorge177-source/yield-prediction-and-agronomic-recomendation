# Kenya Maize Yield — progressive web app

React front end for the district-level maize yield prediction API. Installable, works offline
for everything that does not need the model, and reads its own field definitions from the API
so the form and the server never drift apart.

```bash
npm install
npm run dev        # http://localhost:5173
npm run build      # → dist/
```

## Pages

| Route | What it is |
|---|---|
| `/` | Project summary, headline accuracy read live from the API, how the pipeline works, contributors |
| `/forecast` | Score one plot or a batch; district mean with its interval, per-plot estimates behind a disclosure |
| `/advisor` | Rank the levers available to one plot by expected yield gain, with evidence and response curves |
| `/model` | The deployed model's own summary: seasons, per-season accuracy, interval calibration, stated limits, API endpoint override |
| `/docs` | How the application works, the API surface, offline behaviour, deployment, glossary |

## Configuration

| Variable | When | Default |
|---|---|---|
| `VITE_API_BASE_URL` | build time | `https://web-production-7dae9.up.railway.app` |
| `PORT` | run time (container) | `8080` |

The API base URL can also be overridden per device on the Model page; it is stored in
`localStorage` and survives reloads, which is convenient for pointing one build at a staging API.

## Deploying to Railway

Add a **second service** on the same repository:

1. New service → GitHub repo → this repository.
2. Settings → **Root Directory**: `frontend`.
3. `frontend/railway.json` selects the Dockerfile builder; nothing else to configure.
4. Optionally set `VITE_API_BASE_URL` as a service variable — it is declared as a build
   argument, so Railway passes it into the build.

The image is a static bundle behind nginx. `${PORT}` is substituted into the nginx config at
container start by the official image's envsubst step, so it binds whatever Railway injects.

```bash
docker build -t maize-pwa frontend
docker run -p 8080:8080 -e PORT=8080 maize-pwa
```

## Offline behaviour

The service worker (vite-plugin-pwa, `generateSW`) precaches the app shell and caches reference
endpoints — districts, seed types, input schema, model summary — stale-while-revalidate for a
week. Forecasts and recommendations are computed server side and need a connection; requests made
offline fail with a message that says so rather than hanging.

## Structure

```
src/
  lib/api.js        one client for every endpoint, with typed errors
  lib/hooks.js      cached reference data, actions, online state, theme
  lib/format.js     every number the user sees passes through here
  components/       shell, plot form, and the small UI kit (cards, stats, interval bar, charts)
  pages/            one file per route
  styles/global.css design tokens and layout; no UI framework
```

Charts are hand-drawn SVG — the response curve on the advisor and the season bars on the model
page — so the bundle carries no charting library.
