<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton, NCard, NDropdown, NEmpty, NInput, NModal, NSelect, NSpin, NTag, useMessage,
} from 'naive-ui'
import {
  jobStatusLabels, jobStatusType, jobsApi, parseStatusLabels,
  type JobListItem,
} from '@/api/jobs'
import { useRunsStore } from '@/stores/runs'

const router = useRouter()
const message = useMessage()
const runs = useRunsStore()

const jobs = ref<JobListItem[]>([])
const loading = ref(false)
const statusFilter = ref<string | null>(null)
const keyword = ref('')

const showPaste = ref(false)
const pasteText = ref('')
const pasteUrl = ref('')
const submitting = ref(false)

const statusOptions = [
  { label: '全部', value: '' },
  { label: '收藏', value: 'saved' },
  { label: '已投', value: 'applied' },
  { label: '面试中', value: 'interviewing' },
  { label: '挂', value: 'rejected' },
]

const filtered = computed(() => {
  const needle = keyword.value.trim().toLowerCase()
  if (!needle) return jobs.value
  return jobs.value.filter(
    (j) =>
      j.title.toLowerCase().includes(needle) ||
      j.company.toLowerCase().includes(needle) ||
      j.skills.some((s) => s.toLowerCase().includes(needle)),
  )
})

async function refresh() {
  loading.value = true
  try {
    jobs.value = await jobsApi.list({ status: statusFilter.value || undefined })
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    loading.value = false
  }
}

onMounted(refresh)

async function submitPaste() {
  if (pasteText.value.trim().length < 20) {
    message.warning('JD 内容太短，请粘贴完整内容')
    return
  }
  submitting.value = true
  try {
    const job = await jobsApi.create({
      raw_text: pasteText.value,
      source_url: pasteUrl.value,
    })
    showPaste.value = false
    pasteText.value = ''
    pasteUrl.value = ''
    message.success(`岗位 #${job.id} 已创建，正在解析`)
    await refresh()
    // 详情页会轮询解析结果；同时把解析 run 接入 Run 面板
    if (job.parse_run_id) runs.openRun(job.parse_run_id)
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    submitting.value = false
  }
}

async function setStatus(job: JobListItem, status: string) {
  try {
    await jobsApi.update(job.id, { status })
    job.status = status as JobListItem['status']
    message.success(`已标记为「${jobStatusLabels[status]}」`)
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}

const statusMenu = (job: JobListItem) =>
  Object.entries(jobStatusLabels).map(([value, label]) => ({
    label: `${label}${job.status === value ? '（当前）' : ''}`,
    key: value,
  }))

function openDetail(job: JobListItem) {
  router.push(`/jobs/${job.id}`)
}
</script>

<template>
  <div class="page">
    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 4px">
      <h1 class="page-title" style="margin: 0">岗位库</h1>
      <div style="flex: 1" />
      <NButton type="primary" @click="showPaste = true">粘贴 JD</NButton>
    </div>
    <p class="page-desc">
      复制 Boss 原文粘贴即可。解析失败可点「重新解析」，投递状态由你自己维护。
    </p>

    <div style="display: flex; gap: 12px; margin-bottom: 16px">
      <NSelect
        v-model:value="statusFilter"
        :options="statusOptions"
        placeholder="全部状态"
        style="width: 160px"
        @update:value="refresh"
      />
      <NInput v-model:value="keyword" placeholder="搜索标题 / 公司 / 技能" clearable style="max-width: 320px" />
      <div style="flex: 1" />
      <NButton quaternary :loading="loading" @click="refresh">刷新</NButton>
    </div>

    <NSpin :show="loading">
      <NEmpty v-if="!filtered.length" description="还没有岗位，点右上角粘贴 JD" />
      <div v-else style="display: grid; gap: 12px">
        <NCard
          v-for="job in filtered"
          :key="job.id"
          size="small"
          hoverable
          @click="openDetail(job)"
        >
          <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 6px">
            <strong style="font-size: 15px">#{{ job.id }} {{ job.title }}</strong>
            <NTag
              size="tiny"
              :type="job.parse_status === 'failed' ? 'error' : job.parse_status === 'succeeded' ? 'success' : 'default'"
            >{{ parseStatusLabels[job.parse_status] }}</NTag>
            <div style="flex: 1" />
            <NDropdown
              trigger="click"
              :options="statusMenu(job)"
              @select="(key: string) => setStatus(job, key)"
            >
              <NTag
                size="small"
                :type="jobStatusType[job.status]"
                style="cursor: pointer"
                @click.stop
              >{{ jobStatusLabels[job.status] }}</NTag>
            </NDropdown>
          </div>

          <div style="color: #8a919c; font-size: 13px; margin-bottom: 8px">
            <span v-if="job.company">{{ job.company }} · </span>
            <span v-if="job.location">{{ job.location }}</span>
            <span v-if="job.salary"> · {{ job.salary }}</span>
            <span v-if="job.seniority"> · {{ job.seniority }}</span>
          </div>

          <div v-if="job.skills.length" style="display: flex; gap: 6px; flex-wrap: wrap">
            <NTag v-for="s in job.skills.slice(0, 12)" :key="s" size="tiny" :bordered="false">{{ s }}</NTag>
          </div>
          <div v-if="job.parse_error" class="mono" style="color: #b3261e; margin-top: 8px">
            {{ job.parse_error }}
          </div>
        </NCard>
      </div>
    </NSpin>

    <NModal
      v-model:show="showPaste"
      preset="card"
      title="粘贴 Boss 原文 JD"
      style="width: 720px; max-width: 92vw"
      :bordered="false"
    >
      <NInput
        v-model:value="pasteText"
        type="textarea"
        :autosize="{ minRows: 12, maxRows: 24 }"
        placeholder="把 Boss 上的岗位描述整段复制粘贴到这里（建议包含岗位名、城市、薪资、职责、要求）"
      />
      <NInput
        v-model:value="pasteUrl"
        placeholder="原始链接（可选）"
        style="margin-top: 12px"
      />
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <NButton quaternary @click="showPaste = false">取消</NButton>
          <NButton type="primary" :loading="submitting" @click="submitPaste">保存并解析</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>
