<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton, NCard, NEmpty, NInput, NModal, NSelect, NSpin, NTag, useMessage,
} from 'naive-ui'
import {
  dimensionLabels, interviewsApi, stageLabels, verdictLabels, type InterviewSession,
} from '@/api/interviews'
import { generatedApi, type DraftSummary } from '@/api/generated'
import { resumesApi, type ImportedResumeItem } from '@/api/resumes'
import { jobsApi, type JobListItem } from '@/api/jobs'

const router = useRouter()
const message = useMessage()

const items = ref<InterviewSession[]>([])
const loading = ref(false)
const showNew = ref(false)
const creating = ref(false)

const jobs = ref<JobListItem[]>([])
const drafts = ref<DraftSummary[]>([])
const imported = ref<ImportedResumeItem[]>([])

const form = ref({
  job_id: null as number | null,
  resume_kind: 'draft' as 'draft' | 'imported',
  draft_id: null as number | null,
  imported_resume_id: null as number | null,
  title: '',
})

async function refresh() {
  loading.value = true
  try {
    items.value = await interviewsApi.list()
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await refresh()
  jobs.value = await jobsApi.list().catch(() => [])
  drafts.value = await generatedApi.drafts().catch(() => [])
  imported.value = await resumesApi.listImported().catch(() => [])
})

async function create() {
  if (!form.value.job_id) {
    message.warning('选择目标岗位')
    return
  }
  if (form.value.resume_kind === 'draft' && !form.value.draft_id) {
    message.warning('选择一份生成简历')
    return
  }
  if (form.value.resume_kind === 'imported' && !form.value.imported_resume_id) {
    message.warning('选择一份导入简历')
    return
  }
  creating.value = true
  try {
    const { session_id } = await interviewsApi.create({
      job_id: form.value.job_id,
      resume_kind: form.value.resume_kind,
      draft_id: form.value.draft_id ?? undefined,
      imported_resume_id: form.value.imported_resume_id ?? undefined,
      title: form.value.title,
    })
    showNew.value = false
    router.push(`/interviews/${session_id}`)
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    creating.value = false
  }
}

function statusTag(s: InterviewSession) {
  if (s.status === 'completed') return { type: 'success' as const, text: '已完成' }
  if (s.status === 'abandoned') return { type: 'default' as const, text: '已放弃' }
  if (s.plan_total === 0) return { type: 'warning' as const, text: '生成契约中' }
  return { type: 'info' as const, text: `进行中 ${s.progress?.answered ?? 0}/${s.plan_total}` }
}

function scoreColor(score?: number) {
  if (score == null) return '#8a919c'
  if (score >= 80) return '#1a7f43'
  if (score >= 55) return '#a35c00'
  return '#b3261e'
}
</script>

<template>
  <div class="page" style="max-width: 1100px">
    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 4px">
      <h1 class="page-title" style="margin: 0">模拟面试</h1>
      <div style="flex: 1" />
      <NButton type="primary" @click="showNew = true">新建面试</NButton>
    </div>
    <p class="page-desc">
      面试官出题、评分官独立打分。基于已确认的简历证据、目标岗位 JD 与项目证据提问；可随时暂停第二天继续。
    </p>

    <NSpin :show="loading">
      <NEmpty v-if="!items.length" description="还没有面试。新建一场，选择岗位与简历。" />
      <div v-else style="display: grid; gap: 12px">
        <NCard v-for="s in items" :key="s.id" size="small" hoverable @click="router.push(`/interviews/${s.id}`)">
          <div style="display: flex; align-items: center; gap: 12px">
            <div style="width: 64px; text-align: center">
              <div v-if="s.status === 'completed'" style="font-size: 26px; font-weight: 700"
                   :style="{ color: scoreColor(s.report?.overall_score) }">
                {{ s.report?.overall_score ?? '—' }}
              </div>
              <div v-else style="font-size: 12px; color: #8a919c">{{ s.status === 'active' ? '进行中' : '—' }}</div>
              <div style="font-size: 11px; color: #8a919c">总分</div>
            </div>
            <div style="flex: 1; min-width: 0">
              <div style="display: flex; gap: 10px; align-items: center; margin-bottom: 6px; flex-wrap: wrap">
                <strong>#{{ s.id }} {{ s.title }}</strong>
                <NTag size="tiny" :type="statusTag(s).type">{{ statusTag(s).text }}</NTag>
                <NTag size="tiny" :bordered="false">岗位 #{{ s.job_id }}</NTag>
                <NTag size="tiny" :bordered="false">{{ s.resume_kind === 'draft' ? '生成简历' : '导入简历' }}</NTag>
                <NTag v-if="s.report?.verdict" size="tiny" :bordered="false">
                  {{ verdictLabels[s.report.verdict] ?? s.report.verdict }}
                </NTag>
              </div>
              <div v-if="s.pact?.focus" style="font-size: 13px; color: #5c6470">{{ s.pact.focus }}</div>
              <div v-if="s.report?.summary" style="font-size: 13px; color: #5c6470; margin-top: 4px">
                {{ s.report.summary.slice(0, 120) }}
              </div>
              <div v-if="s.plan_error" class="mono" style="color: #b3261e; margin-top: 4px">{{ s.plan_error }}</div>
            </div>
            <span class="mono" style="color: #8a919c">{{ new Date(s.created_at).toLocaleString('zh-CN') }}</span>
          </div>
        </NCard>
      </div>
    </NSpin>

    <NModal v-model:show="showNew" preset="card" title="新建模拟面试" style="width: 600px" :bordered="false">
      <div style="display: grid; gap: 10px">
        <NSelect
          v-model:value="form.job_id"
          :options="jobs.map(j => ({ label: `#${j.id} ${j.title}`, value: j.id }))"
          placeholder="目标岗位（必选）"
        />
        <NSelect
          v-model:value="form.resume_kind"
          :options="[
            { label: '生成简历（有证据账本，评分更严格）', value: 'draft' },
            { label: '导入简历（只有文本）', value: 'imported' },
          ]"
        />
        <NSelect
          v-if="form.resume_kind === 'draft'"
          v-model:value="form.draft_id"
          :options="drafts.map(d => ({ label: `#${d.id} ${d.title}`, value: d.id }))"
          placeholder="选择生成简历"
        />
        <NSelect
          v-else
          v-model:value="form.imported_resume_id"
          :options="imported.map(r => ({ label: `#${r.id} ${r.title}`, value: r.id }))"
          placeholder="选择导入简历"
        />
        <NInput v-model:value="form.title" placeholder="会话标题（可选）" />
        <div style="color: #8a919c; font-size: 12px; line-height: 1.7">
          建议用生成简历：评分官能对照证据账本判断回答是否与事实一致。导入简历只能做文本比对。
        </div>
      </div>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <NButton quaternary @click="showNew = false">取消</NButton>
          <NButton type="primary" :loading="creating" @click="create">创建</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>
