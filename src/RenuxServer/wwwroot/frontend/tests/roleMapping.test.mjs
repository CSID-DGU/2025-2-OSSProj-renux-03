import assert from 'node:assert/strict'
import test from 'node:test'

import { mapRoleNameToUserRole } from '../src/auth/roleMapping.ts'

test('총학생회와 관리자는 대학 수준 권한으로 매핑한다', () => {
  assert.equal(mapRoleNameToUserRole('총학생회'), 'UNIVERSITY_COUNCIL')
  assert.equal(mapRoleNameToUserRole('총 학생회'), 'UNIVERSITY_COUNCIL')
  assert.equal(mapRoleNameToUserRole('관리자'), 'UNIVERSITY_COUNCIL')
  assert.equal(mapRoleNameToUserRole('UNIVERSITY_COUNCIL'), 'UNIVERSITY_COUNCIL')
})

test('학과 학생회와 일반 사용자를 구분한다', () => {
  assert.equal(mapRoleNameToUserRole('학생회'), 'DEPARTMENT_COUNCIL')
  assert.equal(mapRoleNameToUserRole('DEPARTMENT_COUNCIL'), 'DEPARTMENT_COUNCIL')
  assert.equal(mapRoleNameToUserRole('일반학생'), 'STUDENT')
  assert.equal(mapRoleNameToUserRole(null), 'STUDENT')
})
