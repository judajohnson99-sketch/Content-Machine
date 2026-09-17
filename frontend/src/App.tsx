import { Link, Route, Routes } from 'react-router-dom'
import { DashboardPage } from './features/dashboard/DashboardPage'
import { WorkspacePage } from './features/workspace/WorkspacePage'
import { ReviewCenterPage } from './features/review/ReviewCenterPage'

function App() {
  return (
    <div style={{ textAlign: 'left', padding: '24px 32px' }}>
      <nav style={{ marginBottom: 16, display: 'flex', gap: 16, fontSize: 13 }}>
        <Link to="/">Dashboard</Link>
        <Link to="/review">Review Center</Link>
      </nav>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/review" element={<ReviewCenterPage />} />
        <Route path="/projects/:videoId" element={<WorkspacePage />} />
      </Routes>
    </div>
  )
}

export default App
