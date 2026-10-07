import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell'
import { DashboardPage } from './features/dashboard/DashboardPage'
import { WorkspacePage } from './features/workspace/WorkspacePage'
import { ReviewCenterPage } from './features/review/ReviewCenterPage'
import { NewProjectPage } from './features/new-project/NewProjectPage'
import { GeneratePage } from './features/generate/GeneratePage'
import { NotFoundPage } from './features/not-found/NotFoundPage'

function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/review" element={<ReviewCenterPage />} />
        <Route path="/projects/new" element={<NewProjectPage />} />
        {/* The raw image lab is a diagnostic tool, not part of making a
            video, so it lives under /system rather than in primary nav. */}
        <Route path="/system/image-lab" element={<GeneratePage />} />
        <Route path="/generate" element={<Navigate to="/system/image-lab" replace />} />
        <Route path="/projects/:videoId" element={<WorkspacePage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </AppShell>
  )
}

export default App
