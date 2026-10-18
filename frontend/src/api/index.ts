export interface RunView {
  id: number
  kind: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted'
  title: string
  target: Record<string, unknown>
  prompt_tokens: number
  completion_tokens: number
  error: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  last_seq: number
  parent_run_id: number | null
}

export interface RunEvent {
  seq: number
  type: string
  payload: Record<string, any>
}

export interface RunStep {
  id: number
  seq: number
  agent: string
  model: string
  prompt_name: string
  prompt_hash: string
  request: Record<string, unknown>
  response: Record<string, unknown>
  tool_calls: string[]
  prompt_tokens: number
  completion_tokens: number
  latency_ms: number
  error: string
  created_at: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    ...init,
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      detail = body.detail ?? detail
    } catch {
      /* 保持默认错误文本 */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export interface Overview {
  jobs: {
    total: number
    by_status: Record<string, number>
    pending_parse: number
  }
  resumes: { drafts: number; versions: number; imported: number }
  claims: { total: number; pending: number; confirmed: number }
  projects: { total: number; evidence: number; scanning: number }
  evaluations: { total: number; avg_score: number | null }
  interviews: {
    total: number
    active: number
    completed: number
    avg_score: number | null
  }
  recent_jobs: { id: number; title: string; status: string; parse_status: string }[]
  recent_interviews: {
    id: number
    title: string
    status: string
    score: number | null
  }[]
}

export const api = {
  health: () => request<{ ok: boolean; db: any; redis: any; model: string }>('/api/system/health'),
  overview: () => request<Overview>('/api/system/overview'),
  settings: () => request<Record<string, any>>('/api/settings'),
  listRuns: (limit = 30) => request<RunView[]>(`/api/runs?limit=${limit}`),
  listChildRuns: (parentRunId: number) =>
    request<RunView[]>(`/api/runs?parent_run_id=${parentRunId}&limit=20`),
  getRun: (id: number) => request<RunView>(`/api/runs/${id}`),
  createRun: (body: Record<string, unknown>) =>
    request<RunView>('/api/runs', { method: 'POST', body: JSON.stringify(body) }),
  cancelRun: (id: number) => request<{ ok: boolean }>(`/api/runs/${id}/cancel`, { method: 'POST' }),
  events: (id: number, after = 0) => request<RunEvent[]>(`/api/runs/${id}/events?after=${after}`),
  steps: (id: number) => request<RunStep[]>(`/api/runs/${id}/steps`),
}
