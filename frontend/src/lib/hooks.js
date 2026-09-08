import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from './api'

/**
 * Reference data (districts, levers, schema, model summary) is small, changes
 * rarely, and is what makes the app usable on a poor connection — so it is
 * served from localStorage first and refreshed in the background.
 */
const CACHE_PREFIX = 'maize.cache.'
const CACHE_TTL_MS = 1000 * 60 * 60 * 24 * 7

function readCache(key) {
  try {
    const raw = localStorage.getItem(CACHE_PREFIX + key)
    if (!raw) return null
    const { at, value } = JSON.parse(raw)
    if (Date.now() - at > CACHE_TTL_MS) return null
    return value
  } catch {
    return null
  }
}

function writeCache(key, value) {
  try {
    localStorage.setItem(CACHE_PREFIX + key, JSON.stringify({ at: Date.now(), value }))
  } catch {
    /* quota or private mode — the network path still works */
  }
}

/** Cached GET with background refresh. Returns { data, error, loading, stale, reload }. */
export function useReference(key, fetcher) {
  const cached = useRef(readCache(key))
  const [data, setData] = useState(cached.current)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(!cached.current)
  const [stale, setStale] = useState(Boolean(cached.current))

  const load = useCallback(async () => {
    setLoading(!readCache(key))
    try {
      const value = await fetcher()
      setData(value)
      setError(null)
      setStale(false)
      writeCache(key, value)
    } catch (err) {
      setError(err)
      // A cached copy is better than an error page when the network is down.
      if (readCache(key)) setStale(true)
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  useEffect(() => {
    load()
  }, [load])

  return { data, error, loading, stale, reload: load }
}

/** One-shot action (POST) with its own pending/error state. */
export function useAction(fn) {
  const [state, setState] = useState({ data: null, error: null, pending: false })

  const run = useCallback(
    async (...args) => {
      setState({ data: null, error: null, pending: true })
      try {
        const data = await fn(...args)
        setState({ data, error: null, pending: false })
        return data
      } catch (err) {
        const error = err instanceof ApiError ? err : new ApiError(err.message || 'Unexpected error')
        setState({ data: null, error, pending: false })
        return null
      }
    },
    [fn],
  )

  const reset = useCallback(() => setState({ data: null, error: null, pending: false }), [])
  return { ...state, run, reset }
}

/** Browser online/offline, so the UI can say why a request will not work.
 *  Guarded for environments without `navigator` (prerender, tests). */
export const isOnline = () => (typeof navigator === 'undefined' ? true : navigator.onLine)

export function useOnline() {
  const [online, setOnline] = useState(isOnline)
  useEffect(() => {
    const on = () => setOnline(true)
    const off = () => setOnline(false)
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => {
      window.removeEventListener('online', on)
      window.removeEventListener('offline', off)
    }
  }, [])
  return online
}

/** Theme, remembered per device; defaults to dark, which is the designed state. */
export function useTheme() {
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem('maize.theme') || 'dark'
    } catch {
      return 'dark'
    }
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    const meta = document.querySelector('meta[name="theme-color"]')
    if (meta) meta.setAttribute('content', theme === 'dark' ? '#0A1317' : '#F6F4EF')
    try {
      localStorage.setItem('maize.theme', theme)
    } catch {
      /* ignore */
    }
  }, [theme])

  return [theme, () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))]
}
