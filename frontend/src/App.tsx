import { Route, Routes } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell'
import { DashboardPage } from './features/dashboard/DashboardPage'
import { WorkspacePage } from './features/workspace/WorkspacePage'
import { ReviewCenterPage } from './features/review/ReviewCenterPage'
import { NewProjectPage } from './features/new-project/NewProjectPage'

function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/review" element={<ReviewCenterPage />} />
        <Route path="/projects/new" element={<NewProjectPage />} />
        <Route path="/projects/:videoId" element={<WorkspacePage />} />
      </Routes>
    </AppShell>
  )
}

export default App
