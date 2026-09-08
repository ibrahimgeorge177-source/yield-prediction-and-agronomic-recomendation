import { useCallback, useState } from 'react'
import PlotForm, { EMPTY_PLOT, toApiPlot } from '../components/PlotForm'
import { Banner, Card, CurveChart, ErrorState, Loading, Pill, Stat } from '../components/ui'
import { IconAdvisor, IconArrow } from '../components/Icons'
import { api } from '../lib/api'
import { useAction, useReference } from '../lib/hooks'
import { confidenceTone, districtName, kg, signed, titleCase } from '../lib/format'

/**
 * Advisor: one plot in, a ranked list of changes out.
 *
 * Each card leads with the expected lift because that is the decision, and
 * carries its interval and evidence beside it because a lift without either is
 * not actionable. Lever effects are associations from fixed-effects curves —
 * the page says so, at the top and at the bottom.
 */
function LeverCard({ rec }) {
  const curve = rec.curve?.map((p) => ({ x: p[0] ?? p.x, y: p[1] ?? p.y })) || null
  const ev = rec.evidence || {}

  return (
    <Card>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div className="row" style={{ gap: 8 }}>
            <span className="mono" style={{ color: 'var(--maize)' }}>{String(rec.rank).padStart(2, '0')}</span>
            <h3>{rec.label}</h3>
          </div>
          <p className="small" style={{ margin: '8px 0 0' }}>{rec.action}</p>
        </div>
        <Pill tone={confidenceTone(ev.confidence)} title={`t = ${ev.t_statistic?.toFixed(1)} · ${ev.n_plots} plots`}>
          {ev.confidence} confidence
        </Pill>
      </div>

      <div className="grid grid-3" style={{ marginTop: 18 }}>
        <Stat label="Expected lift" value={signed(rec.expected_lift_kg_ph)} unit="kg/ha" tone="leaf" />
        <div className="stat">
          <span className="eyebrow">Range</span>
          <span className="mono" style={{ fontSize: '0.95rem' }}>
            {signed(rec.lift_low_kg_ph)} … {signed(rec.lift_high_kg_ph)}
          </span>
          <span className="tiny">district-clustered</span>
        </div>
        <div className="stat">
          <span className="eyebrow">Change</span>
          <span className="mono" style={{ fontSize: '0.95rem' }}>
            {formatValue(rec.current_value, rec.unit)} <IconArrow width={13} height={13} style={{ verticalAlign: 'middle', color: 'var(--maize)' }} />{' '}
            {formatValue(rec.recommended_value, rec.unit)}
          </span>
          {rec.current_is_assumed && <span className="tiny">current value assumed from district</span>}
        </div>
      </div>

      {curve?.length > 1 && (
        <div style={{ marginTop: 16 }}>
          <CurveChart points={curve} xLabel={rec.unit || rec.lever} yLabel="kg/ha"
            current={numeric(rec.current_value)} recommended={numeric(rec.recommended_value)} />
        </div>
      )}

      <p className="small" style={{ margin: '14px 0 0' }}>{rec.why}</p>

      <div className="chip-row" style={{ marginTop: 12 }}>
        <Pill title="Plots behind this curve">{kg(ev.n_plots)} plots</Pill>
        <Pill title="Districts represented">{ev.n_districts} districts</Pill>
        <Pill title="Plots already at or beyond the recommended value">
          {kg(ev.support_at_target)} at target
        </Pill>
        <Pill tone={ev.agronomically_plausible ? 'pill-leaf' : 'pill-danger'}>
          {ev.agronomically_plausible ? 'mechanism plausible' : 'mechanism unclear'}
        </Pill>
        <Pill>{titleCase(ev.curve_shape || '')}</Pill>
      </div>
    </Card>
  )
}

const numeric = (v) => (typeof v === 'number' ? v : null)

function formatValue(v, unit) {
  if (v == null) return '—'
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  if (typeof v === 'number') return `${kg(v, 1)}${unit ? ` ${unit}` : ''}`
  return titleCase(String(v))
}

export default function Advisor() {
  const { data: districtsData } = useReference('districts', api.districts)
  const { data: schema } = useReference('input-schema', api.inputSchema)
  const { data: leverIndex } = useReference('levers', api.levers)

  const [plot, setPlot] = useState({ ...EMPTY_PLOT })
  const advise = useAction(useCallback((p, year) => api.recommend(p, { year }), []))
  const result = advise.data

  const districts = districtsData?.districts || []
  const seasons = districtsData?.seasons || []

  const run = async (event) => {
    event.preventDefault()
    await advise.run(toApiPlot(plot), Number(plot.year) || undefined)
  }

  return (
    <div className="stack">
      <header>
        <span className="eyebrow">Advisor</span>
        <h1>What would raise this plot's yield?</h1>
        <p className="lede">
          The advisor ranks the levers a farmer actually controls — planting date, DAP, CAN topdress,
          hybrid seed share, compost, lime, intercropping — by the yield each is expected to add, with
          the evidence behind it. Everything is in kg/ha; this survey carries no prices.
        </p>
      </header>

      <form onSubmit={run}>
        <Card title="The plot as it is today"
          aside={leverIndex && <Pill tone="pill-rain">{leverIndex.levers?.length} levers fitted</Pill>}>
          <PlotForm plot={plot} onChange={setPlot} districts={districts} schema={schema} seasons={seasons} />
          <div className="row" style={{ marginTop: 18, justifyContent: 'flex-end' }}>
            <button type="submit" className="btn btn-primary" disabled={!plot.district || advise.pending}>
              {advise.pending ? <span className="spinner" /> : <IconAdvisor width={16} height={16} />}
              {advise.pending ? 'Ranking levers…' : 'Rank the options'}
            </button>
          </div>
        </Card>
      </form>

      <ErrorState error={advise.error} />
      {advise.pending && <Loading label="Fitting this plot against the response curves" />}

      {result && (
        <section className="stack">
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <h2>{districtName(result.district)} · {result.year ?? 'season'}</h2>
            <div className="chip-row">
              {!result.district_known && <Pill tone="pill-maize">district not in training data</Pill>}
              <Pill>curves {String(result.curves_version).slice(0, 10)}</Pill>
            </div>
          </div>

          <Card title="If every recommendation is adopted">
            <div className="grid grid-3">
              <Stat label="Baseline" value={kg(result.baseline_predicted_yield_kg_ph)} unit="kg/ha" />
              <Stat label="Total expected lift" value={signed(result.bundle?.total_expected_lift_kg_ph)}
                unit="kg/ha" tone="leaf" />
              <Stat label="Projected" value={kg(result.projected_yield_kg_ph)} unit="kg/ha" tone="maize" />
            </div>
            <p className="small" style={{ margin: '14px 0 0' }}>{result.bundle?.note}</p>
          </Card>

          {result.recommendations?.length ? (
            <div className="stack">
              {result.recommendations.map((rec) => (
                <LeverCard key={rec.lever} rec={rec} />
              ))}
            </div>
          ) : (
            <Card>
              <p className="small" style={{ margin: 0 }}>
                No lever cleared the evidence threshold for this plot. That is a real answer: with the
                inputs given, nothing in the survey shows a reliable gain.
              </p>
            </Card>
          )}

          {result.skipped?.length > 0 && (
            <Card title="Considered and set aside">
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Lever</th><th>Why it was skipped</th></tr></thead>
                  <tbody>
                    {result.skipped.map((s) => (
                      <tr key={s.lever}>
                        <td>{titleCase(s.lever)}</td>
                        <td className="tiny" style={{ whiteSpace: 'normal' }}>{s.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}

          <Banner tone="warn" title="Read this before acting.">
            {result.causal_note}
          </Banner>
          <p className="tiny">{result.method_note}</p>
        </section>
      )}
    </div>
  )
}
