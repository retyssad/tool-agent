export interface JobListItem {
  id: number
  title: string
  company: string
  location: string
  salary: string
  seniority: string
  status: 'saved' | 'applied' | 'interviewing' | 'rejected'
  parse_status: 'pending' | 'running' | 'succeeded' | 'failed'
  parse_error: string
  skills: string[]
  created_at: string
  updated_at: string
}

export interface RawJDView {
  id: number
  content: string
  source_url: string
  char_count: number
  created_at: string
}

export interface ParsedJD {
  title: string
  company: string
  location: string
  salary: string
  seniority: string
  employment_type: string
  responsibilities: string[]
  hard_requirements: string[]
  skills: string[]
  nice_to_have: string[]
  keywords: string[]
  summary: string
  ambiguities: string[]
}

export interface JobDetail extends JobListItem {
  parsed: Partial<ParsedJD>
  notes: string
  raw_jds: RawJDView[]
  parse_run_id: number | null
}

const JSON_HEADERS = { 'Content-Type': 'application/json' }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { ...JSON_HEADERS, ...(init?.headers ?? {}) },
    ...init,
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      detail = body.detail ?? detail
    } catch {
      /* 保留默认文本 */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const jobsApi = {
  list: (params: { status?: string; q?: string } = {}) => {
    const search = new URLSearchParams()
    if (params.status) search.set('status', params.status)
    if (params.q) search.set('q', params.q)
    const qs = search.toString()
    return request<JobListItem[]>(`/api/jobs${qs ? `?${qs}` : ''}`)
  },
  get: (id: number) => request<JobDetail>(`/api/jobs/${id}`),
  create: (body: { raw_text: string; source_url?: string; title_hint?: string }) =>
    request<JobDetail>('/api/jobs', { method: 'POST', body: JSON.stringify(body) }),
  update: (id: number, body: Record<string, unknown>) =>
    request<JobDetail>(`/api/jobs/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  reparse: (id: number) => request<JobDetail>(`/api/jobs/${id}/reparse`, { method: 'POST' }),
  remove: (id: number) => request<{ ok: boolean }>(`/api/jobs/${id}`, { method: 'DELETE' }),
}

export const jobStatusLabels: Record<string, string> = {
  saved: '收藏',
  applied: '已投',
  interviewing: '面试中',
  rejected: '挂',
}

export const jobStatusType: Record<string, 'default' | 'info' | 'warning' | 'error'> = {
  saved: 'default',
  applied: 'info',
  interviewing: 'warning',
  rejected: 'error',
}

export const parseStatusLabels: Record<string, string> = {
  pending: '排队中',
  running: '解析中',
  succeeded: '已解析',
  failed: '解析失败',
}
