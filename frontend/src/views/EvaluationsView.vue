<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton, NCard, NEmpty, NProgress, NSpin, NTag, useMessage,
} from 'naive-ui'
import {
  criticLabels, evaluationsApi, severityLabels, severityType, severityWeight,
  type Evaluation, type Finding,
} from '@/api/evaluations'
import { useRunsStore } from '@/stores/runs'

const router = useRouter()
const message = useMessage()
const runs = useRunsStore()

const items = ref<Evaluation[]>([])
const loading = ref(false)
let timer: number | undefined

async function refresh() {
  loading.value = true
  try {
    items.value = await evaluationsApi.list()
    const pending = items.value.some((e) => e.status === 'pending' || e.status === 'running')
    window.clearTimeout(timer)
    if (pending) timer = window.setTimeout(refresh, 3000)
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    loading.value = false
  }
}

onMounted(refresh)
onBeforeUnmount(() => window.clearTimeout(timer))

function scoreType(score: number) {
  if (score >= 80) return 'success'
  if (score >= 60) return 'warning'
  return 'error'
}

function open(e: Evaluation) {
  router.push(`/evaluations/${e.id}`)
}

const statusText: Record<string, string> = {
  pending: '排队中',
  running: '评价中',
  succeeded: '已完成',
  failed: '失败',
}
</script>

<template>
  <div class="page" style="max-width: 1100px">
    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 4px">
      <h1 class="page-title" style="margin: 0">简历评价</h1>
      <div style="flex: 1" />
      <NButton quaternary :loading="loading" @click="refresh">刷新</NButton>
    </div>
    <p class="page-desc">
      四个独立 critic 并行评审同一份简历：岗位匹配、成果量化、技术深度、表达结构。只出报告，不自动改简历。
    </p>

    <NSpin :show="loading">
      <NEmpty v-if="!items.length" description="还没有评价。打开一份生成简历，点「评价」发起。" />
      <div v-else style="display: grid; gap: 12px">
        <NCard v-for="e in items" :key="e.id" size="small" hoverable @click="open(e)">
          <div style="display: flex; align-items: center; gap: 12px">
            <div style="width: 64px; text-align: center">
              <div v-if="e.status === 'succeeded'" style="font-size: 26px; font-weight: 700"
                   :style="{ color: e.overall_score >= 80 ? '#1a7f43' : e.overall_score >= 60 ? '#a35c00' : '#b3261e' }">
                {{ e.overall_score }}
              </div>
              <div v-else style="font-size: 12px; color: #8a919c">{{ statusText[e.status] }}</div>
              <div style="font-size: 11px; color: #8a919c">总分</div>
            </div>
            <div style="flex: 1; min-width: 0">
              <div style="display: flex; gap: 10px; align-items: center; margin-bottom: 6px; flex-wrap: wrap">
                <strong>#{{ e.id }} 评价 · 草稿 #{{ e.draft_id }}</strong>
                <NTag size="tiny" :bordered="false">rev {{ e.draft_revision }}</NTag>
                <NTag v-if="e.job_id" size="tiny" :bordered="false">岗位 #{{ e.job_id }}</NTag>
              </div>
              <div style="display: flex; gap: 16px; flex-wrap: wrap">
                <span v-for="(score, key) in e.dimension_scores" :key="key" style="font-size: 12px; color: #5c6470">
                  {{ criticLabels[key as keyof typeof criticLabels] ?? key }}
                  <strong :style="{ color: (score ?? 0) >= 80 ? '#1a7f43' : (score ?? 0) >= 60 ? '#a35c00' : '#b3261e' }">{{ score }}</strong>
                </span>
                <span v-if="e.error" class="mono" style="color: #b3261e">{{ e.error.slice(0, 80) }}</span>
              </div>
              <div v-if="e.summary?.conclusion" style="font-size: 13px; color: #5c6470; margin-top: 6px">
                {{ e.summary.conclusion }}
              </div>
            </div>
            <span class="mono" style="color: #8a919c">{{ new Date(e.created_at).toLocaleString('zh-CN') }}</span>
          </div>
        </NCard>
      </div>
    </NSpin>
  </div>
</template>
