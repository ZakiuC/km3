import { http } from './client'
import type { Paged, Role, SessionUser } from './types'

export interface Invitation {
  id: number
  email: string
  role: Role
  token: string
  expires_at: string
  accepted_at: string | null
}

export const usersApi = {
  list: (query?: { page?: number; size?: number }) =>
    http.get<Paged<SessionUser>>('/users', query),
  invite: (email: string, role: Role) =>
    http.post<Invitation>('/users/invite', { email, role }),
  invitations: () => http.get<Paged<Invitation>>('/users/invitations'),
  updateRole: (id: number, role: Role) =>
    http.put<SessionUser>(`/users/${id}`, { role }),
}
