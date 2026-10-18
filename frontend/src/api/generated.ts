export interface TemplateMeta {
  key: string
  name: string
  description: string
  layout: 'flow' | 'sidebar' | 'two-column' | 'asu'
  body_classes: string[]
  two_page: boolean
}

export interface Profile {
  name: string
  headline: string
  phone: string
  email: string
  location: string
  wechat: string
  birth_year: string
  political_status: string
  english_level: string
  github: string
}

export interface Claim {
  id: number
  ledger_id: number
  statement: string
  source_type: string
  source_ref: string
  kind: string
  organization: string
  role: string
  period: string
  tags: string[]
  variants: string[]
  status: 'pending' | 'confirmed' | 'expired' | 'rejected'
  detail: string
  created_at: string
  updated_at: string
}

export interface Ledger {
  id: number
  name: string
  kind: 'personal' | 'project'
  project_id: number | null
  claim_count: number
  confirmed_count: number
  created_at: string
  updated_at: string
}

export interface GateIssue {
  code: string
  message: string
  where?: string
  claim_id?: number
  status?: string
  text?: string
}

export interface GateResult {
  ok: boolean
  errors: GateIssue[]
}

export interface ResumeBullet {
  label: string
  text: string
  claim_ids: number[]
}

export interface ResumeEntry {
  dates: string
  bullets: ResumeBullet[]
  school?: string
  major_degree?: string
  organization?: string
  role?: string
  name?: string
  summary?: string
}

export interface ResumeLine {
  label: string
  text: string
  claim_ids: number[]
}

export interface ResumeJson {
  schema_version: number
  profile: Profile
  education: ResumeEntry[]
  experience: ResumeEntry[]
  projects: ResumeEntry[]
  skills: ResumeLine[]
  campus: ResumeEntry[]
  summary: ResumeLine[]
}

export interface Draft {
  id: number
  title: string
  template: string
  target_job_id: number | null
  ledger_id: number
  revision: number
  run_id: number | null
  resume_json: Partial<ResumeJson>
  gate: GateResult | null
  created_at: string
  updated_at: string
}

export interface DraftSummary {
  id: number
  title: string
  template: string
  target_job_id: number | null
  ledger_id: number
  revision: number
  created_at: string
  updated_at: string
}

export interface Version {
  id: number
  title: string
  template: string
  target_job_id: number | null
  ledger_id: number
  version_no: number
  gate_status: string
  created_at: string
  pdf_url: string
  html_url: string
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
      const detail = body.detail ?? body
      if (typeof detail === 'string') {
        message = detail
      } else if (detail?.message) {
        // 门禁错误：{message, errors:[...]}
        const first = Array.isArray(detail.errors) ? detail.errors[0] : null
        message = first
          ? `${detail.message}：${first.message}${detail.errors.length > 1 ? ` 等 ${detail.errors.length} 处` : ''}`
          : String(detail.message)
      } else if (Array.isArray(detail)) {
        message = detail.map((e: any) => e.msg ?? JSON.stringify(e)).join('；')
      } else {
        message = JSON.stringify(detail)
      }
    } catch {
      /* 保留默认文本 */
    }
    throw new Error(message)
  }
  return res.json() as Promise<T>
}

export const generatedApi = {
  templates: () => request<TemplateMeta[]>('/api/templates'),

  getProfile: () => request<Profile>('/api/profile'),
  saveProfile: (body: Profile) =>
    request<Profile>('/api/profile', { method: 'PUT', body: JSON.stringify(body) }),

  ledgers: (kind?: 'personal' | 'project') =>
    request<Ledger[]>(`/api/ledgers${kind ? `?kind=${kind}` : ''}`),
  createLedger: (name: string) =>
    request<Ledger>('/api/ledgers', { method: 'POST', body: JSON.stringify({ name }) }),
  renameLedger: (id: number, name: string) =>
    request<Ledger>(`/api/ledgers/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) }),
  deleteLedger: (id: number) =>
    request<{ ok: boolean }>(`/api/ledgers/${id}`, { method: 'DELETE' }),
  importProjectEvidence: (ledgerId: number, projectId: number, evidenceIds: number[] = []) =>
    request<{ created: number; skipped: number }>(
      `/api/ledgers/${ledgerId}/import-project-evidence`,
      { method: 'POST', body: JSON.stringify({ project_id: projectId, evidence_ids: evidenceIds }) },
    ),
  copyClaims: (ledgerId: number, claimIds: number[]) =>
    request<{ created: number; skipped: number }>(
      `/api/ledgers/${ledgerId}/copy-claims`,
      { method: 'POST', body: JSON.stringify({ claim_ids: claimIds }) },
    ),

  claims: (ledgerId: number, params: { status?: string; kind?: string } = {}) => {
    const search = new URLSearchParams()
    if (params.status) search.set('status', params.status)
    if (params.kind) search.set('kind', params.kind)
    const qs = search.toString()
    return request<Claim[]>(`/api/ledgers/${ledgerId}/claims${qs ? `?${qs}` : ''}`)
  },
  createClaim: (ledgerId: number, body: Partial<Claim>) =>
    request<Claim>(`/api/ledgers/${ledgerId}/claims`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  patchClaim: (claimId: number, body: Partial<Claim>) =>
    request<Claim>(`/api/claims/${claimId}`, { method: 'PATCH', body: JSON.stringify(body) }),
  bulkStatus: (claimIds: number[], status: string) =>
    request<{ updated: number }>('/api/claims/bulk-status', {
      method: 'POST',
      body: JSON.stringify({ claim_ids: claimIds, status }),
    }),
  deleteClaim: (claimId: number) =>
    request<{ ok: boolean }>(`/api/claims/${claimId}`, { method: 'DELETE' }),
  extractFromResume: (importedResumeId: number, ledgerId?: number) =>
    request<{ run_id: number; ledger_id: number }>('/api/claims/extract-from-resume', {
      method: 'POST',
      body: JSON.stringify({ imported_resume_id: importedResumeId, ledger_id: ledgerId }),
    }),

  drafts: () => request<DraftSummary[]>('/api/drafts'),
  createDraft: (body: { ledger_id: number; target_job_id?: number | null; title?: string; template?: string }) =>
    request<Draft>('/api/drafts', { method: 'POST', body: JSON.stringify(body) }),
  getDraft: (id: number) => request<Draft>(`/api/drafts/${id}`),
  patchDraft: (id: number, body: { title?: string; template?: string; resume_json?: unknown }) =>
    request<Draft>(`/api/drafts/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  generateDraft: (id: number) =>
    request<{ run_id: number }>(`/api/drafts/${id}/generate`, { method: 'POST' }),
  deleteDraft: (id: number) =>
    request<{ ok: boolean }>(`/api/drafts/${id}`, { method: 'DELETE' }),
  previewUrl: (id: number, template?: string) =>
    `/api/drafts/${id}/preview${template ? `?template=${encodeURIComponent(template)}` : ''}`,
  exportDraft: (id: number, template?: string) =>
    request<{ version_id: number; version_no: number; pdf_url: string; html_url: string }>(
      `/api/drafts/${id}/export${template ? `?template=${encodeURIComponent(template)}` : ''}`,
      { method: 'POST' },
    ),

  versions: (jobId?: number) =>
    request<Version[]>(`/api/versions${jobId ? `?job_id=${jobId}` : ''}`),
  getVersion: (id: number) => request<Version & { resume_json: ResumeJson; gate_errors: GateIssue[] }>(`/api/versions/${id}`),
  rerenderVersion: (id: number, template: string) =>
    request<{ ok: boolean; template: string }>(
      `/api/versions/${id}/re-render?template=${encodeURIComponent(template)}`,
      { method: 'POST' },
    ),
  deleteVersion: (id: number) => request<{ ok: boolean }>(`/api/versions/${id}`, { method: 'DELETE' }),
}

export const claimKindLabels: Record<string, string> = {
  experience: '工作经历',
  project: '项目',
  education: '教育',
  skill: '技能',
  campus: '校园',
  award: '奖项',
  other: '其他',
}

export const claimStatusLabels: Record<string, string> = {
  pending: '待确认',
  confirmed: '已确认',
  expired: '已过期',
  rejected: '不采用',
}

export const claimStatusType: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  pending: 'warning',
  confirmed: 'success',
  expired: 'default',
  rejected: 'error',
}
