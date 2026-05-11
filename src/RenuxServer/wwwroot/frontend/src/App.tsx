import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import HomePage from './pages/home/HomePage'
import SignInPage from './pages/auth/SignInPage'
import SignUpPage from './pages/auth/SignUpPage'
import ChatPage from './pages/chat/ChatPage'
import SettingsPage from './pages/settings/SettingsPage'
import UniversityAdminPage from './pages/admin/UniversityAdminPage'
import DepartmentAdminPage from './pages/admin/DepartmentAdminPage'
import RequireRole from './components/auth/RequireRole'
import type { UserRole } from './types/auth'

const universityAdminRoles: UserRole[] = ['UNIVERSITY_COUNCIL']
const departmentAdminRoles: UserRole[] = ['DEPARTMENT_COUNCIL', 'UNIVERSITY_COUNCIL']

function App() {
  return (
    <BrowserRouter>
      <div className="app-shell">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/auth/in" element={<SignInPage />} />
          <Route path="/auth/up" element={<SignUpPage />} />
          <Route path="/chat/:chatId" element={<ChatPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route
            path="/admin/university"
            element={
              <RequireRole allowedRoles={universityAdminRoles}>
                <UniversityAdminPage />
              </RequireRole>
            }
          />
          <Route
            path="/admin/department"
            element={
              <RequireRole allowedRoles={departmentAdminRoles}>
                <DepartmentAdminPage />
              </RequireRole>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </BrowserRouter>
  )
}

export default App
