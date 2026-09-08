import { useCallback, useState } from 'react'
import PlotForm, { EMPTY_PLOT, toApiPlot } from '../components/PlotForm'
import { Banner, Card, ErrorState, IntervalBar, Loading, Pill, Stat } from '../components/ui'
import { IconForecast, IconPlus, IconTrash } from '../components/Icons'
import { api } from '../lib/api'
import { useAction, useReference } from '../lib/hooks'
import { districtName, kg } from '../lib/format'

/**
 * Forecast: build a batch of plots, score them, read the district means.
 *
 * The district card is the headline because that is the unit the model was
 * validated on. Plot-level numbers are available underneath, labelled with the
 * warning the API itself returns.
 */
export default function Forecast() {
  const { data: districtsData } = useReference('districts', api.districts)
  const { data: schema } = useReference('input-schema', api.inputSchema)

  const [draft, setDraft] = useState({ ...EMPTY_PLOT })
  const [batch, setBatch] = useState([])
  const [showPlots, setShowPlots] = useState(false)

  const predict = useAction(
    useCallback((plots, year) => api.predict(plots, { year, includePlots: true }), []),
  )

  const districts = districtsData?.districts || []
  const seasons = districtsData?.seasons || []
  const canSubmit = Boolean(draft.district) || batch.length > 0

  const addToBatch = () => {
    if (!draft.district) return
    setBatch((b) => [...b, { ...draft, plot_id: `plot-${b.length + 1}` }])
    setDraft({ ...EMPTY_PLOT, district: draft.district, year: draft.year })
  }

  const run = async (event) => {
    event.preventDefault()
    const plots = batch.length ? batch : [draft]
    const year = Number(plots[0].year) || undefined
    await predict.run(plots.map(toApiPlot), year)
  }

  const result = predict.data

  return (
    <div className="stack">
      <header>
        <span className="eyebrow">Forecast</span>
        <h1>District yield forecast</h1>
        <p className="lede">
          Describe one plot or a batch of them. Everything except the district is optional — anything
          you leave blank is filled from that district's own history, so a sparse request still scores.
        </p>
      </header>

      <form onSubmit={run} className="stack">
        <Card title="Plot details"
          aside={<Pill tone={draft.district ? 'pill-leaf' : ''}>{draft.district ? districtName(draft.district) : 'district required'}</Pill>}>
          <PlotForm plot={draft} onChange={setDraft} districts={districts} schema={schema} seasons={seasons} />

          <div className="row" style={{ marginTop: 18, justifyContent: 'space-between' }}>
            <button type="button" className="btn btn-sm" onClick={addToBatch} disabled={!draft.district}>
              <IconPlus width={15} height={15} /> Add to batch
            </button>
            <button type="submit" className="btn btn-primary" disabled={!canSubmit || predict.pending}>
              {predict.pending ? <span className="spinner" /> : <IconForecast width={16} height={16} />}
              {predict.pending ? 'Scoring…' : batch.length ? `Forecast ${batch.length} plots` : 'Forecast this plot'}
            </button>
          </div>
        </Card>

        {batch.length > 0 && (
          <Card title={`Batch · ${batch.length} plot${batch.length > 1 ? 's' : ''}`}
            aside={<button type="button" className="btn btn-ghost btn-sm" onClick={() => setBatch([])}>Clear</button>}>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Plot</th><th>District</th><th>Season</th><th>Seed</th>
                    <th className="num">DAP</th><th className="num">CAN</th><th />
                  </tr>
                </thead>
                <tbody>
                  {batch.map((p, i) => (
                    <tr key={p.plot_id}>
                      <td className="mono">{p.plot_id}</td>
                      <td>{districtName(p.district)}</td>
                      <td className="num">{p.year}</td>
                      <td>{p.seed_category ? p.seed_category.replace(/_/g, ' ') : '—'}</td>
                      <td className="num">{p.dap_kg_ph || '—'}</td>
                      <td className="num">{p.can_kg_ph || '—'}</td>
                      <td>
                        <button type="button" className="btn btn-ghost btn-sm"
                          aria-label={`Remove ${p.plot_id}`}
                          onClick={() => setBatch((b) => b.filter((_, j) => j !== i))}>
                          <IconTrash width={14} height={14} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </form>

      <ErrorState error={predict.error} />
      {predict.pending && <Loading label="Running the ensemble" />}

      {result && (
        <section className="stack">
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <h2>Result</h2>
            <div className="chip-row">
              <Pill tone="pill-rain">{result.model_version}</Pill>
              <Pill>{result.n_plots_scored} plot{result.n_plots_scored > 1 ? 's' : ''} scored</Pill>
            </div>
          </div>

          <div className="grid grid-2">
            {result.districts.map((d) => (
              <Card key={`${d.district}-${d.year}`}
                title={districtName(d.district)}
                aside={<Pill tone={d.district_known ? 'pill-leaf' : 'pill-maize'}
                  title={d.district_known ? 'This district is in the training data.'
                    : 'This district was not in the training data; the level estimate is weaker.'}>
                  {d.district_known ? 'known district' : 'unseen district'}
                </Pill>}>
                <Stat label={`Predicted mean · ${d.year ?? 'season'}`}
                  value={kg(d.predicted_mean_yield_kg_ph)} unit="kg/ha" tone="maize" />
                <div style={{ marginTop: 16 }}>
                  <IntervalBar low={d.interval_low_kg_ph} high={d.interval_high_kg_ph}
                    point={d.predicted_mean_yield_kg_ph} coverage={d.interval_coverage} />
                </div>
                {d.below_reporting_threshold && (
                  <p className="tiny" style={{ marginTop: 12 }}>
                    Based on {d.n_plots} plot{d.n_plots > 1 ? 's' : ''} — below the {'≥'}20 the model
                    reports on. Treat this as indicative; a district mean needs more plots to be stable.
                  </p>
                )}
              </Card>
            ))}
          </div>

          {result.accuracy_note && (
            <Banner tone="info" title="Accuracy.">{result.accuracy_note}</Banner>
          )}
          {result.warnings?.map((w, i) => (
            <Banner key={i} tone="warn">{w}</Banner>
          ))}

          {result.plots?.length > 0 && (
            <Card title="Per-plot estimates"
              aside={<button className="btn btn-ghost btn-sm" onClick={() => setShowPlots((s) => !s)}>
                {showPlots ? 'Hide' : 'Show'}
              </button>}>
              <p className="small" style={{ marginTop: 0 }}>
                Plot-level predictions score about R² 0.16 — far weaker than the district means above,
                and deliberately shrunk toward the mean. Use them for ranking plots, not for telling a
                farmer what their field will produce.
              </p>
              {showPlots && (
                <div className="table-wrap" style={{ marginTop: 12 }}>
                  <table>
                    <thead>
                      <tr>
                        <th>Plot</th><th>District</th>
                        <th className="num">Predicted</th><th className="num">Inputs used</th><th>Notes</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.plots.map((p) => (
                        <tr key={p.index}>
                          <td className="mono">{p.plot_id || `#${p.index + 1}`}</td>
                          <td>{districtName(p.district)}</td>
                          <td className="num">{kg(p.predicted_yield_kg_ph)}</td>
                          <td className="num">{p.inputs_supplied_count}</td>
                          <td className="tiny" style={{ whiteSpace: 'normal', maxWidth: 320 }}>
                            {p.warnings?.length ? p.warnings[0] : p.used_season_weather ? 'observed weather' : 'climatology'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          )}
        </section>
      )}
    </div>
  )
}
