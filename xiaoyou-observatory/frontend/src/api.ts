import type { AuditItem, AuthState, ContainerAction, MetricsResponse, QrState, RuntimeStatus } from './types'

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...(init.headers || {}),
    },
  })
  if (!response.ok) {
    let detail = '命轨请求失败'
    try {
      const body = await response.json()
      detail = body.detail || detail
    } catch {
      // Keep the safe generic message.
    }
    throw new ApiError(detail, response.status)
  }
  return response.json() as Promise<T>
}

export function getMe() {
  return request<AuthState>('/api/auth/me')
}

export function login(username: string, password: string, otp: string) {
  return request<AuthState>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password, otp }),
  })
}

export function guestLogin() {
  return request<AuthState>('/api/auth/guest', { method: 'POST' })
}

export function logout(csrf: string) {
  return request<{ ok: boolean }>('/api/auth/logout', {
    method: 'POST',
    headers: { 'X-CSRF-Token': csrf },
  })
}

export function getStatus() {
  return request<RuntimeStatus>('/api/status')
}

export function getMetrics(hours = 24) {
  const safeHours = Math.max(1, Math.min(168, Math.floor(hours)))
  return request<MetricsResponse>(`/api/metrics?hours=${safeHours}`)
}

export function getQr() {
  return request<QrState>('/api/qr')
}

export function getLogs(limit = 240) {
  return request<{ lines: string[]; truncated: boolean }>(`/api/logs?limit=${limit}`)
}

export function getAudit() {
  return request<AuditItem[]>('/api/audit')
}

export function containerAction(action: ContainerAction, csrf: string) {
  return request<{ ok: boolean; action: ContainerAction; message: string }>(`/api/container/${action}`, {
    method: 'POST',
    headers: { 'X-CSRF-Token': csrf },
  })
}
