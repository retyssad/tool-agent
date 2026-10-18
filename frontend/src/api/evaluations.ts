export type Severity = 'high' | 'medium' | 'low' | 'good'
export type CriticKey = 'jd_match' | 'quantification' | 'technical_depth' | 'clarity'

export interface Finding {
  id: number
  critic: CriticKey
  severity: Severity
  title: string
  detail: string
  where: string
  excerpt: string
  suggestion: string
  claim_ids: number[]
}

export interface DimensionScores {
  jd_match?: number
  quantification?: number
  technical_depth?: number
  clarity?: number
}

export interface EvaluationSummary {
  conclusion?: string
  strengths?: string[]
  priorities?: { title: string; suggestion: string; where: string }[]
  failed_critics?: string[]
}

export interface Evaluation {
  id: number
  draft_id: number
  draft_revision: number
  job_id: number | null
  run_id: number | null
  status: 'pending' | 'running' | 'succeeded' | 'failed'
  overall_score: number
  dimension_scores: DimensionScores
  summary: EvaluationSummary
  error: string
  created_at: string
  updated_at: string
  findings?: Finding[]
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
      message =
        typeof detail === 'string'
          ? detail
          : detail?.message
            ? String(detail.message)
            : JSON.stringify(detail)
    } catch {
      /* 保留默认文本 */
    }
    throw new Error(message)
  }
  return res.json() as Promise<T>
}

export const evaluationsApi = {
  create: (draftId: number) =>
    request<{ evaluation_id: number; run_id: number }>('/api/evaluations', {
      method: 'POST',
      body: JSON.stringify({ draft_id: draftId }),
    }),
  list: (params: { draft_id?: number; job_id?: number } = {}) => {
    const search = new URLSearchParams()
    if (params.draft_id) search.set('draft_id', String(params.draft_id))
    if (params.job_id) search.set('job_id', String(params.job_id))
    const qs = search.toString()
    return request<Evaluation[]>(`/api/evaluations${qs ? `?${qs}` : ''}`)
  },
  get: (id: number) => request<Evaluation>(`/api/evaluations/${id}`),
  remove: (id: number) => request<{ ok: boolean }>(`/api/evaluations/${id}`, { method: 'DELETE' }),
}

export const criticLabels: Record<CriticKey, string> = {
  jd_match: '岗位匹配',
  quantification: '成果量化',
  technical_depth: '技术深度',
  clarity: '表达与结构',
}

export const severityLabels: Record<Severity, string> = {
  high: '必须改',
  medium: '建议改',
  low: '可选',
  good: '做得好',
}

export const severityType: Record<Severity, 'error' | 'warning' | 'default' | 'success'> = {
  high: 'error',
  medium: 'warning',
  low: 'default',
  good: 'success',
}

export const severityWeight: Record<Severity, number> = {
  high: 0,
  medium: 1,
  low: 2,
  good: 3,
}
