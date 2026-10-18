import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api, type RunEvent, type RunView } from '@/api'

interface StreamState {
  source: EventSource | null
  lastSeq: number
  reconnects: number
}

const STREAM_TYPES = [
  'run.started', 'run.log', 'agent.started', 'agent.finished',
  'tool.called', 'tool.result', 'token.delta', 'artifact.created',
  'step.recorded', 'run.finished',
  'chat.plan', 'chat.message', 'chat.compaction', 'chat.investigation',
  'chat.confirmation',
]

export const useRunsStore = defineStore('runs', () => {
  const runs = ref<RunView[]>([])
  const activeRunId = ref<number | null>(null)
  const events = ref<Record<number, RunEvent[]>>({})
  // 流式文本：只存在于内存，不落库；run.finished/chat.message 后清掉
  const deltas = ref<Record<number, string>>({})
  // run 结束的信号，供助手页触发一次状态同步
  const finished = ref<Record<number, number>>({})
  const drawerOpen = ref(false)
  const loading = ref(false)
  const streams = new Map<number, StreamState>()

  async function refresh() {
    loading.value = true
    try {
      runs.value = await api.listRuns()
    } finally {
      loading.value = false
    }
  }

  async function create(kind = 'fake', title = '') {
    const run = await api.createRun({ kind, title })
    runs.value = [run, ...runs.value]
    openRun(run.id)
    connect(run.id)
    return run
  }

  function openRun(id: number) {
    activeRunId.value = id
    drawerOpen.value = true
    if (!events.value[id]) events.value[id] = []
  }

  /** 助手页用：连上轮次 Run，但不打开 Run 抽屉。 */
  function openTurnRun(id: number) {
    if (!events.value[id]) events.value[id] = []
    connect(id)
  }

  function appendEvent(id: number, event: RunEvent) {
    const list = (events.value[id] ??= [])
    if (list.some((e) => e.seq === event.seq)) return
    list.push(event)
    list.sort((a, b) => a.seq - b.seq)
    const run = runs.value.find((r) => r.id === id)
    if (run) run.last_seq = Math.max(run.last_seq, event.seq)
    if (event.type === 'agent.started') {
      // 新一轮模型输出开始：清掉上一轮的流式文本，避免多轮叙述粘在一起
      deltas.value[id] = ''
    }
    if (event.type === 'chat.message') {
      deltas.value[id] = ''
    }
    if (event.type === 'run.finished') {
      const status = event.payload?.status
      if (run && status) run.status = status
      if (run) {
        run.prompt_tokens = event.payload?.prompt_tokens ?? run.prompt_tokens
        run.completion_tokens = event.payload?.completion_tokens ?? run.completion_tokens
        run.error = event.payload?.error ?? run.error
      }
      finished.value[id] = (finished.value[id] ?? 0) + 1
      disconnect(id)
    }
  }

  function connect(id: number) {
    const existing = streams.get(id)
    if (existing?.source) return
    const state: StreamState = existing ?? { source: null, lastSeq: 0, reconnects: 0 }
    state.lastSeq = Math.max(
      state.lastSeq,
      events.value[id]?.at(-1)?.seq ?? 0,
    )
    const source = new EventSource(`/api/runs/${id}/stream?last_event_id=${state.lastSeq}`)
    state.source = source
    streams.set(id, state)

    const handle = (raw: MessageEvent) => {
      try {
        const payload = JSON.parse(raw.data)
        if (raw.type === 'token.delta' || payload.transient) {
          // 流式分片没有 seq，只拼内存文本，不进事件列表
          deltas.value[id] = (deltas.value[id] ?? '') + (payload.text ?? '')
          return
        }
        const seq = Number(raw.lastEventId || payload.seq || 0)
        state.lastSeq = Math.max(state.lastSeq, seq)
        appendEvent(id, { seq, type: raw.type, payload })
      } catch {
        /* 忽略非 JSON 数据 */
      }
    }

    STREAM_TYPES.forEach((t) => source.addEventListener(t, handle as EventListener))
    source.addEventListener('end', () => disconnect(id))
    source.onerror = () => {
      state.reconnects += 1
      if (state.reconnects > 5) disconnect(id)
    }
  }

  function disconnect(id: number) {
    const state = streams.get(id)
    state?.source?.close()
    streams.delete(id)
  }

  async function cancel(id: number) {
    await api.cancelRun(id)
    const run = runs.value.find((r) => r.id === id)
    if (run) run.status = 'cancelled'
  }

  async function loadHistory(id: number) {
    const list = await api.events(id)
    events.value[id] = list
    if (list.length) {
      const state = streams.get(id) ?? { source: null, lastSeq: 0, reconnects: 0 }
      state.lastSeq = list.at(-1)!.seq
      streams.set(id, state)
    }
  }

  function runById(id: number | null | undefined) {
    if (id == null) return undefined
    return runs.value.find((r) => r.id === id)
  }

  return {
    runs, activeRunId, events, deltas, finished, drawerOpen, loading,
    refresh, create, openRun, openTurnRun, connect, disconnect, cancel,
    loadHistory, runById,
  }
})
