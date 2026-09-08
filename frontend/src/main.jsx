import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, useLocation } from 'react-router-dom'
import App from './App'
import './styles/global.css'

/** Route changes should land at the top of the new page, as a document would. */
function ScrollToTop() {
  const { pathname } = useLocation()
  React.useEffect(() => window.scrollTo({ top: 0, behavior: 'auto' }), [pathname])
  return null
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <ScrollToTop />
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)
