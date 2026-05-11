import { type ReactNode, useEffect, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { apiFetch } from '../../api/client'
import type { AuthNameResponse, UserRole } from '../../types/auth'
import { hasRoleAccess, mapRoleNameToUserRole } from '../../utils/auth'

interface RequireRoleProps {
  allowedRoles: UserRole[]
  children: ReactNode
}

const RequireRole = ({ allowedRoles, children }: RequireRoleProps) => {
  const [isLoading, setIsLoading] = useState(true)
  const [isAllowed, setIsAllowed] = useState(false)
  const allowedRoleKey = allowedRoles.join('|')

  useEffect(() => {
    const checkAccess = async () => {
      try {
        const data = await apiFetch<AuthNameResponse>('/auth/name', { method: 'GET' })
        const resolvedRole = mapRoleNameToUserRole(data?.roleName ?? data?.role)
        setIsAllowed(hasRoleAccess(resolvedRole, allowedRoles))
      } catch {
        setIsAllowed(false)
      } finally {
        setIsLoading(false)
      }
    }

    checkAccess()
  }, [allowedRoleKey, allowedRoles])

  if (isLoading) {
    return (
      <div className="admin-page-wrapper">
        <div className="admin-shell compact-mode">
          <section className="admin-metrics compact">권한 확인 중...</section>
        </div>
      </div>
    )
  }

  if (!isAllowed) {
    return <Navigate to="/" replace />
  }

  return children
}

export default RequireRole
