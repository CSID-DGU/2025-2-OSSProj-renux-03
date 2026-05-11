import type { UserRole } from '../types/auth'

export const mapRoleNameToUserRole = (roleName?: string | null): UserRole => {
  if (!roleName) return 'STUDENT'

  const normalized = roleName.trim()
  if (normalized.includes('관리자')) return 'UNIVERSITY_COUNCIL'
  if (normalized.includes('학생회')) return 'DEPARTMENT_COUNCIL'

  return 'STUDENT'
}

export const hasRoleAccess = (role: UserRole, allowedRoles: UserRole[]) => {
  if (role === 'UNIVERSITY_COUNCIL') return true
  return allowedRoles.includes(role)
}
