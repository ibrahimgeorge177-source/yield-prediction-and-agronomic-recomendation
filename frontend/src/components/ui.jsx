import { kg } from '../lib/format'

/* --- primitives ---------------------------------------------------------- */

export function Card({ title, aside, children, className = '', ...rest }) {
  return (
    <section className={`card ${className}`} {...rest}>
      {(title || aside) && (
        <header className="card-head">
          {title && <h3>{title}</h3>}
          {aside}
        </header>
      )}
      {children}
    </section>
  )
}

export function Stat({ label, value, unit, tone }) {
  return (
    <div className="stat">
      <span className="eyebrow">{label}</span>
      <span className="stat-value" style={tone ? { color: `var(--${tone})` } : undefined}>
        {value}
        {unit && <span className="stat-unit"> {unit}</span>}
      </span>
    </div>
  )
}

export function Pill({ tone = '', children, title }) {
  return (
    <span className={`pill ${tone}`} title={title}>
      {children}
    </span>
  )
}

export function Banner({ tone = 'info', title, children }) {
  return (
    <div className={`banner banner-${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>
      <div>
        {title && <strong>{title} </strong>}
        {children}
      </div>
    </div>
  )
}

export function Field({ label, hint, children, id }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  )
}

export function Skeleton({ height = 16, width = '100%', style }) {
  return <div className="skeleton" style={{ height, width, ...style }} />
}

export function Loading({ label = 'Loading' }) {
  return (
    <div className="row" style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>
      <span className="spinner" /> {label}…
    </div>
  )
}

export function ErrorState({ error, onRetry }) {
  if (!error) return null
  return (
    <Banner tone={error.offline ? 'warn' : 'danger'} title={error.offline ? 'Offline.' : 'Error.'}>
      {error.message}
      {onRetry && (
        <>
          {' '}
          <button className="btn btn-ghost btn-sm" onClick={onRetry} style={{ marginTop: 8 }}>
            Try again
          </button>
        </>
      )}
    </Banner>
  )
}

/* --- interval bar --------------------------------------------------------
   A prediction with its uncertainty. The band is the model's interval, the
   marker is the point estimate; the scale is anchored to the band so the
   width is read as "how much this model actually knows".
------------------------------------------------------------------------- */
export function IntervalBar({ low, high, point, coverage = 0.8 }) {
  const span = Math.max(high - low, 1)
  const pad = span * 0.12
  const min = Math.max(0, low - pad)
  const max = high + pad
  const at = (v) => `${((v - min) / (max - min)) * 100}%`

  return (
    <div className="interval">
      <div className="interval-track" role="img"
        aria-label={`Predicted ${kg(point)} kg/ha, ${Math.round(coverage * 100)}% interval ${kg(low)} to ${kg(high)}`}>
        <div className="interval-span" style={{ left: at(low), right: `${100 - parseFloat(at(high))}%` }} />
        <div className="interval-point" style={{ left: at(point) }} />
      </div>
      <div className="interval-scale">
        <span>{kg(low)}</span>
        <span className="tiny">{Math.round(coverage * 100)}% interval · kg/ha</span>
        <span>{kg(high)}</span>
      </div>
    </div>
  )
}

/* --- tiny SVG line chart for lever response curves ---------------------- */
export function CurveChart({ points, xLabel, yLabel, height = 120, current, recommended }) {
  if (!points || points.length < 2) return null
  const xs = points.map((p) => p.x)
  const ys = points.map((p) => p.y)
  const xMin = Math.min(...xs)
  const xMax = Math.max(...xs)
  const yMin = Math.min(...ys)
  const yMax = Math.max(...ys)
  const w = 320
  const h = height
  const padX = 34
  const padY = 16
  const sx = (x) => padX + ((x - xMin) / (xMax - xMin || 1)) * (w - padX - 8)
  const sy = (y) => h - padY - ((y - yMin) / (yMax - yMin || 1)) * (h - padY * 2)
  const d = points.map((p, i) => `${i ? 'L' : 'M'}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(' ')
  const area = `${d} L${sx(xMax).toFixed(1)},${(h - padY).toFixed(1)} L${sx(xMin).toFixed(1)},${(h - padY).toFixed(1)} Z`

  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} role="img"
      aria-label={`Response curve: ${yLabel} against ${xLabel}`}>
      <line x1={padX} y1={h - padY} x2={w - 8} y2={h - padY} stroke="var(--line)" strokeWidth="1" />
      <path d={area} fill="var(--rain-dim)" />
      <path d={d} fill="none" stroke="var(--rain)" strokeWidth="2" strokeLinejoin="round" />
      {current != null && current >= xMin && current <= xMax && (
        <g>
          <line x1={sx(current)} y1={padY} x2={sx(current)} y2={h - padY} stroke="var(--muted)" strokeDasharray="3 3" />
          <text x={sx(current)} y={padY - 4} fill="var(--muted)" fontSize="9" textAnchor="middle"
            fontFamily="var(--font-mono)">now</text>
        </g>
      )}
      {recommended != null && recommended >= xMin && recommended <= xMax && (
        <g>
          <line x1={sx(recommended)} y1={padY} x2={sx(recommended)} y2={h - padY} stroke="var(--maize)" strokeWidth="1.5" />
          <circle cx={sx(recommended)} cy={sy(points.reduce((best, p) =>
            Math.abs(p.x - recommended) < Math.abs(best.x - recommended) ? p : best, points[0]).y)}
            r="3.5" fill="var(--maize)" />
          <text x={sx(recommended)} y={padY - 4} fill="var(--maize)" fontSize="9" textAnchor="middle"
            fontFamily="var(--font-mono)">target</text>
        </g>
      )}
      <text x={4} y={sy(yMax)} fill="var(--muted)" fontSize="9" fontFamily="var(--font-mono)">
        {Math.round(yMax)}
      </text>
      <text x={4} y={h - padY} fill="var(--muted)" fontSize="9" fontFamily="var(--font-mono)">
        {Math.round(yMin)}
      </text>
      <text x={w - 8} y={h - 3} fill="var(--muted)" fontSize="9" textAnchor="end" fontFamily="var(--font-mono)">
        {xLabel}
      </text>
    </svg>
  )
}

/* --- season accuracy bars (model page) ---------------------------------- */
export function SeasonBars({ seasons }) {
  if (!seasons?.length) return null
  const max = Math.max(...seasons.map((s) => Math.max(s.r2 ?? 0, 0.1)))
  return (
    <div className="stack" style={{ gap: 10 }}>
      {seasons.map((s) => (
        <div key={s.season} className="row" style={{ gap: 10, flexWrap: 'nowrap' }}>
          <span className="mono tiny" style={{ width: 38 }}>{s.season}</span>
          <div style={{ flex: 1, height: 8, background: 'var(--surface-2)', borderRadius: 999, overflow: 'hidden' }}>
            <div style={{
              width: `${Math.max(0, (s.r2 ?? 0) / max) * 100}%`,
              height: '100%',
              background: 'linear-gradient(90deg, var(--rain), var(--maize))',
            }} />
          </div>
          <span className="mono tiny" style={{ width: 96, textAlign: 'right' }}>
            R² {s.r2?.toFixed(3)} · {kg(s.mae_kg_ph)} MAE
          </span>
        </div>
      ))}
    </div>
  )
}
