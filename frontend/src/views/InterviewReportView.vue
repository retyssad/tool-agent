<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton, NCard, NGrid, NGridItem, NProgress, NSpin, NTag, useMessage,
} from 'naive-ui'
import {
  dimensionLabels, interviewsApi, verdictLabels, type InterviewSession,
} from '@/api/interviews'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const sessionId = computed(() => Number(route.params.id))
const session = ref<InterviewSession | null>(null)
const loading = ref(true)

onMounted(async () => {
  try {
    session.value = await interviewsApi.get(sessionId.value)
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    loading.value = false
  }
})

const report = computed(() => session.value?.report ?? {})

function color(v?: number) {
  if (v == null) return '#8a919c'
  if (v >= 80) return '#1a7f43'
  if (v >= 55) return '#a35c00'
  return '#b3261e'
}

const verdictType = computed(() => {
  const v = report.value.verdict
  if (v === 'strong_hire' || v === 'hire') return 'success'
  if (v === 'borderline') return 'warning'
  return 'error'
})

function scoreForTurn(idx: number) {
  const t = session.value?.turns?.find((x) => x.idx === idx)
  if (!t || !t.scores) return null
  const values = Object.values(t.scores)
  if (!values.length) return null
  return (values.reduce((a, b) => a + b, 0) / values.length).toFixed(1)
}
</script>

<template>
  <div class="page" style="max-width: 1000px">
    <NSpin :show="loading">
      <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px">
        <NButton quaternary size="small" @click="router.push(`/interviews/${sessionId}`)">← 返回面试</NButton>
        <div style="flex: 1" />
      </div>

      <template v-if="session">
        <div style="display: flex; align-items: flex-end; gap: 20px; margin-bottom: 16px">
          <div>
            <h1 class="page-title" style="margin: 0">面试报告</h1>
            <div style="color: #8a919c; font-size: 13px; margin-top: 4px">
              {{ session.title }} · 共 {{ session.turns?.length ?? 0 }} 轮
            </div>
          </div>
          <div style="flex: 1" />
          <div style="text-align: right">
            <div style="font-size: 44px; font-weight: 700; line-height: 1"
                 :style="{ color: color(report.overall_score) }">
              {{ report.overall_score ?? '—' }}
            </div>
            <NTag size="small" :type="verdictType" style="margin-top: 4px">
              {{ verdictLabels[report.verdict ?? ''] ?? report.verdict ?? '未生成' }}
            </NTag>
          </div>
        </div>

        <NCard v-if="report.summary" size="small" title="整体判断" style="margin-bottom: 16px">
          <p style="margin: 0; line-height: 1.9">{{ report.summary }}</p>
        </NCard>

        <NGrid :cols="5" :x-gap="10" style="margin-bottom: 16px">
          <NGridItem v-for="(v, k) in report.dimension_scores" :key="k">
            <NCard size="small">
              <div style="font-size: 12px; color: #8a919c; margin-bottom: 6px">{{ dimensionLabels[k] ?? k }}</div>
              <div style="font-size: 22px; font-weight: 700" :style="{ color: color(v) }">{{ v }}</div>
              <NProgress type="line" :percentage="v" :show-indicator="false" :height="4"
                         :color="color(v)" style="margin-top: 6px" />
            </NCard>
          </NGridItem>
        </NGrid>

        <NGrid :cols="2" :x-gap="16" :y-gap="16" style="margin-bottom: 16px">
          <NGridItem>
            <NCard size="small" title="做得好的地方" type="success">
              <ul v-if="report.strengths?.length" style="margin: 0; padding-left: 18px; line-height: 1.9">
                <li v-for="s in report.strengths" :key="s">{{ s }}</li>
              </ul>
              <div v-else style="color: #8a919c">—</div>
            </NCard>
          </NGridItem>
          <NGridItem>
            <NCard size="small" title="短板" type="warning">
              <ul v-if="report.weaknesses?.length" style="margin: 0; padding-left: 18px; line-height: 1.9">
                <li v-for="s in report.weaknesses" :key="s">{{ s }}</li>
              </ul>
              <div v-else style="color: #8a919c">—</div>
            </NCard>
          </NGridItem>
        </NGrid>

        <NCard v-if="report.red_flags?.length" size="small" title="需要你自己核实的地方" type="error"
               style="margin-bottom: 16px">
          <ul style="margin: 0; padding-left: 18px; line-height: 1.9">
            <li v-for="s in report.red_flags" :key="s">{{ s }}</li>
          </ul>
        </NCard>

        <NCard v-if="report.next_steps?.length" size="small" title="下一步怎么准备" style="margin-bottom: 16px">
          <ol style="margin: 0; padding-left: 20px; line-height: 1.9">
            <li v-for="s in report.next_steps" :key="s" style="margin-bottom: 4px">{{ s }}</li>
          </ol>
        </NCard>

        <NCard v-if="report.question_review?.length" size="small" title="逐题回顾">
          <div v-for="q in report.question_review" :key="q.idx" style="padding: 8px 0; border-bottom: 1px dashed #eef0f3">
            <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 4px">
              <span class="mono" style="color: #8a919c">Q{{ q.idx }}</span>
              <strong style="flex: 1">{{ q.question }}</strong>
              <NTag size="tiny" :bordered="false">
                逐题均分 {{ scoreForTurn(q.idx) ?? '—' }} · 复盘 {{ q.score }}/5
              </NTag>
            </div>
            <div style="font-size: 13px; color: #5c6470; line-height: 1.7">{{ q.comment }}</div>
          </div>
        </NCard>
      </template>
    </NSpin>
  </div>
</template>
