import { Route, Routes } from 'react-router-dom'
import { DashboardPage } from './features/dashboard/DashboardPage'
import { WorkspacePage } from './features/workspace/WorkspacePage'

function App() {
  return (
    <div style={{ textAlign: 'left', padding: '24px 32px' }}>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/projects/:videoId" element={<WorkspacePage />} />
      </Routes>
    </div>
  )
}

export default App
