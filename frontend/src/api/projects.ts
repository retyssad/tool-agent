export interface ProjectSummary {
  id: number
  name: string
  path: string
  role: string
  description: string
  tech_stack: string[]
  scan_status: 'pending' | 'running' | 'succeeded' | 'failed'
  scan_error: string
  scan_run_id: number | null
  scan_summary: Record<string, any>
  last_scanned_at: string | null
  created_at: string
  updated_at: string
}

export interface EvidenceRef {
  path: string
  line: number
  snippet: string
}

export interface ProjectEvidence {
  id: number
  statement: string
  refs: EvidenceRef[]
  module: string
  category: 'architecture' | 'feature' | 'quality' | 'data' | 'ops'
  capabilities: string[]
}

export interface GuideMeta {
  id: number
  kind: string
  title: string
  chars: number
  created_at: string
}

export interface ProjectDetail extends ProjectSummary {
  evidence: ProjectEvidence[]
  guides: GuideMeta[]
  interview_questions: { question: string; angle: string; hint: string }[]
  weak_spots: string[]
  skipped_dirs: string[]
}

export interface EvidenceSource {
  path: string
  exists: boolean
  line: number
  total_lines?: number
  excerpt?: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  })
  if (!res.ok) {
    let message = `HTTP ${res.status}`
    try {
      const body = await res.json()
      message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
    } catch {
      /* 保留默认 */
    }
    throw new Error(message)
  }
  return res.json() as Promise<T>
}

export const projectsApi = {
  list: () => request<ProjectSummary[]>('/api/projects'),
  create: (body: { name: string; path: string; role?: string; description?: string }) =>
    request<ProjectSummary>('/api/projects', { method: 'POST', body: JSON.stringify(body) }),
  get: (id: number) => request<ProjectDetail>(`/api/projects/${id}`),
  patch: (id: number, body: { name?: string; role?: string; description?: string }) =>
    request<ProjectSummary>(`/api/projects/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  remove: (id: number) => request<{ ok: boolean }>(`/api/projects/${id}`, { method: 'DELETE' }),
  scan: (id: number) => request<{ run_id: number }>(`/api/projects/${id}/scan`, { method: 'POST' }),
  guide: (id: number, kind = 'interview') =>
    request<{ run_id: number }>(`/api/projects/${id}/guide`, {
      method: 'POST',
      body: JSON.stringify({ kind }),
    }),
  guideContent: (id: number, guideId: number) =>
    request<{ id: number; title: string; content_md: string; created_at: string }>(
      `/api/projects/${id}/guides/${guideId}`,
    ),
  evidenceSource: (id: number, evidenceId: number) =>
    request<{ evidence_id: number; files: EvidenceSource[] }>(
      `/api/projects/${id}/evidence/${evidenceId}/source`,
    ),
}

export const categoryLabels: Record<string, string> = {
  architecture: '架构设计',
  feature: '功能实现',
  quality: '质量保障',
  data: '数据处理',
  ops: '运维部署',
}

export const categoryType: Record<string, 'info' | 'success' | 'warning' | 'default' | 'error'> = {
  architecture: 'info',
  feature: 'success',
  quality: 'warning',
  data: 'default',
  ops: 'error',
}

export const scanStatusLabels: Record<string, string> = {
  pending: '排队中',
  running: '扫描中',
  succeeded: '已完成',
  failed: '失败',
}
