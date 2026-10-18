<script setup lang="ts">
import { computed, ref } from 'vue'
import { NButton, NEmpty, NInput, NTag, NTimeline, NTimelineItem, NCollapse, NCollapseItem, useMessage } from 'naive-ui'
import { useRunsStore } from '@/stores/runs'

const runs = useRunsStore()
const message = useMessage()
const title = ref('')
const creating = ref(false)

const statusText: Record<string, string> = {
  queued: '排队中',
  running: '执行中',
  succeeded: '成功',
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断',
}

const currentEvents = computed(() => {
  if (runs.activeRunId == null) return []
  return runs.events[runs.activeRunId] ?? []
})

const activeRun = computed(() => runs.runs.find((r) => r.id === runs.activeRunId))

function summarize(event: { type: string; payload: Record<string, any> }): string {
  const p = event.payload ?? {}
  switch (event.type) {
    case 'run.log': return p.message ?? ''
    case 'agent.started': return `${p.agent ?? ''} 开始`
    case 'agent.finished': return `${p.agent ?? ''} 结束`
    case 'tool.called': return `${p.tool} ${JSON.stringify(p.args ?? {})}`
    case 'tool.result': return `${p.tool} ${p.ok ? '成功' : '失败'}${p.error ? ': ' + p.error : ''}`
    case 'token.delta': return p.text ?? ''
    case 'step.recorded': return `step#${p.seq} ${p.agent} · ${p.tokens} tokens · ${p.latency_ms}ms`
    case 'run.finished': return `结束：${p.status}（${p.prompt_tokens}+${p.completion_tokens} tokens）`
    case 'run.started': return `开始：${p.kind ?? ''}`
    default: return JSON.stringify(p).slice(0, 120)
  }
}

async function startFake() {
  creating.value = true
  try {
    await runs.create('fake', title.value || '假任务')
    title.value = ''
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    creating.value = false
  }
}

async function pick(id: number) {
  runs.openRun(id)
  runs.disconnect(id)
  await runs.loadHistory(id)
  const run = runs.runs.find((r) => r.id === id)
  if (run && (run.status === 'running' || run.status === 'queued')) runs.connect(id)
  else if (run && run.status === 'queued') runs.connect(id)
}

async function cancel() {
  if (runs.activeRunId == null) return
  try {
    await runs.cancel(runs.activeRunId)
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}
</script>

<template>
  <div>
    <div style="display: flex; gap: 8px; margin-bottom: 16px">
      <NInput v-model:value="title" placeholder="任务标题（可选）" size="small" />
      <NButton type="primary" size="small" :loading="creating" @click="startFake">发起 fake run</NButton>
    </div>

    <NCollapse :default-expanded-names="['runs']" style="margin-bottom: 12px">
      <NCollapseItem title="历史 Run" name="runs">
        <NEmpty v-if="!runs.runs.length" description="暂无 Run" size="small" />
        <div
          v-for="run in runs.runs"
          :key="run.id"
          class="event-row"
          style="cursor: pointer"
          :style="{ background: run.id === runs.activeRunId ? '#f2f6ff' : 'transparent' }"
          @click="pick(run.id)"
        >
          <span class="mono" style="color: #8a919c">#{{ run.id }}</span>
          <NTag size="tiny" :class="`tag-${run.status}`">{{ statusText[run.status] ?? run.status }}</NTag>
          <span style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">{{ run.title }}</span>
          <span class="mono" style="color: #8a919c">{{ run.last_seq }} ev</span>
        </div>
      </NCollapseItem>
    </NCollapse>

    <template v-if="activeRun">
      <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px">
        <strong>#{{ activeRun.id }} {{ activeRun.title }}</strong>
        <NTag size="small" :class="`tag-${activeRun.status}`">{{ statusText[activeRun.status] }}</NTag>
        <span class="mono" style="color: #8a919c">
          {{ activeRun.prompt_tokens }}+{{ activeRun.completion_tokens }} tokens
        </span>
        <NButton
          v-if="activeRun.status === 'running' || activeRun.status === 'queued'"
          size="tiny"
          quaternary
          type="error"
          @click="cancel"
        >取消</NButton>
      </div>
      <div v-if="activeRun.error" class="mono" style="color: #b3261e; margin-bottom: 8px">{{ activeRun.error }}</div>
      <div style="max-height: calc(100vh - 260px); overflow: auto">
        <NTimeline>
          <NTimelineItem
            v-for="event in currentEvents"
            :key="event.seq"
            :type="event.type === 'run.finished' ? 'success' : event.type === 'run.log' ? 'info' : 'default'"
            :title="`${event.seq} · ${event.type}`"
          >
            <div class="mono" style="white-space: pre-wrap; word-break: break-all">{{ summarize(event) }}</div>
          </NTimelineItem>
        </NTimeline>
      </div>
    </template>
    <NEmpty v-else description="选择或发起一个 Run" />
  </div>
</template>
