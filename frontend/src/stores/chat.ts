import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { chatApi, type ChatSessionDetail, type ChatSessionSummary, type ChatTurn } from '@/api/chat'
import { useRunsStore } from '@/stores/runs'

/**
 * 对话状态：会话列表 + 当前会话轮次。
 *
 * 一轮的步骤不在这里维护，直接复用 runs store 的事件流：
 * 每个轮次都有 current_run_id，事件按 run_id 存在 runs.events。
 */
export const useChatStore = defineStore('chat', () => {
  const sessions = ref<ChatSessionSummary[]>([])
  const activeId = ref<number | null>(null)
  const detail = ref<ChatSessionDetail | null>(null)
  const loading = ref(false)
  const sending = ref(false)
  const error = ref('')

  const turns = computed<ChatTurn[]>(() => detail.value?.turns ?? [])
  const activeTurn = computed(() => {
    const list = turns.value
    for (let i = list.length - 1; i >= 0; i -= 1) {
      const t = list[i]
      if (t.status === 'queued' || t.status === 'running' || t.status === 'awaiting_confirmation') {
        return t
      }
    }
    return null
  })

  const pendingActions = computed(() =>
    turns.value.flatMap((t) =>
      t.actions.filter((a) => a.status === 'pending').map((a) => ({ ...a, turn_id: t.id })),
    ),
  )

  async function refreshSessions() {
    sessions.value = await chatApi.sessions()
  }

  async function open(id: number) {
    loading.value = true
    error.value = ''
    try {
      activeId.value = id
      detail.value = await chatApi.detail(id)
      window.localStorage.setItem('chat.sessionId', String(id))
      await attachStream()
    } finally {
      loading.value = false
    }
  }

  async function ensureSession(): Promise<number> {
    if (activeId.value != null) return activeId.value
    const created = await chatApi.createSession()
    sessions.value = [created, ...sessions.value]
    activeId.value = created.id
    detail.value = { id: created.id, title: created.title, turns: [], active_turn: null, entries_count: 0 }
    window.localStorage.setItem('chat.sessionId', String(created.id))
    return created.id
  }

  async function bootstrap() {
    await refreshSessions()
    const stored = Number(window.localStorage.getItem('chat.sessionId') || '0')
    const target = sessions.value.find((s) => s.id === stored) ?? sessions.value[0]
    if (target) await open(target.id)
    else {
      activeId.value = null
      detail.value = null
    }
  }

  async function newSession() {
    const created = await chatApi.createSession()
    sessions.value = [created, ...sessions.value]
    await open(created.id)
  }

  async function removeSession(id: number) {
    await chatApi.remove(id)
    sessions.value = sessions.value.filter((s) => s.id !== id)
    if (activeId.value === id) {
      activeId.value = null
      detail.value = null
      const next = sessions.value[0]
      if (next) await open(next.id)
      else await newSession()
    }
  }

  async function renameSession(id: number, title: string) {
    const row = await chatApi.rename(id, title)
    const item = sessions.value.find((s) => s.id === id)
    if (item) item.title = row.title
    if (detail.value?.id === id) detail.value.title = row.title
  }

  async function send(content: string) {
    const text = content.trim()
    if (!text || sending.value) return
    sending.value = true
    error.value = ''
    try {
      const sessionId = await ensureSession()
      const created = await chatApi.createTurn(sessionId, text)
      // 先乐观插入一条 executing 轮次，SSE 到了再刷新
      detail.value?.turns.push({
        id: created.turn_id,
        seq: created.seq,
        user_content: text,
        assistant_content: '',
        status: 'running',
        error: '',
        plan: [],
        cards: [],
        run_ids: [created.run_id],
        child_run_ids: [],
        current_run_id: created.run_id,
        actions: [],
        created_at: new Date().toISOString(),
      })
      const runs = useRunsStore()
      runs.openTurnRun(created.run_id)
      await refreshSessions()
    } catch (err) {
      error.value = String(err instanceof Error ? err.message : err)
      throw err
    } finally {
      sending.value = false
    }
  }

  async function attachStream() {
    const runs = useRunsStore()
    // 历史轮次的步骤来自事件表，打开会话时补齐；只补最近 10 轮，避免一次拉太多
    const recent = turns.value.slice(-10)
    for (const turn of recent) {
      for (const runId of turn.run_ids) {
        if (!runs.events[runId]?.length) {
          await runs.loadHistory(runId).catch(() => {})
        }
      }
      if (
        turn.current_run_id != null &&
        (turn.status === 'running' || turn.status === 'queued')
      ) {
        runs.connect(turn.current_run_id)
      }
    }
  }

  /** 轮次状态变化（由 SSE 触发），静默拉一次详情。 */
  async function syncTurn(turnId: number) {
    if (activeId.value == null) return
    const fresh = await chatApi.detail(activeId.value)
    detail.value = fresh
    const turn = fresh.turns.find((t) => t.id === turnId)
    if (turn?.current_run_id != null) {
      const runs = useRunsStore()
      if (turn.status === 'running' || turn.status === 'queued') runs.connect(turn.current_run_id)
    }
    await refreshSessions()
  }

  async function confirm(actionId: number) {
    const result = await chatApi.confirm(actionId)
    if (detail.value) {
      for (const turn of detail.value.turns) {
        const action = turn.actions.find((a) => a.id === actionId)
        if (action) {
          action.status = 'executed'
          turn.status = 'running'
          turn.current_run_id = result.run_id
          turn.run_ids = [...turn.run_ids, result.run_id]
        }
      }
    }
    const runs = useRunsStore()
    runs.openTurnRun(result.run_id)
  }

  async function reject(actionId: number) {
    await chatApi.reject(actionId)
    if (activeId.value != null) {
      detail.value = await chatApi.detail(activeId.value)
    }
  }

  async function cancel(turnId: number) {
    await chatApi.cancelTurn(turnId)
    if (activeId.value != null) detail.value = await chatApi.detail(activeId.value)
  }

  async function compact() {
    if (activeId.value == null) return null
    const result = await chatApi.compact(activeId.value)
    if (activeId.value != null) detail.value = await chatApi.detail(activeId.value)
    return result
  }

  return {
    sessions, activeId, detail, loading, sending, error,
    turns, activeTurn, pendingActions,
    refreshSessions, open, bootstrap, newSession, removeSession, renameSession,
    send, syncTurn, confirm, reject, cancel, compact, attachStream,
  }
})
