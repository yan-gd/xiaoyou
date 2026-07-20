export type OverallState = 'online' | 'waiting_qr' | 'starting' | 'stopped' | 'degraded' | 'unknown'
export type PulseState = 'healthy' | 'idle' | 'waiting' | 'degraded' | 'offline' | 'unknown'

export interface AuthState {
  authenticated: boolean
  username: string
  role: 'admin' | 'guest'
  csrf_token: string
  expires_at: number
}

export interface ResourceState {
  cpu_percent: number
  memory_percent: number
  memory_usage: string
}

export interface ContainerState extends ResourceState {
  exists: boolean
  running: boolean
  status: string
  health: string
  started_at: string
  finished_at: string
  restart_count: number
  image: string
}

export interface ServicePulse {
  state: PulseState
  label: string
  detail: string
  last_event_at: string
}

export interface RuntimeStatus {
  overall: OverallState
  observed_at: number
  host: ResourceState
  container: ContainerState
  wechat: ServicePulse
  model: ServicePulse
  memory: ServicePulse
  vision: ServicePulse
  last_input_at: string
  last_output_at: string
  total_tokens: number
  today_tokens: number
  token_usage_available: boolean
  recent_errors: number
  qr_available: boolean
  plugin_versions: string[]
}

export interface MetricPoint {
  observed_at: number
  host_cpu_percent: number
  host_memory_percent: number
  container_cpu_percent: number
  container_memory_percent: number
  recent_errors: number
  total_tokens: number
  today_tokens: number
  running: boolean
}

export interface MetricsResponse {
  hours: number
  points: MetricPoint[]
}

export interface QrState {
  available: boolean
  login_url: string
  detected_at: string
  status: 'waiting' | 'online' | 'unavailable'
}

export interface AuditItem {
  id: number
  action: string
  result: string
  created_at: number
  ip_address: string
}

export type ContainerAction = 'start' | 'stop' | 'restart'
