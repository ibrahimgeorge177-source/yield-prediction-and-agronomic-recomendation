import React from 'react'

/**
 * A render error must never leave a blank page.
 *
 * The failure this was written for: a service worker from an earlier build
 * serves a stale bundle, React commits an effect whose cleanup no longer
 * exists, and the whole tree unmounts — the user sees nothing at all and has
 * no way back except knowing to clear site data. This catches that, says what
 * happened, and offers the one action that actually fixes it.
 */
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null, clearing: false }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error('render error', error, info?.componentStack)
  }

  reload = () => window.location.reload()

  /** Drop every cache and service worker, then reload on a clean slate. */
  reset = async () => {
    this.setState({ clearing: true })
    try {
      if ('serviceWorker' in navigator) {
        const registrations = await navigator.serviceWorker.getRegistrations()
        await Promise.all(registrations.map((r) => r.unregister()))
      }
      if ('caches' in window) {
        const keys = await caches.keys()
        await Promise.all(keys.map((k) => caches.delete(k)))
      }
    } catch {
      /* nothing else to try — the reload below is still worth doing */
    }
    window.location.reload()
  }

  render() {
    if (!this.state.error) return this.props.children

    return (
      <div style={{ maxWidth: 640, margin: '12vh auto', padding: '0 24px' }}>
        <span className="eyebrow">Something broke</span>
        <h1 style={{ marginTop: 10 }}>This page failed to render.</h1>
        <p className="lede" style={{ marginTop: 14 }}>
          Usually this means an old copy of the app is cached on this device after an update.
          Clearing it takes a second and keeps nothing you would miss.
        </p>
        <div className="row" style={{ marginTop: 20 }}>
          <button className="btn btn-primary" onClick={this.reset} disabled={this.state.clearing}>
            {this.state.clearing ? 'Clearing…' : 'Clear cache and reload'}
          </button>
          <button className="btn" onClick={this.reload}>Just reload</button>
        </div>
        <pre style={{
          marginTop: 26, padding: '12px 14px', overflowX: 'auto',
          background: 'var(--surface)', border: '1px solid var(--line)',
          borderRadius: 'var(--radius-s)', fontSize: '0.78rem', color: 'var(--ink-2)',
        }}>
          <code>{String(this.state.error?.message || this.state.error)}</code>
        </pre>
      </div>
    )
  }
}
