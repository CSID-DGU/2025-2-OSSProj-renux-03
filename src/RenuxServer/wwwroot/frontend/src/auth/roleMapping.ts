import type { UserRole } from '../types/auth'

/**
 * The server currently exposes a localized role name. Keep the matching order
 * explicit so "총학생회" is never swallowed by the broader "학생회" check.
 */
export const mapRoleNameToUserRole = (roleName?: string | null): UserRole => {
  if (!roleName) return 'STUDENT'

  const normalized = roleName.trim().toLowerCase()
  const compact = normalized.replace(/\s+/g, '')
  if (
    normalized === 'university_council'
    || compact.includes('총학생회')
    || compact.includes('관리자')
  ) {
    return 'UNIVERSITY_COUNCIL'
  }
  if (normalized === 'department_council' || compact.includes('학생회')) {
    return 'DEPARTMENT_COUNCIL'
  }
  return 'STUDENT'
}
