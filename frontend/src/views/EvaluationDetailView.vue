<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton, NCard, NEmpty, NGrid, NGridItem, NProgress, NSpin, NTabPane, NTabs, NTag, useMessage,
} from 'naive-ui'
import {
  criticLabels, evaluationsApi, severityLabels, severityType, severityWeight,
  type CriticKey, type Evaluation, type Finding, type Severity,
} from '@/api/evaluations'
import { jobsApi } from '@/api/jobs'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const id = computed(() => Number(route.params.id))
const evaluation = ref<Evaluation | null>(null)
const jobTitle = ref('')
const loading = ref(true)
const filter = ref<'all' | 'high' | 'good'>('all')
let timer: number | undefined

async function load() {
  try {
    const data = await evaluationsApi.get(id.value)
    evaluation.value = data
    if (data.job_id) {
      const job = await jobsApi.get(data.job_id).catch(() => null)
      if (job) jobTitle.value = `#${job.id} ${job.title}`
    }
    if (data.status === 'pending' || data.status === 'running') {
      timer = window.setTimeout(load, 3000)
    }
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    loading.value = false
  }
}

onMounted(load)
onBeforeUnmount(() => window.clearTimeout(timer))

const findings = computed(() => {
  const list = [...(evaluation.value?.findings ?? [])]
  if (filter.value === 'high') return list.filter((f) => f.severity === 'high')
  if (filter.value === 'good') return list.filter((f) => f.severity === 'good')
  return list
})

const grouped = computed(() => {
  const map = new Map<string, Finding[]>()
  for (const f of findings.value) {
    const list = map.get(f.critic) ?? []
    list.push(f)
    map.set(f.critic, list)
  }
  const scores = evaluation.value?.dimension_scores ?? {}
  return [...map.entries()]
    .map(([key, list]) => ({
      key,
      label: criticLabels[key as CriticKey] ?? key,
      score: scores[key as keyof typeof scores],
      list: list.sort((a, b) => severityWeight[a.severity] - severityWeight[b.severity]),
    }))
    .sort((a, b) => (a.score ?? 0) - (b.score ?? 0))
})

const scoreColor = (score: number | undefined) => {
  if (score == null) return '#8a919c'
  if (score >= 80) return '#1a7f43'
  if (score >= 60) return '#a35c00'
  return '#b3261e'
}

const counts = computed(() => {
  const list = evaluation.value?.findings ?? []
  return {
    high: list.filter((f) => f.severity === 'high').length,
    medium: list.filter((f) => f.severity === 'medium').length,
    good: list.filter((f) => f.severity === 'good').length,
  }
})

function openDraft() {
  if (evaluation.value) {
    router.push(`/resumes/generated/${evaluation.value.draft_id}`)
  }
}

function jumpTo(where: string) {
  // 打开编辑器并带上定位参数；编辑器会滚动到对应位置
  if (!evaluation.value) return
  router.push({
    path: `/resumes/generated/${evaluation.value.draft_id}`,
    query: where ? { focus: where } : undefined,
  })
}
</script>

<template>
  <div class="page" style="max-width: 1200px">
    <NSpin :show="loading">
      <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px">
        <NButton quaternary size="small" @click="router.push('/evaluations')">← 评价列表</NButton>
        <div style="flex: 1" />
        <NButton size="small" @click="openDraft">打开简历</NButton>
      </div>

      <template v-if="evaluation">
        <div style="display: flex; align-items: flex-end; gap: 20px; margin-bottom: 16px">
          <div>
            <h1 class="page-title" style="margin: 0">评价 #{{ evaluation.id }}</h1>
            <div style="color: #8a919c; font-size: 13px; margin-top: 4px">
              草稿 #{{ evaluation.draft_id }} · rev {{ evaluation.draft_revision }}
              <span v-if="jobTitle"> · {{ jobTitle }}</span>
            </div>
          </div>
          <div style="flex: 1" />
          <div style="text-align: right">
            <div style="font-size: 40px; font-weight: 700; line-height: 1"
                 :style="{ color: scoreColor(evaluation.overall_score) }">
              {{ evaluation.status === 'succeeded' ? evaluation.overall_score : '—' }}
            </div>
            <div style="font-size: 12px; color: #8a919c">总分 / 100</div>
          </div>
        </div>

        <NCard v-if="evaluation.status === 'failed'" size="small" type="error" style="margin-bottom: 16px">
          {{ evaluation.error }}
        </NCard>

        <NCard v-if="evaluation.summary?.conclusion" size="small" style="margin-bottom: 16px" title="结论">
          <p style="margin: 0 0 8px">{{ evaluation.summary.conclusion }}</p>
          <div v-if="evaluation.summary.failed_critics?.length" style="color: #a35c00; font-size: 12px">
            以下维度评价失败：{{ evaluation.summary.failed_critics.join('、') }}
          </div>
        </NCard>

        <NGrid :cols="4" :x-gap="12" style="margin-bottom: 16px">
          <NGridItem v-for="(score, key) in evaluation.dimension_scores" :key="key">
            <NCard size="small">
              <div style="font-size: 12px; color: #8a919c; margin-bottom: 6px">
                {{ criticLabels[key as CriticKey] ?? key }}
              </div>
              <div style="font-size: 24px; font-weight: 700" :style="{ color: scoreColor(score) }">{{ score }}</div>
              <NProgress
                type="line"
                :percentage="score"
                :show-indicator="false"
                :height="4"
                :color="scoreColor(score)"
                style="margin-top: 6px"
              />
            </NCard>
          </NGridItem>
        </NGrid>

        <NCard v-if="evaluation.summary?.priorities?.length" size="small" style="margin-bottom: 16px" title="优先改进">
          <ol style="margin: 0; padding-left: 20px; line-height: 1.9">
            <li v-for="(p, i) in evaluation.summary.priorities" :key="i">
              <strong>{{ p.title }}</strong>
              <span style="color: #5c6470"> — {{ p.suggestion }}</span>
              <NButton v-if="p.where" size="tiny" quaternary style="margin-left: 6px" @click="jumpTo(p.where)">
                定位
              </NButton>
            </li>
          </ol>
        </NCard>

        <NCard v-if="evaluation.summary?.strengths?.length" size="small" style="margin-bottom: 16px" title="做得好的地方">
          <ul style="margin: 0; padding-left: 18px; line-height: 1.9">
            <li v-for="(s, i) in evaluation.summary.strengths" :key="i">{{ s }}</li>
          </ul>
        </NCard>

        <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 12px">
          <strong style="font-size: 15px">详细问题</strong>
          <NTag size="tiny" type="error" :bordered="false">必须改 {{ counts.high }}</NTag>
          <NTag size="tiny" type="warning" :bordered="false">建议改 {{ counts.medium }}</NTag>
          <NTag size="tiny" type="success" :bordered="false">做得好 {{ counts.good }}</NTag>
          <div style="flex: 1" />
          <NTag
            size="small" :bordered="filter === 'all'" style="cursor: pointer"
            @click="filter = 'all'"
          >全部</NTag>
          <NTag
            size="small" type="error" :bordered="filter !== 'high'" style="cursor: pointer"
            @click="filter = 'high'"
          >必须改</NTag>
          <NTag
            size="small" type="success" :bordered="filter !== 'good'" style="cursor: pointer"
            @click="filter = 'good'"
          >做得好</NTag>
        </div>

        <NEmpty v-if="!grouped.length" description="没有符合条件的条目" />
        <NTabs v-else type="segment" animated>
          <NTabPane v-for="g in grouped" :key="g.key" :name="g.key" :tab="`${g.label} ${g.score ?? '—'}`">
            <div style="display: grid; gap: 10px; padding-top: 4px">
              <NCard v-for="f in g.list" :key="f.id" size="small">
                <div style="display: flex; gap: 10px; align-items: flex-start">
                  <NTag size="tiny" :type="severityType[f.severity]">{{ severityLabels[f.severity] }}</NTag>
                  <div style="flex: 1; min-width: 0">
                    <div style="font-weight: 600; margin-bottom: 4px">{{ f.title }}</div>
                    <div v-if="f.excerpt" class="mono"
                         style="background: #f6f7f9; border-left: 3px solid #ccd5df; padding: 6px 8px; margin-bottom: 6px; white-space: pre-wrap">
                      {{ f.excerpt }}
                    </div>
                    <div style="line-height: 1.75; margin-bottom: 6px">{{ f.detail }}</div>
                    <div v-if="f.suggestion" style="color: #2458b8; font-size: 13px; line-height: 1.7">
                      建议：{{ f.suggestion }}
                    </div>
                    <div style="display: flex; gap: 8px; margin-top: 8px; align-items: center">
                      <span v-if="f.where" class="mono" style="color: #8a919c; font-size: 11px">{{ f.where }}</span>
                      <NButton v-if="f.where && f.where !== '__missing__'" size="tiny" quaternary @click="jumpTo(f.where)">
                        定位到简历
                      </NButton>
                      <NTag v-else-if="f.where === '__missing__'" size="tiny" :bordered="false">整体缺失</NTag>
                    </div>
                  </div>
                </div>
              </NCard>
            </div>
          </NTabPane>
        </NTabs>
      </template>
      <NEmpty v-else description="评价不存在" />
    </NSpin>
  </div>
</template>
