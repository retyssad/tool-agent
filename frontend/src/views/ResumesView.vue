<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton, NCard, NEmpty, NInput, NModal, NPopconfirm, NSelect, NSpin, NTabPane, NTabs, NTag, useMessage,
} from 'naive-ui'
import { generatedApi, type DraftSummary, type Ledger, type TemplateMeta, type Version } from '@/api/generated'
import { jobsApi, type JobListItem } from '@/api/jobs'

const router = useRouter()
const message = useMessage()

const tab = ref<'generated' | 'versions'>('generated')
const drafts = ref<DraftSummary[]>([])
const versions = ref<Version[]>([])
const templates = ref<TemplateMeta[]>([])
const ledgers = ref<Ledger[]>([])
const jobs = ref<JobListItem[]>([])
const loading = ref(false)

const showNew = ref(false)
const newTitle = ref('新建简历')
const newLedger = ref<number | null>(null)
const newJob = ref<number | null>(null)
const newTemplate = ref('asu')
const creating = ref(false)
const deletingId = ref<number | null>(null)

const templateName = computed(() => {
  const map = new Map(templates.value.map((t) => [t.key, t.name]))
  return (key: string) => map.get(key) ?? key
})
const jobName = computed(() => {
  const map = new Map(jobs.value.map((j) => [j.id, j.title]))
  return (id: number | null) => (id ? map.get(id) ?? `岗位 #${id}` : '通用版')
})

async function refresh() {
  loading.value = true
  try {
    const [d, v] = await Promise.all([generatedApi.drafts(), generatedApi.versions()])
    drafts.value = d
    versions.value = v
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  templates.value = await generatedApi.templates().catch(() => [])
  ledgers.value = await generatedApi.ledgers('personal').catch(() => [])
  jobs.value = await jobsApi.list().catch(() => [])
  newLedger.value = ledgers.value[0]?.id ?? null
  await refresh()
})

async function deleteDraft(id: number) {
  deletingId.value = id
  try {
    await generatedApi.deleteDraft(id)
    drafts.value = drafts.value.filter((d) => d.id !== id)
    message.success('已删除生成简历')
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    deletingId.value = null
  }
}

async function deleteVersion(id: number) {
  deletingId.value = id
  try {
    await generatedApi.deleteVersion(id)
    versions.value = versions.value.filter((v) => v.id !== id)
    message.success('已删除版本')
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    deletingId.value = null
  }
}

async function createDraft() {
  if (newLedger.value == null) {
    message.warning('先创建一个证据账本')
    return
  }
  creating.value = true
  try {
    const draft = await generatedApi.createDraft({
      ledger_id: newLedger.value,
      target_job_id: newJob.value,
      title: newTitle.value || '新建简历',
      template: newTemplate.value,
    })
    showNew.value = false
    router.push(`/resumes/generated/${draft.id}`)
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <div class="page" style="max-width: 1200px">
    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 4px">
      <h1 class="page-title" style="margin: 0">简历库</h1>
      <div style="flex: 1" />
      <NButton @click="showNew = true">新建生成简历</NButton>
    </div>
    <p class="page-desc">
      生成简历以结构化 JSON 为事实源，可随时换模板重渲染；导入简历只保留原文件与文本。
    </p>

    <NTabs v-model:value="tab" type="line">
      <NTabPane name="generated" tab="生成简历">
        <NSpin :show="loading">
          <NEmpty v-if="!drafts.length" description="还没有生成简历" />
          <div v-else style="display: grid; gap: 12px">
            <NCard v-for="d in drafts" :key="d.id" size="small" hoverable
                   @click="router.push(`/resumes/generated/${d.id}`)">
              <div style="display: flex; align-items: center; gap: 10px">
                <strong style="font-size: 15px">#{{ d.id }} {{ d.title }}</strong>
                <NTag size="tiny" :bordered="false">{{ templateName(d.template) }}</NTag>
                <NTag size="tiny" :bordered="false">{{ jobName(d.target_job_id) }}</NTag>
                <div style="flex: 1" />
                <span class="mono" style="color: #8a919c">rev {{ d.revision }}</span>
                <NPopconfirm @positive-click="deleteDraft(d.id)">
                  <template #trigger>
                    <NButton size="tiny" type="error" quaternary :loading="deletingId === d.id"
                             @click.stop>删除</NButton>
                  </template>
                  删除「{{ d.title }}」？其评价报告会一并删除，已导出版本保留。
                </NPopconfirm>
              </div>
            </NCard>
          </div>
        </NSpin>
      </NTabPane>

      <NTabPane name="versions" tab="已导出版本">
        <NSpin :show="loading">
          <NEmpty v-if="!versions.length" description="导出后会在这里留下不可变版本" />
          <div v-else style="display: grid; gap: 10px">
            <NCard v-for="v in versions" :key="v.id" size="small">
              <div style="display: flex; align-items: center; gap: 10px">
                <strong>#{{ v.id }} {{ v.title }}</strong>
                <NTag size="tiny" type="success">v{{ v.version_no }}</NTag>
                <NTag size="tiny" :bordered="false">{{ templateName(v.template) }}</NTag>
                <NTag size="tiny" :bordered="false">{{ jobName(v.target_job_id) }}</NTag>
                <span class="mono" style="color: #8a919c; font-size: 12px">
                  {{ new Date(v.created_at).toLocaleString('zh-CN') }}
                </span>
                <div style="flex: 1" />
                <NButton size="tiny" tag="a" :href="v.pdf_url" target="_blank">PDF</NButton>
                <NButton size="tiny" quaternary tag="a" :href="v.html_url" target="_blank">HTML</NButton>
                <NPopconfirm @positive-click="deleteVersion(v.id)">
                  <template #trigger>
                    <NButton size="tiny" type="error" quaternary :loading="deletingId === v.id">删除</NButton>
                  </template>
                  删除 v{{ v.version_no }}？文件会一并删除，不可恢复。
                </NPopconfirm>
              </div>
            </NCard>
          </div>
        </NSpin>
      </NTabPane>

      <NTabPane name="imported" tab="导入简历">
        <NEmpty description="导入简历请在下方入口管理" />
        <div style="display: flex; justify-content: center; margin-top: 12px">
          <NButton @click="router.push('/resumes/imported')">前往导入简历</NButton>
        </div>
      </NTabPane>
    </NTabs>

    <NModal v-model:show="showNew" preset="card" title="新建生成简历" style="width: 520px" :bordered="false">
      <div style="display: grid; gap: 10px">
        <NInput v-model:value="newTitle" placeholder="简历标题" />
        <NSelect
          v-model:value="newLedger"
          :options="ledgers.map(l => ({ label: `${l.name}（已确认 ${l.confirmed_count}）`, value: l.id }))"
          placeholder="证据账本"
        />
        <NSelect
          v-model:value="newJob"
          :options="jobs.map(j => ({ label: `#${j.id} ${j.title}`, value: j.id }))"
          clearable
          placeholder="目标岗位（可留空，做通用版）"
        />
        <NSelect
          v-model:value="newTemplate"
          :options="templates.map(t => ({ label: t.name, value: t.key }))"
        />
        <div v-if="!ledgers.length" style="color: #a35c00; font-size: 12px">
          还没有证据账本，请先在「证据账本」页创建。
        </div>
      </div>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <NButton quaternary @click="showNew = false">取消</NButton>
          <NButton type="primary" :loading="creating" @click="createDraft">创建</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>
