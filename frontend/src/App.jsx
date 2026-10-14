import { HashRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth } from './context/AuthContext'
import { Loading, ToastProvider } from './components/ui'
import Layout from './components/Layout'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Projects from './pages/Projects'
import ProjectDetail from './pages/ProjectDetail'
import People from './pages/People'
import Allocation from './pages/Allocation'
import Budgets from './pages/Budgets'
import TimeLog from './pages/TimeLog'
import Archive from './pages/Archive'
import Profile from './pages/Profile'

function RequireRole({ roles, children }) {
  const { user } = useAuth()
  return roles.includes(user.role) ? children : <Navigate to="/" replace />
}

function AppRoutes() {
  const { user, loading } = useAuth()
  if (loading) return <Loading />
  if (!user) return <Login />
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="projects" element={<Projects />} />
        <Route path="projects/:id" element={<ProjectDetail />} />
        <Route path="people" element={<People />} />
        <Route path="time" element={<TimeLog />} />
        <Route path="profile" element={<Profile />} />
        <Route path="allocation" element={<RequireRole roles={['admin', 'manager']}><Allocation /></RequireRole>} />
        <Route path="budgets" element={<RequireRole roles={['admin', 'manager']}><Budgets /></RequireRole>} />
        <Route path="archive" element={<RequireRole roles={['admin']}><Archive /></RequireRole>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}

export default function App() {
  return (
    <HashRouter>
      <ToastProvider>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </ToastProvider>
    </HashRouter>
  )
}
