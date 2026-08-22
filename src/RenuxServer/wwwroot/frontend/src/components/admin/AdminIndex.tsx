import { Navigate } from 'react-router-dom'
import { useAdminConsole } from './adminConsoleContext'

/**
 * /admin 진입 시 역할에 맞는 첫 화면으로 보낸다.
 * 학과 관리자를 대시보드로 보내면 권한 없음 화면부터 보게 되므로,
 * 각자 실제로 일할 수 있는 곳을 시작점으로 삼는다.
 */
const AdminIndex = () => {
  const { isUniversityLevel } = useAdminConsole()
  return <Navigate to={isUniversityLevel ? '/admin/dashboard' : '/admin/department'} replace />
}

export default AdminIndex
