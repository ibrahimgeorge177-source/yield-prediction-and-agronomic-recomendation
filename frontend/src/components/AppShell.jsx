import { NavLink, Outlet } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { isOnline, useOnline, useTheme } from '../lib/hooks'
import {
  IconAdvisor, IconDocs, IconForecast, IconHome, IconMaize, IconModel, IconMoon, IconSun,
} from './Icons'

const NAV = [
  { to: '/', label: 'Overview', Icon: IconHome, end: true },
  { to: '/forecast', label: 'Forecast', Icon: IconForecast },
  { to: '/advisor', label: 'Advisor', Icon: IconAdvisor },
  { to: '/model', label: 'Model', Icon: IconModel },
  { to: '/docs', label: 'Docs', Icon: IconDocs },
]

/** Live service state: what the API says about itself, polled gently. */
function useServiceStatus() {
  const [status, setStatus] = useState({ state: 'checking' })
  const online = useOnline()

  useEffect(() => {
    let cancelled = false
    const check = async () => {
      if (!isOnline()) {
        if (!cancelled) setStatus({ state: 'offline' })
        return
      }
      try {
        const health = await api.health()
        if (!cancelled) {
          setStatus({
            state: health.model_loaded ? 'ready' : health.status === 'ok' ? 'warming' : 'degraded',
            detail: health.detail,
            version: health.version,
          })
        }
      } catch (err) {
        if (!cancelled) setStatus({ state: 'down', detail: err.message })
      }
    }
    check()
    const timer = setInterval(check, 60000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [online])

  return status
}

const STATUS_COPY = {
  checking: { label: 'Checking', tone: 'pill' },
  ready: { label: 'Model ready', tone: 'pill-leaf' },
  warming: { label: 'Model idle', tone: 'pill-maize' },
  degraded: { label: 'Degraded', tone: 'pill-maize' },
  down: { label: 'API unreachable', tone: 'pill-danger' },
  offline: { label: 'Offline', tone: 'pill-danger' },
}

export function StatusPill({ status }) {
  const copy = STATUS_COPY[status.state] || STATUS_COPY.checking
  const title =
    status.detail ||
    { ready: 'The model is loaded and serving predictions.',
      warming: 'The service is up; the model loads on the first prediction.',
      offline: 'No network. Cached reference data is still readable.' }[status.state] ||
    ''
  return (
    <span className={`pill ${copy.tone}`} title={title}>
      <span className={`dot ${status.state === 'ready' ? 'dot-live' : ''}`} />
      {copy.label}
    </span>
  )
}

export default function AppShell() {
  const status = useServiceStatus()
  const [theme, toggleTheme] = useTheme()

  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          <span className="brand-mark">
            <IconMaize width={20} height={20} style={{ color: '#16202a' }} />
          </span>
          <span>
            <span className="brand-name">Maize Yield</span>
            <br />
            <span className="brand-sub">Kenya · district</span>
          </span>
        </div>

        <nav className="nav" aria-label="Main">
          {NAV.map(({ to, label, Icon, end }) => (
            <NavLink key={to} to={to} end={end}
              className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
              <Icon />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="rail-foot">
          <StatusPill status={status} />
          <button className="btn btn-ghost btn-sm" onClick={toggleTheme}
            aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}>
            {theme === 'dark' ? <IconSun /> : <IconMoon />}
            {theme === 'dark' ? 'Light' : 'Dark'}
          </button>
          <span className="tiny">
            One Acre Fund MEL survey
            <br />
            2016–2020 · 23,674 plots
          </span>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <span className="brand-mark" style={{ width: 26, height: 26 }}>
            <IconMaize width={15} height={15} style={{ color: '#16202a' }} />
          </span>
          <span className="brand-name" style={{ flex: 1 }}>Maize Yield</span>
          <StatusPill status={status} />
          <button className="btn btn-ghost btn-sm" onClick={toggleTheme} aria-label="Toggle theme">
            {theme === 'dark' ? <IconSun /> : <IconMoon />}
          </button>
        </header>

        <main className="content">
          <Outlet />
        </main>

        <nav className="mobile-nav" aria-label="Main">
          {NAV.map(({ to, label, Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => (isActive ? 'active' : '')}>
              <Icon width={19} height={19} />
              {label}
            </NavLink>
          ))}
        </nav>
      </div>
    </div>
  )
}
