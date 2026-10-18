<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NButton, NIcon, NTag } from 'naive-ui'
import type { RunView } from '@/api'
import { api } from '@/api'
import { useRunsStore } from '@/stores/runs'

const props = defineProps<{ runId: number; title?: string }>()
const runs = useRunsStore()

const expanded = ref(false)
const detail = ref<RunView | null>(null)
const loading = ref(false)

const liveRun = computed(() => runs.runById(props.runId) ?? detail.value)
const status = computed(() => liveRun.value?.status ?? '')
const events = computed(() => runs.events[props.runId] ?? [])
const progress = computed(() => {
  // 折叠态只需要"最后在干什么"
  const last = events.value.filter((e) => e.type !== 'token.delta').at(-1)
  if (!last) return ''
  const p = last.payload ?? {}
  if (last.type === 'tool.called') return `正在调用 ${p.tool}`
  if (last.type === 'tool.result') return `${p.tool} ${p.ok === false ? '失败' : '完成'}`
  if (last.type === 'agent.started') return `${p.agent ?? 'agent'} 执行中`
  if (last.type === 'run.log') return String(p.message ?? '')
  return last.type
})

const isLive = computed(() => status.value === 'running' || status.value === 'queued')

const label = computed(() =>
  !status.value ? (loading.value ? '读取中' : '—') :
  status.value === 'running' ? '执行中' :
  status.value === 'queued' ? '排队中' :
  status.value === 'succeeded' ? '已完成' :
  status.value === 'failed' ? '失败' :
  status.value === 'cancelled' ? '已取消' : '已中断',
)

const tagType = computed(() => {
  if (status.value === 'succeeded') return 'success'
  if (status.value === 'failed') return 'error'
  if (status.value === 'running' || status.value === 'queued') return 'info'
  return 'default'
})

async function load() {
  loading.value = true
  try {
    detail.value = await api.getRun(props.runId)
    if (!runs.events[props.runId]?.length) await runs.loadHistory(props.runId)
    if (isLive.value) runs.connect(props.runId)
  } catch {
    /* 子 Run 可能已被清理 */
  } finally {
    loading.value = false
  }
}

function toggle() {
  expanded.value = !expanded.value
  if (expanded.value && !detail.value) void load()
}

onMounted(() => {
  // 子 Run 不一定在 runs 列表里（列表只取最近 30 条），先拿一次状态
  if (!runs.runById(props.runId)) void load()
  else if (isLive.value) runs.connect(props.runId)
})

watch(() => props.runId, () => {
  detail.value = null
  void load()
})

const visibleEvents = computed(() =>
  events.value.filter((e) => e.type !== 'token.delta').slice(-14),
)

onBeforeUnmount(() => {
  // 只断开自己发起的流；父组件可能还会用到同一个 run
  if (detail.value) runs.disconnect(props.runId)
})
</script>

<template>
  <div class="run-card" :class="{ 'run-card--live': isLive }">
    <div class="run-head">
      <NIcon size="14" color="#5c6470">
        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z" /></svg>
      </NIcon>
      <span class="run-title">{{ title || liveRun?.title || `Run #${runId}` }}</span>
      <span class="mono run-id">#{{ runId }}</span>
      <NTag size="tiny" :type="tagType" :bordered="false">{{ label }}</NTag>
      <div style="flex: 1" />
      <NButton size="tiny" quaternary @click="toggle">{{ expanded ? '收起' : '看进度' }}</NButton>
    </div>

    <div v-if="progress" class="run-progress">{{ progress }}</div>
    <div v-if="liveRun?.error" class="run-error">{{ liveRun.error }}</div>

    <div v-if="expanded" class="run-events">
      <div v-if="!visibleEvents.length" class="run-empty">暂无事件</div>
      <div v-for="event in visibleEvents" :key="event.seq" class="run-event">
        <span class="mono run-seq">{{ event.seq }}</span>
        <span class="mono run-type">{{ event.type }}</span>
        <span class="run-text">{{ JSON.stringify(event.payload).slice(0, 220) }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.run-card {
  border: 1px solid #e8eaee;
  border-radius: 8px;
  background: #fbfcfd;
  padding: 8px 10px;
  margin: 6px 0;
}
.run-card--live { border-color: #cfe0f7; background: #f7faff; }
.run-head { display: flex; align-items: center; gap: 8px; }
.run-title { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.run-id { color: #8a919c; }
.run-progress { margin-top: 4px; font-size: 12px; color: #5c6470; }
.run-error { margin-top: 4px; font-size: 12px; color: #b3261e; }
.run-events {
  margin-top: 8px;
  border-top: 1px dashed #e8eaee;
  padding-top: 6px;
  max-height: 220px;
  overflow: auto;
}
.run-event { display: flex; gap: 8px; font-size: 11px; padding: 2px 0; }
.run-seq { color: #a7adb6; min-width: 26px; text-align: right; }
.run-type { color: #2458b8; }
.run-text { color: #5c6470; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.run-empty { color: #8a919c; font-size: 12px; }
</style>
