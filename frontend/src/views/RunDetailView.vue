<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { NButton, NCard, NDataTable, NSpin, NTag, NTimeline, NTimelineItem, useMessage } from 'naive-ui'
import { api, type RunStep } from '@/api'
import { useRunsStore } from '@/stores/runs'

const route = useRoute()
const runs = useRunsStore()
const message = useMessage()
const steps = ref<RunStep[]>([])
const loading = ref(true)

const runId = computed(() => Number(route.params.id))
const run = computed(() => runs.runs.find((r) => r.id === runId.value))
const events = computed(() => runs.events[runId.value] ?? [])

const statusText: Record<string, string> = {
  queued: '排队中', running: '执行中', succeeded: '成功',
  failed: '失败', cancelled: '已取消', interrupted: '已中断',
}

const stepColumns = [
  { title: '#', key: 'seq', width: 50 },
  { title: 'Agent', key: 'agent', width: 120 },
  { title: '模型', key: 'model', width: 140 },
  { title: 'Prompt', key: 'prompt_hash', width: 110 },
  { title: 'Tokens', key: 'tokens', width: 90 },
  { title: '耗时', key: 'latency_ms', width: 80 },
  { title: '工具', key: 'tool_calls', ellipsis: true },
  { title: '错误', key: 'error', ellipsis: true },
]

const stepRows = computed(() =>
  steps.value.map((s) => ({
    ...s,
    tokens: s.prompt_tokens + s.completion_tokens,
    latency_ms: `${s.latency_ms}ms`,
    prompt_hash: s.prompt_hash ? s.prompt_hash.slice(0, 8) : (s.prompt_name || '—'),
    tool_calls: s.tool_calls.join(', '),
  })),
)

async function loadSteps() {
  steps.value = await api.steps(runId.value).catch(() => [])
}

onMounted(async () => {
  loading.value = true
  try {
    if (!runs.runs.length) await runs.refresh()
    runs.openRun(runId.value)
    await runs.loadHistory(runId.value)
    const current = runs.runs.find((r) => r.id === runId.value)
    if (current && (current.status === 'running' || current.status === 'queued')) {
      runs.connect(runId.value)
    }
    await loadSteps()
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    loading.value = false
  }
})

// run 结束后刷新一次步骤表，并同步该 run 的最新状态
watch(
  () => run.value?.status,
  async (status) => {
    if (status && status !== 'running' && status !== 'queued') {
      await loadSteps()
      const fresh = await api.getRun(runId.value).catch(() => null)
      if (fresh && run.value) {
        run.value.prompt_tokens = fresh.prompt_tokens
        run.value.completion_tokens = fresh.completion_tokens
        run.value.error = fresh.error
      }
    }
  },
)
</script>

<template>
  <div class="page">
    <h1 class="page-title">Run #{{ runId }}</h1>
    <p class="page-desc">
      <NTag v-if="run" size="small" :class="`tag-${run.status}`">{{ statusText[run.status] }}</NTag>
      <span v-if="run" style="margin-left: 8px">{{ run.title }}</span>
      <span v-if="run" class="mono" style="margin-left: 12px; color: #8a919c">
        {{ run.prompt_tokens }}+{{ run.completion_tokens }} tokens
      </span>
    </p>

    <NSpin :show="loading">
      <NCard size="small" title="事件流" style="margin-bottom: 16px">
        <NTimeline>
          <NTimelineItem
            v-for="event in events"
            :key="event.seq"
            :title="`${event.seq} · ${event.type}`"
          >
            <div class="mono" style="white-space: pre-wrap; word-break: break-all">
              {{ JSON.stringify(event.payload) }}
            </div>
          </NTimelineItem>
        </NTimeline>
      </NCard>

      <NCard size="small" title="模型调用步骤（回放）">
        <NDataTable :columns="stepColumns" :data="stepRows" size="small" :bordered="false" />
      </NCard>
    </NSpin>
  </div>
</template>
