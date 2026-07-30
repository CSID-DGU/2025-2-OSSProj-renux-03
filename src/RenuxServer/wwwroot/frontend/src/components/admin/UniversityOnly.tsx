import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useAdminConsole } from './adminConsoleContext'

/**
 * 대학 수준(관리자·총학생회) 전용 화면 가드.
 * AdminLayout이 이미 서버에서 역할을 확인했으므로 여기서 다시 요청하지 않는다.
 * 서버 API도 같은 조건을 독립적으로 검증하므로 이 가드는 UI 노출 제어용이다.
 */
const UniversityOnly = ({ children }: { children: ReactNode }) => {
  const { isUniversityLevel } = useAdminConsole()

  if (!isUniversityLevel) {
    return (
      <div className="ac-denied">
        <h2>접근 권한이 없습니다</h2>
        <p>이 화면은 총학생회·관리자 계정만 이용할 수 있습니다.</p>
        <Link to="/admin/department" className="ac-btn ac-btn--primary">학과 콘솔로 이동</Link>
      </div>
    )
  }

  return <>{children}</>
}

export default UniversityOnly
