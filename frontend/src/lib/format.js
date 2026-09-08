/** Formatting helpers. Every number a user sees goes through one of these. */

export const kg = (v, digits = 0) =>
  v == null || Number.isNaN(v) ? '—' : Number(v).toLocaleString('en-KE', { maximumFractionDigits: digits })

export const signed = (v, digits = 0) =>
  v == null ? '—' : `${v >= 0 ? '+' : '−'}${kg(Math.abs(v), digits)}`

export const pct = (v, digits = 0) => (v == null ? '—' : `${(v * 100).toFixed(digits)}%`)

export const r2 = (v) => (v == null ? '—' : (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(3))

/** district slugs are lower_snake in the data; show them as words. */
export const districtName = (s) =>
  (s || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

export const titleCase = (s) =>
  (s || '').replace(/[_-]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

/** A yield in kg/ha, with the unit as a separate quiet element. */
export function yieldParts(v) {
  return { value: kg(v), unit: 'kg/ha' }
}

export const confidenceTone = (confidence) =>
  ({ high: 'pill-leaf', moderate: 'pill-maize', low: 'pill' })[String(confidence).toLowerCase()] ||
  'pill'

export function relativeTime(iso) {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return iso
  const mins = Math.round((Date.now() - then) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs} h ago`
  return `${Math.round(hrs / 24)} d ago`
}
