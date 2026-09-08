import { useState } from 'react'
import { Banner, Card, ErrorState, Field, Loading, Pill, SeasonBars, Stat } from '../components/ui'
import { IconRefresh } from '../components/Icons'
import { api, defaultBaseUrl, getBaseUrl, setBaseUrl } from '../lib/api'
import { useReference } from '../lib/hooks'
import { kg, titleCase } from '../lib/format'

/** The interval model the API reports: sd(n) = sqrt(a + b/n). */
const intervalSd = (interval, n) =>
  interval?.a == null ? null : Math.sqrt(interval.a + (interval.b || 0) / n)

/** The model's own account of itself: what it was trained on, how it scores, what it cannot do. */
export default function Model() {
  const { data, error, loading, stale, reload } = useReference('model-summary', api.modelSummary)
  const [base, setBase] = useState(getBaseUrl())
  const [saved, setSaved] = useState(false)

  const applyBase = (event) => {
    event.preventDefault()
    setBaseUrl(base.trim().replace(/\/$/, ''))
    setSaved(true)
    setTimeout(() => window.location.reload(), 400)
  }

  return (
    <div className="stack">
      <header>
        <span className="eyebrow">Model</span>
        <h1>What is behind the numbers</h1>
        <p className="lede">
          Everything on this page is read live from the API's own summary endpoint, so it describes the
          model that is actually deployed rather than the one that was documented.
        </p>
      </header>

      {loading && <Loading label="Reading model summary" />}
      <ErrorState error={error} onRetry={reload} />
      {stale && !error && <Banner tone="warn">Showing a cached copy — the API could not be reached.</Banner>}

      {data && (
        <>
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <div className="chip-row">
              <Pill tone={data.status === 'ready' ? 'pill-leaf' : 'pill-maize'}>{titleCase(data.status)}</Pill>
              <Pill tone="pill-rain">{data.model_version}</Pill>
              {data.ensemble_members?.map((m) => <Pill key={m}>{m}</Pill>)}
            </div>
            <button className="btn btn-ghost btn-sm" onClick={reload}>
              <IconRefresh width={14} height={14} /> Refresh
            </button>
          </div>

          <div className="grid grid-3">
            <Card><Stat label="Target" value="District mean" unit="kg/ha" tone="maize" /></Card>
            <Card>
              <Stat label="Training plots" value={kg(data.n_training_plots)} />
              <p className="tiny" style={{ margin: '6px 0 0' }}>
                Seasons {data.train_seasons?.join(', ') || '—'}
              </p>
            </Card>
            <Card>
              <Stat label="Reporting threshold" value={data.min_plots_for_reporting ?? '—'} unit="plots" />
              <p className="tiny" style={{ margin: '6px 0 0' }}>
                Districts with fewer plots are flagged, not hidden.
              </p>
            </Card>
          </div>

          {data.validation?.length > 0 && (
            <Card title="Out-of-time accuracy"
              aside={<span className="tiny">fitted on prior seasons only</span>}>
              <SeasonBars seasons={data.validation} />
              <div className="table-wrap" style={{ marginTop: 16 }}>
                <table>
                  <thead>
                    <tr>
                      <th>Season</th><th className="num">Districts</th><th className="num">R²</th>
                      <th className="num">MAE</th><th className="num">RMSE</th><th className="num">corr</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.validation.map((s) => (
                      <tr key={s.season}>
                        <td className="mono">{s.season}</td>
                        <td className="num">{s.n_districts}</td>
                        <td className="num">{s.r2?.toFixed(3)}</td>
                        <td className="num">{kg(s.mae_kg_ph)}</td>
                        <td className="num">{kg(s.rmse_kg_ph)}</td>
                        <td className="num">{s.corr?.toFixed(3)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {data.expected_performance && (
                <p className="small" style={{ margin: '14px 0 0' }}>
                  Expect R² between {data.expected_performance.r2_range?.[0]} and{' '}
                  {data.expected_performance.r2_range?.[1]}, mean error{' '}
                  {kg(data.expected_performance.mae_range_kg_ph?.[0])}–
                  {kg(data.expected_performance.mae_range_kg_ph?.[1])} kg/ha on a new season. Which
                  season you are asked to predict matters more than which model is used.
                </p>
              )}
            </Card>
          )}

          {data.interval && (
            <Card title="Prediction intervals">
              <div className="grid grid-3">
                <Stat label="Nominal coverage"
                  value={`${Math.round((data.interval.coverage_target ?? 0.8) * 100)}%`} />
                <Stat label="Error sd at 50 plots" value={kg(intervalSd(data.interval, 50))} unit="kg/ha" />
                <Stat label="At 200 plots" value={kg(intervalSd(data.interval, 200))} unit="kg/ha" tone="leaf" />
              </div>
              <p className="small" style={{ margin: '14px 0 0' }}>
                {data.interval.description ||
                  'District-mean error is modelled as sd(n) = sqrt(a + b/n), so districts backed by fewer plots get wider bands.'}
              </p>
            </Card>
          )}

          {data.limitations?.length > 0 && (
            <Card title="Stated limits">
              <ul className="small" style={{ margin: 0, paddingLeft: '1.1rem' }}>
                {data.limitations.map((l, i) => <li key={i} style={{ marginBottom: 6 }}>{l}</li>)}
              </ul>
            </Card>
          )}

          {data.artefact && (
            <Card title="Deployed artefact">
              <div className="table-wrap">
                <table>
                  <tbody>
                    {Object.entries(data.artefact).map(([k, v]) => (
                      <tr key={k}>
                        <td className="tiny">{titleCase(k)}</td>
                        <td className="mono tiny" style={{ whiteSpace: 'normal' }}>
                          {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </>
      )}

      <Card title="API endpoint">
        <form onSubmit={applyBase} className="stack" style={{ gap: 10 }}>
          <Field label="Base URL" id="base" hint={`default: ${defaultBaseUrl}`}>
            <input id="base" type="url" value={base} onChange={(e) => setBase(e.target.value)}
              placeholder={defaultBaseUrl} />
          </Field>
          <div className="row">
            <button className="btn btn-sm" type="submit">{saved ? 'Saved — reloading…' : 'Use this API'}</button>
            <button className="btn btn-ghost btn-sm" type="button"
              onClick={() => { setBaseUrl(null); setBase(defaultBaseUrl); window.location.reload() }}>
              Reset
            </button>
          </div>
          <p className="tiny" style={{ margin: 0 }}>
            Stored on this device only. Useful for pointing the same build at a staging API.
          </p>
        </form>
      </Card>
    </div>
  )
}
