export interface InterviewTurn {
  id: number
  idx: number
  kind: 'primary' | 'follow_up'
  stage: 'project' | 'job_fit' | 'behavior' | 'closing'
  question: string
  answer: string
  basis: Record<string, any>
  scores: Record<string, number>
  feedback: string
  skipped: boolean
}

export interface Progress {
  asked: number
  primary_asked: number
  plan_total: number
  answered: number
  skipped: number
  remaining: number
}

export interface InterviewReport {
  overall_score?: number
  dimension_scores?: Record<string, number>
  verdict?: string
  summary?: string
  strengths?: string[]
  weaknesses?: string[]
  red_flags?: string[]
  next_steps?: string[]
  question_review?: { idx: number; question: string; score: number; comment: string }[]
}

export interface InterviewSession {
  id: number
  job_id: number
  resume_kind: string
  draft_id: number | null
  imported_resume_id: number | null
  title: string
  status: 'active' | 'completed' | 'abandoned'
  pact: {
    focus?: string
    dimensions?: string[]
    questions?: {
      stage: string
      question: string
      intent: string
      basis: { claims: number[]; evidence_ids: number[]; jd: string }
      difficulty: number
      follow_ups: string[]
    }[]
  }
  plan_total: number
  plan_run_id: number | null
  report_run_id: number | null
  plan_error: string
  report: InterviewReport
  created_at: string
  updated_at: string
  turns?: InterviewTurn[]
  progress?: Progress
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

export const interviewsApi = {
  create: (body: {
    job_id: number
    resume_kind: 'draft' | 'imported'
    draft_id?: number
    imported_resume_id?: number
    title?: string
  }) => request<{ session_id: number; run_id: number }>('/api/interviews', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  list: () => request<InterviewSession[]>('/api/interviews'),
  get: (id: number) => request<InterviewSession>(`/api/interviews/${id}`),
  next: (id: number) =>
    request<{ action: 'ask' | 'finish'; turn?: InterviewTurn; progress: Progress }>(
      `/api/interviews/${id}/next`,
      { method: 'POST' },
    ),
  answer: (id: number, answer: string) =>
    request<{ ok: boolean; run_id: number; progress: Progress }>(`/api/interviews/${id}/answer`, {
      method: 'POST',
      body: JSON.stringify({ answer }),
    }),
  skip: (id: number, reason = '') =>
    request<{ ok: boolean; progress: Progress }>(`/api/interviews/${id}/skip`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),
  report: (id: number) =>
    request<{ run_id: number }>(`/api/interviews/${id}/report`, { method: 'POST' }),
  abandon: (id: number) =>
    request<{ ok: boolean }>(`/api/interviews/${id}/abandon`, { method: 'POST' }),
  remove: (id: number) => request<{ ok: boolean }>(`/api/interviews/${id}`, { method: 'DELETE' }),
}

export const stageLabels: Record<string, string> = {
  project: '项目深挖',
  job_fit: '岗位匹配',
  behavior: '行为面',
  closing: '收尾',
}

export const verdictLabels: Record<string, string> = {
  strong_hire: '强烈推荐',
  hire: '推荐',
  borderline: '待定',
  no_hire: '不推荐',
}

export const dimensionLabels: Record<string, string> = {
  fact_consistency: '事实一致性',
  technical_depth: '技术深度',
  job_match: '岗位匹配',
  structure: '表达结构',
  resilience: '追问韧性',
}
