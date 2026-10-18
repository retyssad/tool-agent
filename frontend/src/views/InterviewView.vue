<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NAlert, NButton, NCard, NEmpty, NInput, NProgress, NSpin, NTag, useMessage,
} from 'naive-ui'
import {
  dimensionLabels, interviewsApi, stageLabels, verdictLabels,
  type InterviewSession, type InterviewTurn, type Progress,
} from '@/api/interviews'
import { useRunsStore } from '@/stores/runs'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const runs = useRunsStore()

const sessionId = computed(() => Number(route.params.id))
const session = ref<InterviewSession | null>(null)
const progress = ref<Progress | null>(null)
const loading = ref(true)
const asking = ref(false)
const submitting = ref(false)
const draftAnswer = ref('')
const askError = ref('')
const chatRef = ref<HTMLElement | null>(null)
let planTimer: number | undefined

const turns = computed(() => session.value?.turns ?? [])
const current = computed(() => {
  const list = turns.value
  if (!list.length) return null
  const last = list[list.length - 1]
  return !last.answer && !last.skipped ? last : null
})
const canAsk = computed(() =>
  session.value?.status === 'active' &&
  !asking.value &&
  !current.value &&
  (session.value.plan_total ?? 0) > 0,
)
const canReport = computed(() =>
  session.value?.status === 'active' &&
  turns.value.some((t) => t.answer) &&
  !current.value,
)
const scrollToBottom = async () => {
  await nextTick()
  if (chatRef.value) chatRef.value.scrollTop = chatRef.value.scrollHeight
}

async function load() {
  try {
    const data = await interviewsApi.get(sessionId.value)
    session.value = data
    progress.value = data.progress ?? null
    if (data.status === 'active' && data.plan_total === 0) {
      window.clearTimeout(planTimer)
      planTimer = window.setTimeout(load, 4000)
    }
    await scrollToBottom()
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    loading.value = false
  }
}

onMounted(load)
onBeforeUnmount(() => window.clearTimeout(planTimer))

async function askNext() {
  if (!session.value) return
  asking.value = true
  askError.value = ''
  try {
    const data = await interviewsApi.next(sessionId.value)
    if (data.action === 'finish') {
      message.info('主问题已问完，可以生成报告')
      await load()
      return
    }
    await load()
  } catch (err) {
    askError.value = String(err instanceof Error ? err.message : err)
  } finally {
    asking.value = false
  }
}

async function submitAnswer() {
  if (!current.value || !draftAnswer.value.trim()) return
  submitting.value = true
  try {
    await interviewsApi.answer(sessionId.value, draftAnswer.value.trim())
    draftAnswer.value = ''
    await load()
    // 评分是异步的，稍后再拉一次
    window.setTimeout(load, 8000)
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    submitting.value = false
  }
}

async function skip() {
  if (!current.value) return
  try {
    await interviewsApi.skip(sessionId.value, '没准备好')
    await load()
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}

async function makeReport() {
  if (!session.value) return
  try {
    const { run_id } = await interviewsApi.report(sessionId.value)
    runs.openRun(run_id)
    message.info('正在生成报告')
    const poll = window.setInterval(async () => {
      await runs.refresh()
      const run = runs.runs.find((r) => r.id === run_id)
      if (run && ['succeeded', 'failed', 'cancelled', 'interrupted'].includes(run.status)) {
        window.clearInterval(poll)
        await load()
        if (run.status === 'failed') message.error(run.error || '报告生成失败')
        else message.success('报告已生成')
      }
    }, 3000)
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}

async function abandon() {
  try {
    await interviewsApi.abandon(sessionId.value)
    await load()
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}

function scoreColor(v?: number) {
  if (v == null) return '#8a919c'
  if (v >= 80) return '#1a7f43'
  if (v >= 55) return '#a35c00'
  return '#b3261e'
}

const percent = computed(() => {
  const p = progress.value
  if (!p || !p.plan_total) return 0
  return Math.round((p.primary_asked / p.plan_total) * 100)
})

const report = computed(() => session.value?.report ?? {})
</script>

<template>
  <div class="page" style="max-width: 1180px">
    <NSpin :show="loading">
      <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px">
        <NButton quaternary size="small" @click="router.push('/interviews')">← 面试列表</NButton>
        <div style="flex: 1" />
        <NButton v-if="session?.status === 'active'" size="small" quaternary @click="abandon">放弃</NButton>
        <NButton v-if="canReport" size="small" type="primary" @click="makeReport">生成报告</NButton>
      </div>

      <template v-if="session">
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 4px">
          <h1 class="page-title" style="margin: 0">#{{ session.id }} {{ session.title }}</h1>
          <NTag size="small" :type="session.status === 'completed' ? 'success' : session.status === 'abandoned' ? 'default' : 'info'">
            {{ session.status === 'completed' ? '已完成' : session.status === 'abandoned' ? '已放弃' : '进行中' }}
          </NTag>
        </div>
        <p class="page-desc">
          <span v-if="session.pact?.focus">{{ session.pact.focus }}</span>
        </p>

        <NAlert v-if="session.plan_error" type="error" :bordered="false" style="margin-bottom: 12px">
          面试契约生成失败：{{ session.plan_error }}
        </NAlert>

        <div v-if="session.status === 'active' && !session.plan_total" style="margin-bottom: 16px">
          <NAlert type="info" :bordered="false">
            正在生成面试契约（约 2–3 分钟）…
          </NAlert>
        </div>

        <div v-if="session.plan_total" style="margin-bottom: 16px">
          <div style="display: flex; justify-content: space-between; font-size: 12px; color: #8a919c; margin-bottom: 4px">
            <span>主问题进度</span>
            <span>{{ progress?.primary_asked ?? 0 }} / {{ session.plan_total }}
              <span v-if="progress?.skipped"> · 跳过 {{ progress.skipped }}</span>
            </span>
          </div>
          <NProgress :percentage="percent" :show-indicator="false" :height="4" />
        </div>

        <!-- 对话区 -->
        <div style="display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 16px">
          <div>
            <div ref="chatRef" style="max-height: calc(100vh - 330px); overflow: auto; display: grid; gap: 12px; padding-right: 4px">
              <NEmpty v-if="!turns.length" description="点下方「下一题」开始面试" />

              <div v-for="t in turns" :key="t.id">
                <div style="display: flex; gap: 8px; margin-bottom: 6px; align-items: center; flex-wrap: wrap">
                  <NTag size="tiny" :bordered="false">{{ stageLabels[t.stage] ?? t.stage }}</NTag>
                  <NTag v-if="t.kind === 'follow_up'" size="tiny" type="warning">追问</NTag>
                  <span class="mono" style="color: #8a919c; font-size: 11px">Q{{ t.idx }}</span>
                  <span v-if="t.scores && Object.keys(t.scores).length" style="font-size: 11px; color: #8a919c">
                    评分 {{ Object.values(t.scores).join(' / ') }}
                  </span>
                </div>
                <NCard size="small" style="margin-bottom: 6px">
                  <div style="line-height: 1.75">{{ t.question }}</div>
                </NCard>

                <div v-if="t.answer" style="margin-left: 16px">
                  <div style="white-space: pre-wrap; line-height: 1.8; background: #f6f8fb; padding: 10px 12px; border-radius: 6px">
                    {{ t.answer }}
                  </div>
                  <div v-if="t.feedback" style="font-size: 12px; color: #5c6470; margin-top: 6px">
                    评分官：{{ t.feedback }}
                  </div>
                </div>
                <div v-else-if="t.skipped" style="margin-left: 16px; color: #8a919c; font-size: 13px">
                  （已跳过）
                </div>
              </div>
            </div>

            <!-- 作答区 -->
            <div v-if="session.status === 'active'" style="margin-top: 14px">
              <NAlert v-if="askError" type="error" :bordered="false" style="margin-bottom: 8px">
                {{ askError }}
              </NAlert>

              <template v-if="current">
                <NInput
                  v-model:value="draftAnswer"
                  type="textarea"
                  :autosize="{ minRows: 4, maxRows: 12 }"
                  placeholder="用自己的话回答。说清楚做了什么、怎么做的、结果如何；不知道就说不知道。"
                />
                <div style="display: flex; gap: 8px; margin-top: 8px; align-items: center">
                  <NButton type="primary" :loading="submitting" :disabled="!draftAnswer.trim()" @click="submitAnswer">
                    提交回答
                  </NButton>
                  <NButton quaternary :disabled="submitting" @click="skip">跳过这题</NButton>
                  <span style="color: #8a919c; font-size: 12px">Ctrl+Enter 提交</span>
                </div>
              </template>

              <template v-else>
                <NButton type="primary" :loading="asking" :disabled="!canAsk" @click="askNext">
                  {{ turns.length ? '下一题' : '开始面试' }}
                </NButton>
                <span v-if="!canAsk && session.plan_total" style="margin-left: 8px; color: #8a919c; font-size: 12px">
                  主问题已问完，点右下「生成报告」
                </span>
              </template>
            </div>
          </div>

          <!-- 侧栏：契约与报告 -->
          <div style="display: grid; gap: 12px; align-content: start">
            <NCard size="small" title="本场考察">
              <div v-if="session.pact?.dimensions?.length" style="display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 8px">
                <NTag v-for="d in session.pact.dimensions" :key="d" size="tiny" :bordered="false">{{ d }}</NTag>
              </div>
              <div v-if="session.pact?.questions?.length" style="font-size: 12px; color: #8a919c; line-height: 1.8">
                共 {{ session.pact.questions.length }} 道主问题，按
                {{ [...new Set(session.pact.questions.map(q => stageLabels[q.stage] ?? q.stage))].join(' → ') }}
                推进。
              </div>
            </NCard>

            <NCard v-if="session.status === 'completed'" size="small" title="面试报告">
              <div style="text-align: center; margin-bottom: 10px">
                <div style="font-size: 34px; font-weight: 700" :style="{ color: scoreColor(report.overall_score) }">
                  {{ report.overall_score ?? '—' }}
                </div>
                <NTag size="small" :bordered="false">{{ verdictLabels[report.verdict ?? ''] ?? report.verdict }}</NTag>
              </div>
              <div v-for="(v, k) in report.dimension_scores" :key="k" style="margin-bottom: 6px">
                <div style="display: flex; justify-content: space-between; font-size: 12px">
                  <span>{{ dimensionLabels[k] ?? k }}</span>
                  <strong :style="{ color: scoreColor(v) }">{{ v }}</strong>
                </div>
              </div>
              <NButton size="small" block style="margin-top: 8px" @click="router.push(`/interviews/${session.id}/report`)">
                查看完整报告
              </NButton>
            </NCard>
          </div>
        </div>
      </template>
      <NEmpty v-else description="面试不存在" />
    </NSpin>
  </div>
</template>
