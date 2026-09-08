import { Navigate, Route, Routes } from 'react-router-dom'
import AppShell from './components/AppShell'
import Home from './pages/Home'
import Forecast from './pages/Forecast'
import Advisor from './pages/Advisor'
import Model from './pages/Model'
import Docs from './pages/Docs'

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Home />} />
        <Route path="forecast" element={<Forecast />} />
        <Route path="advisor" element={<Advisor />} />
        <Route path="model" element={<Model />} />
        <Route path="docs" element={<Docs />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
