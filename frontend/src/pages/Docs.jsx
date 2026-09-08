import { useState } from 'react'
import { Card, Pill } from '../components/ui'
import { getBaseUrl } from '../lib/api'

/**
 * Documentation: how the application works, end to end.
 *
 * Written for three readers at once — someone using the app, someone calling
 * the API directly, and someone deploying it — with a contents rail so each can
 * skip to their part.
 */

const SECTIONS = [
  { id: 'what', label: 'What it does' },
  { id: 'using', label: 'Using the app' },
  { id: 'reading', label: 'Reading a result' },
  { id: 'api', label: 'The API' },
  { id: 'offline', label: 'Offline & install' },
  { id: 'stack', label: 'How it is built' },
  { id: 'deploy', label: 'Deploying' },
  { id: 'glossary', label: 'Glossary' },
]

function Endpoint({ method, path, children }) {
  return (
    <div style={{ borderTop: '1px solid var(--line)', padding: '12px 0' }}>
      <div className="row" style={{ gap: 10 }}>
        <span className={`pill ${method === 'POST' ? 'pill-maize' : 'pill-rain'}`}>{method}</span>
        <code style={{ fontSize: '0.85rem' }}>{path}</code>
      </div>
      <p className="small" style={{ margin: '8px 0 0' }}>{children}</p>
    </div>
  )
}

function Code({ children }) {
  return (
    <pre style={{
      background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius-s)',
      padding: '12px 14px', overflowX: 'auto', fontSize: '0.8rem', fontFamily: 'var(--font-mono)',
      lineHeight: 1.55, margin: '12px 0 0',
    }}>
      <code>{children}</code>
    </pre>
  )
}

export default function Docs() {
  const [active, setActive] = useState('what')
  const base = getBaseUrl()

  return (
    <div className="stack">
      <header>
        <span className="eyebrow">Documentation</span>
        <h1>How this application works</h1>
        <p className="lede">
          A yield model served over HTTP, and a progressive web app that calls it. This page covers
          both: what the app does, how to read what it returns, and how to call or deploy it yourself.
        </p>
      </header>

      <nav className="chip-row" aria-label="Contents">
        {SECTIONS.map((s) => (
          <a key={s.id} href={`#${s.id}`} onClick={() => setActive(s.id)}
            className={`pill ${active === s.id ? 'pill-maize' : ''}`}>
            {s.label}
          </a>
        ))}
      </nav>

      <section id="what">
        <Card title="What it does">
          <p className="small">
            The app answers two questions against a model trained on the One Acre Fund MEL Agronomic
            Survey (23,674 plots, 2016–2020) joined to satellite rainfall and temperature:
          </p>
          <div className="grid grid-2" style={{ marginTop: 12 }}>
            <div>
              <h3>Forecast</h3>
              <p className="small">
                “What mean yield should this district expect this season?” You describe one or more
                plots; the model scores each and averages them within a district, returning a mean in
                kg/ha with an interval and the number of plots behind it.
              </p>
            </div>
            <div>
              <h3>Advisor</h3>
              <p className="small">
                “What should this farmer change first?” For a single plot, the API ranks the levers a
                farmer controls by expected yield gain, each with an uncertainty range and the evidence
                behind it.
              </p>
            </div>
          </div>
        </Card>
      </section>

      <section id="using" className="section">
        <Card title="Using the app">
          <ol className="small" style={{ paddingLeft: '1.1rem', margin: 0 }}>
            <li style={{ marginBottom: 10 }}>
              <strong>Pick a district.</strong> It is the only required field — the model cannot infer
              location, but it can infer everything else from that district's history.
            </li>
            <li style={{ marginBottom: 10 }}>
              <strong>Fill in only what you know.</strong> Every blank field is filled from the
              district's own median. A request with just a district is valid and will score.
            </li>
            <li style={{ marginBottom: 10 }}>
              <strong>Add plots to a batch</strong> (Forecast) when you want a district mean built from
              several fields. More plots make the district estimate steadier; below 20 the result is
              flagged.
            </li>
            <li>
              <strong>Read the interval, not just the number.</strong> A prediction whose band spans
              1,500 kg/ha is telling you something real about how much the model knows.
            </li>
          </ol>
        </Card>
      </section>

      <section id="reading" className="section">
        <Card title="Reading a result">
          <div className="table-wrap">
            <table>
              <thead><tr><th>What you see</th><th>What it means</th></tr></thead>
              <tbody>
                <tr>
                  <td>Predicted mean</td>
                  <td className="small" style={{ whiteSpace: 'normal' }}>
                    The district-season mean yield in kg/ha. This is the quantity the model was
                    validated on.
                  </td>
                </tr>
                <tr>
                  <td>Interval</td>
                  <td className="small" style={{ whiteSpace: 'normal' }}>
                    An 80% band whose width is fitted on validation seasons and narrows as more plots
                    back the district.
                  </td>
                </tr>
                <tr>
                  <td>Unseen district</td>
                  <td className="small" style={{ whiteSpace: 'normal' }}>
                    That district was not in the training data. Ranking still works; the level is
                    roughly twice as uncertain.
                  </td>
                </tr>
                <tr>
                  <td>Per-plot estimate</td>
                  <td className="small" style={{ whiteSpace: 'normal' }}>
                    R² ≈ 0.16 and deliberately shrunk toward the mean. Useful for ordering plots, not
                    for a per-farm promise.
                  </td>
                </tr>
                <tr>
                  <td>Expected lift</td>
                  <td className="small" style={{ whiteSpace: 'normal' }}>
                    Yield a lever is associated with adding, from a fixed-effects response curve with
                    district and season absorbed — not a randomised effect.
                  </td>
                </tr>
                <tr>
                  <td>Confidence</td>
                  <td className="small" style={{ whiteSpace: 'normal' }}>
                    Derived from the t-statistic, plots behind the curve, and how many plots already
                    sit at the recommended value.
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </Card>
      </section>

      <section id="api" className="section">
        <Card title="The API" aside={<Pill tone="pill-rain">{base.replace(/^https?:\/\//, '')}</Pill>}>
          <p className="small">
            The app is a thin client — every number it shows comes from these endpoints, and they are
            open, so you can call them directly. Interactive docs live at <code>/docs</code> on the API.
          </p>
          <Endpoint method="POST" path="/api/v1/predict">
            Score plots and get district means. Body: <code>{'{ plots: [{ district, ... }], year }'}</code>.
          </Endpoint>
          <Endpoint method="POST" path="/api/v1/recommend">
            Rank levers for one plot. Body: <code>{'{ plot: { district, ... }, year }'}</code>.
          </Endpoint>
          <Endpoint method="GET" path="/api/v1/model/summary">
            Training seasons, per-season accuracy, interval calibration and stated limits.
          </Endpoint>
          <Endpoint method="GET" path="/api/v1/reference/districts">
            The 51 districts the model knows, and the seasons available.
          </Endpoint>
          <Endpoint method="GET" path="/api/v1/reference/input-schema">
            Every accepted field with its allowed values and observed range — this app builds its form
            from it, so the two never drift apart.
          </Endpoint>
          <Endpoint method="GET" path="/api/v1/reference/levers">
            The levers the advisor can rank, with the curve shape fitted for each.
          </Endpoint>
          <Endpoint method="GET" path="/health">
            Liveness. Returns 200 while the model is still loading, so it is safe as a platform health
            check; <code>/ready</code> is the stricter gate.
          </Endpoint>

          <Code>{`curl -X POST ${base}/api/v1/predict \\
  -H 'Content-Type: application/json' \\
  -d '{"plots":[{"district":"kabiyet","year":2021,
                 "dap_kg_ph":60,"can_kg_ph":40,
                 "seed_category":"hybrid_branded"}]}'`}</Code>
        </Card>
      </section>

      <section id="offline" className="section">
        <Card title="Offline & install">
          <p className="small">
            This is a progressive web app. Your browser will offer to install it; on Android use
            “Add to home screen”, on iOS Share → “Add to Home Screen”, on desktop Chrome or Edge the
            install icon in the address bar.
          </p>
          <p className="small">
            <strong>What works offline:</strong> the app shell, the district and seed vocabularies, the
            input schema and the last model summary — all cached on first visit and refreshed in the
            background.
          </p>
          <p className="small" style={{ margin: 0 }}>
            <strong>What does not:</strong> forecasts and recommendations, which are computed server
            side. Requests made offline fail with a message saying so rather than hanging.
          </p>
        </Card>
      </section>

      <section id="stack" className="section">
        <Card title="How it is built">
          <div className="grid grid-2">
            <div>
              <h3>This app</h3>
              <ul className="small" style={{ paddingLeft: '1.1rem' }}>
                <li>React 18 with JSX, built by Vite</li>
                <li>React Router for five routes, no state library — server data is the state</li>
                <li>Service worker via vite-plugin-pwa; reference data is stale-while-revalidate</li>
                <li>Hand-written CSS tokens, no UI framework; SVG charts drawn inline</li>
              </ul>
            </div>
            <div>
              <h3>Behind the API</h3>
              <ul className="small" style={{ paddingLeft: '1.1rem' }}>
                <li>FastAPI serving a fitted scikit-learn / LightGBM / XGBoost / PyTorch ensemble</li>
                <li>Predictions averaged to district level with a fitted interval model</li>
                <li>Recommendations from fixed-effects response curves, not from inverting the model</li>
                <li>Docker image on Railway; the model artefact is fetched from a GitHub release</li>
              </ul>
            </div>
          </div>
        </Card>
      </section>

      <section id="deploy" className="section">
        <Card title="Deploying this front end">
          <p className="small">
            The app is a static bundle behind nginx, built by the Dockerfile in <code>frontend/</code>.
            On Railway, add a second service pointed at this repository with the root directory set to{' '}
            <code>frontend</code>; <code>frontend/railway.json</code> selects the Dockerfile builder.
          </p>
          <Code>{`# local
npm install
npm run dev            # http://localhost:5173

# container
docker build -t maize-pwa frontend
docker run -p 8080:8080 -e PORT=8080 maize-pwa`}</Code>
          <p className="small" style={{ marginTop: 12 }}>
            Set <code>VITE_API_BASE_URL</code> at build time to point the bundle at a different API.
            You can also override it per device on the Model page, which stores it in localStorage.
          </p>
        </Card>
      </section>

      <section id="glossary" className="section">
        <Card title="Glossary">
          <div className="table-wrap">
            <table>
              <tbody>
                {[
                  ['kg/ha', 'Kilograms per hectare — the yield unit used everywhere in this project.'],
                  ['R²', 'Share of variation explained. 0 means no better than predicting the average; district-level here runs 0.22–0.59 out-of-time.'],
                  ['MAE', 'Mean absolute error: the typical size of a miss, in kg/ha. Easier to act on than R².'],
                  ['Out-of-time', 'Scored on a season the model never saw during fitting — the honest test for a forecaster.'],
                  ['DAP / CAN', 'Di-ammonium phosphate applied at planting; calcium ammonium nitrate applied as topdress.'],
                  ['Lever', 'An input a farmer controls and could change next season.'],
                  ['Fixed effects', 'District and season differences absorbed before a lever is measured, so the effect is not just "good districts do more of this".'],
                ].map(([term, meaning]) => (
                  <tr key={term}>
                    <td className="mono" style={{ width: 130 }}>{term}</td>
                    <td className="small" style={{ whiteSpace: 'normal' }}>{meaning}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </section>
    </div>
  )
}
