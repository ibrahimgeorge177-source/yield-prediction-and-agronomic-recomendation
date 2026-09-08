import { useMemo } from 'react'
import { Field } from './ui'
import { districtName, titleCase } from '../lib/format'

/**
 * The plot input form.
 *
 * Only `district` is required — the API fills everything else from that
 * district's own history, so the form is deliberately short and every field
 * says what happens when it is left blank. Ranges come from the API's own
 * input-schema endpoint (p01–p99 of what was observed), so the hints stay
 * true to the data rather than being hard-coded here.
 */

/** Seasons the form offers beyond the ones the survey observed. */
export const FORECAST_FROM = 2021
export const FORECAST_TO = 2030

/** Today's season, held inside the offered range so the select always matches. */
const defaultYear = () =>
  Math.min(Math.max(new Date().getFullYear(), FORECAST_FROM), FORECAST_TO)

export const EMPTY_PLOT = {
  district: '',
  year: defaultYear(),
  seed_category: '',
  plot_acres: '',
  dap_kg_ph: '',
  can_kg_ph: '',
  urea_kg_ph: '',
  lime_kg_ph: '',
  compost_wheelbarrows_per_acre: '',
  plant_date: '',
  intercrop: '',
}

/** Strip blanks and coerce numbers: the API prefers an absent field to a null. */
export function toApiPlot(plot) {
  const out = {}
  for (const [key, raw] of Object.entries(plot)) {
    if (raw === '' || raw == null) continue
    if (key === 'intercrop') out[key] = raw === 'yes'
    else if (key === 'district' || key === 'seed_category' || key === 'plant_date' || key === 'plot_id')
      out[key] = raw
    else {
      const n = Number(raw)
      if (!Number.isNaN(n)) out[key] = n
    }
  }
  return out
}

const NUMERIC_FIELDS = [
  { name: 'plot_acres', label: 'Plot size', unit: 'acres' },
  { name: 'dap_kg_ph', label: 'DAP at planting', unit: 'kg/ha' },
  { name: 'can_kg_ph', label: 'CAN topdress', unit: 'kg/ha' },
  { name: 'urea_kg_ph', label: 'Urea', unit: 'kg/ha' },
  { name: 'lime_kg_ph', label: 'Lime', unit: 'kg/ha' },
  { name: 'compost_wheelbarrows_per_acre', label: 'Compost', unit: 'wheelbarrows/acre' },
]

function rangeHint(spec) {
  if (!spec?.observed_range) return 'blank → district median'
  const { p01, p50, p99 } = spec.observed_range
  if (p50 == null) return 'blank → district median'
  return `typical ${Math.round(p50)} · range ${Math.round(p01)}–${Math.round(p99)}`
}

export default function PlotForm({ plot, onChange, districts = [], schema, seasons = [], compact = false }) {
  const specs = useMemo(() => {
    const map = {}
    for (const f of schema?.fields || []) map[f.name] = f
    return map
  }, [schema])

  const set = (key) => (event) => onChange({ ...plot, [key]: event.target.value })
  const seedOptions = specs.seed_category?.allowed_values || [
    'hybrid_branded', 'other_hybrid', 'local', 'mixed',
  ]

  // Observed seasons come from the API; forecasting seasons run to 2030. The two
  // are separated because they are answered differently: an observed season has
  // real weather behind it, a future one falls back to the district's
  // climatology, which is a weaker basis and the hint says so.
  const { observed, future } = useMemo(() => {
    const known = seasons.length ? [...seasons].sort((a, b) => a - b) : [2016, 2017, 2018, 2019, 2020]
    const start = Math.max(Math.max(...known) + 1, FORECAST_FROM)
    const ahead = []
    for (let y = start; y <= FORECAST_TO; y += 1) ahead.push(y)
    return { observed: known, future: ahead }
  }, [seasons])

  return (
    <div className="stack" style={{ gap: 14 }}>
      <div className="grid grid-2">
        <Field label="District" id="district" hint="required — the one field the model cannot guess">
          <select id="district" value={plot.district} onChange={set('district')} required>
            <option value="">Select a district…</option>
            {districts.map((d) => (
              <option key={d} value={d}>{districtName(d)}</option>
            ))}
          </select>
        </Field>

        <Field label="Season" id="year" hint="seasons after 2020 use that district's climatology">
          <select id="year" value={plot.year} onChange={set('year')}>
            <optgroup label="Observed seasons">
              {observed.map((y) => (
                <option key={y} value={y}>{y}</option>
              ))}
            </optgroup>
            <optgroup label="Forecast seasons · district climatology">
              {future.map((y) => (
                <option key={y} value={y}>{y}</option>
              ))}
            </optgroup>
          </select>
        </Field>
      </div>

      <div className="grid grid-2">
        <Field label="Seed category" id="seed_category" hint="blank → what the district typically plants">
          <select id="seed_category" value={plot.seed_category} onChange={set('seed_category')}>
            <option value="">District default</option>
            {seedOptions.map((s) => (
              <option key={s} value={s}>{titleCase(s)}</option>
            ))}
          </select>
        </Field>

        <Field label="Intercropped" id="intercrop" hint="maize grown with another crop">
          <select id="intercrop" value={plot.intercrop} onChange={set('intercrop')}>
            <option value="">District default</option>
            <option value="yes">Yes</option>
            <option value="no">No</option>
          </select>
        </Field>
      </div>

      {!compact && (
        <>
          <div className="eyebrow" style={{ marginTop: 4 }}>Inputs applied · leave blank to use the district norm</div>
          <div className="grid grid-3">
            {NUMERIC_FIELDS.map(({ name, label, unit }) => (
              <Field key={name} label={`${label} (${unit})`} id={name} hint={rangeHint(specs[name])}>
                <input id={name} type="number" min="0" step="any" inputMode="decimal"
                  placeholder="—" value={plot[name]} onChange={set(name)} />
              </Field>
            ))}
          </div>

          <div className="grid grid-2">
            <Field label="Planting date" id="plant_date" hint="timing is the strongest lever in this data">
              <input id="plant_date" type="date" value={plot.plant_date} onChange={set('plant_date')} />
            </Field>
          </div>
        </>
      )}
    </div>
  )
}
