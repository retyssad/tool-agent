export interface ChatCard {
  type: 'ask' | 'confirm' | 'run' | 'link' | 'error' | 'plan'
  question?: string
  options?: string[]
  action_id?: number
  tool?: string
  preview?: string
  run_id?: number
  label?: string
  url?: string
  message?: string
  steps?: PlanStep[]
}

export interface PlanStep {
  id: number
  label: string
  status: 'pending' | 'running' | 'done' | 'failed'
}

export interface TurnAction {
  id: number
  tool: string
  preview: string
  status: 'pending' | 'executed' | 'rejected' | 'failed'
  error?: string
  run_id?: number | null
}

export interface ChatTurn {
  id: number
  seq: number
  user_content: string
  assistant_content: string
  status:
    | 'queued'
    | 'running'
    | 'succeeded'
    | 'failed'
    | 'cancelled'
    | 'awaiting_confirmation'
  error: string
  plan: PlanStep[]
  cards: ChatCard[]
  run_ids: number[]
  child_run_ids: number[]
  current_run_id: number | null
  actions: TurnAction[]
  created_at: string
}

export interface ChatSessionSummary {
  id: number
  title: string
  turns: number
  created_at: string
  updated_at: string
}

export interface ChatSessionDetail {
  id: number
  title: string
  turns: ChatTurn[]
  active_turn: ChatTurn | null
  entries_count: number
}

export interface ChatTool {
  name: string
  description: string
  risk: 'read' | 'write' | 'destructive' | 'slow'
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
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const chatApi = {
  sessions: () => request<ChatSessionSummary[]>('/api/chat/sessions'),
  createSession: () =>
    request<ChatSessionSummary>('/api/chat/sessions', {
      method: 'POST',
      body: JSON.stringify({ title: '新对话' }),
    }),
  rename: (id: number, title: string) =>
    request<{ id: number; title: string }>(`/api/chat/sessions/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    }),
  detail: (id: number) => request<ChatSessionDetail>(`/api/chat/sessions/${id}`),
  remove: (id: number) =>
    request<{ ok: boolean }>(`/api/chat/sessions/${id}`, { method: 'DELETE' }),
  createTurn: (sessionId: number, content: string) =>
    request<{ turn_id: number; seq: number; status: string; run_id: number }>(
      `/api/chat/sessions/${sessionId}/turns`,
      { method: 'POST', body: JSON.stringify({ content }) },
    ),
  cancelTurn: (turnId: number) =>
    request<{ ok: boolean; note: string }>(`/api/chat/turns/${turnId}/cancel`, { method: 'POST' }),
  compact: (sessionId: number) =>
    request<{
      ok: boolean
      message?: string
      tokens_before?: number
      tokens_after?: number
      summary?: Record<string, string[]>
    }>(`/api/chat/sessions/${sessionId}/compact`, { method: 'POST' }),
  confirm: (actionId: number) =>
    request<{ ok: boolean; run_id: number; turn_id: number }>(
      `/api/chat/actions/${actionId}/confirm`,
      { method: 'POST' },
    ),
  reject: (actionId: number) =>
    request<{ ok: boolean }>(`/api/chat/actions/${actionId}/reject`, { method: 'POST' }),
  tools: () => request<ChatTool[]>('/api/chat/tools'),
}
