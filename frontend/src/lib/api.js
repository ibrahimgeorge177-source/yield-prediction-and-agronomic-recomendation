/**
 * Client for the Kenya Maize Yield Prediction API.
 *
 * The base URL is baked in at build time from VITE_API_BASE_URL and can be
 * overridden at run time (Settings on the Model page), which is what lets one
 * build point at a staging deployment.
 */

const BUILD_TIME_BASE =
  import.meta.env.VITE_API_BASE_URL || 'https://web-production-7dae9.up.railway.app'

const OVERRIDE_KEY = 'maize.apiBaseUrl'

export function getBaseUrl() {
  try {
    return localStorage.getItem(OVERRIDE_KEY) || BUILD_TIME_BASE
  } catch {
    return BUILD_TIME_BASE
  }
}

export function setBaseUrl(url) {
  try {
    if (url && url !== BUILD_TIME_BASE) localStorage.setItem(OVERRIDE_KEY, url.replace(/\/$/, ''))
    else localStorage.removeItem(OVERRIDE_KEY)
  } catch {
    /* private mode: the build-time default still applies */
  }
}

export const defaultBaseUrl = BUILD_TIME_BASE

/** Errors carry the API's own message, which is written to be actionable. */
export class ApiError extends Error {
  constructor(message, { status, detail, offline } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.offline = Boolean(offline)
  }
}

function describe(payload, status) {
  const detail = payload?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    // FastAPI validation errors: name the field rather than dumping the array.
    return detail
      .map((d) => {
        const field = Array.isArray(d.loc) ? d.loc.filter((p) => p !== 'body').join('.') : ''
        return field ? `${field}: ${d.msg}` : d.msg
      })
      .join('; ')
  }
  return `Request failed with status ${status}`
}

async function request(path, { method = 'GET', body, signal, timeout = 45000 } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  if (signal) signal.addEventListener('abort', () => controller.abort(), { once: true })

  let response
  try {
    response = await fetch(`${getBaseUrl()}${path}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    })
  } catch (err) {
    clearTimeout(timer)
    if (typeof navigator !== 'undefined' && !navigator.onLine) {
      throw new ApiError('You are offline. Cached reference data is still available.', {
        offline: true,
      })
    }
    if (err.name === 'AbortError') {
      throw new ApiError('The API did not respond in time. It may be waking up — try again.')
    }
    throw new ApiError(`Could not reach the API at ${getBaseUrl()}.`)
  }
  clearTimeout(timer)

  let payload = null
  const text = await response.text()
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = null
    }
  }

  if (!response.ok) {
    throw new ApiError(describe(payload, response.status), {
      status: response.status,
      detail: payload?.detail,
    })
  }
  return payload
}

/* --- endpoints ----------------------------------------------------------- */

export const api = {
  health: () => request('/health', { timeout: 12000 }),
  ready: () => request('/ready'),
  modelSummary: () => request('/api/v1/model/summary'),
  districts: () => request('/api/v1/reference/districts'),
  seedTypes: () => request('/api/v1/reference/seed-types'),
  inputSchema: () => request('/api/v1/reference/input-schema'),
  levers: () => request('/api/v1/reference/levers'),

  /** plots: array of plot objects; at minimum { district }. */
  predict: (plots, { year, includePlots = true, minPlots = 1 } = {}) =>
    request('/api/v1/predict', {
      method: 'POST',
      body: {
        plots,
        year: year ?? null,
        include_plot_predictions: includePlots,
        min_plots: minPlots,
      },
    }),

  /** One plot in, a ranked list of changes out. */
  recommend: (plot, { year, levers, minLift, includeCurve = true } = {}) =>
    request('/api/v1/recommend', {
      method: 'POST',
      body: {
        plot,
        year: year ?? null,
        levers: levers ?? null,
        ...(minLift != null ? { min_lift_kg_ph: minLift } : {}),
        include_curve: includeCurve,
      },
    }),
}
