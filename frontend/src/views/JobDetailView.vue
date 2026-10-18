<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton, NCard, NDescriptions, NDescriptionsItem, NEmpty, NInput, NSkeleton,
  NSpin, NTag, useMessage,
} from 'naive-ui'
import {
  jobStatusLabels, jobStatusType, jobsApi, parseStatusLabels,
  type JobDetail,
} from '@/api/jobs'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const jobId = computed(() => Number(route.params.id))
const job = ref<JobDetail | null>(null)
const loading = ref(true)
const editingNotes = ref(false)
const notesDraft = ref('')
let timer: number | undefined

async function load() {
  try {
    job.value = await jobsApi.get(jobId.value)
    notesDraft.value = job.value.notes
    if (job.value.parse_status === 'pending' || job.value.parse_status === 'running') {
      schedulePoll()
    }
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    loading.value = false
  }
}

function schedulePoll() {
  window.clearTimeout(timer)
  timer = window.setTimeout(async () => {
    await load()
  }, 2500)
}

onMounted(load)
onBeforeUnmount(() => window.clearTimeout(timer))

const parsed = computed(() => job.value?.parsed ?? {})
const raw = computed(() => job.value?.raw_jds?.[0])

async function setStatus(status: string) {
  if (!job.value) return
  try {
    job.value = await jobsApi.update(job.value.id, { status })
    message.success(`已标记为「${jobStatusLabels[status]}」`)
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}

async function reparse() {
  if (!job.value) return
  try {
    job.value = await jobsApi.reparse(job.value.id)
    message.info('已重新提交解析')
    schedulePoll()
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}

async function saveNotes() {
  if (!job.value) return
  try {
    job.value = await jobsApi.update(job.value.id, { notes: notesDraft.value })
    editingNotes.value = false
    message.success('备注已保存')
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}

async function remove() {
  if (!job.value) return
  try {
    await jobsApi.remove(job.value.id)
    message.success('岗位已删除')
    router.push('/jobs')
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}
</script>

<template>
  <div class="page">
    <NSpin :show="loading">
      <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 4px">
        <NButton quaternary size="small" @click="router.push('/jobs')">← 岗位库</NButton>
        <div style="flex: 1" />
        <NButton size="small" :disabled="job?.parse_status === 'running'" @click="reparse">重新解析</NButton>
        <NButton size="small" type="error" quaternary @click="remove">删除</NButton>
      </div>

      <template v-if="job">
        <div style="display: flex; align-items: center; gap: 10px; margin: 8px 0">
          <h1 class="page-title" style="margin: 0">#{{ job.id }} {{ job.title }}</h1>
          <NTag
            size="small"
            :type="job.parse_status === 'failed' ? 'error' : job.parse_status === 'succeeded' ? 'success' : 'default'"
          >{{ parseStatusLabels[job.parse_status] }}</NTag>
        </div>
        <p class="page-desc">
          <span v-if="job.company">{{ job.company }} · </span>
          <span v-if="job.location">{{ job.location }}</span>
          <span v-if="job.salary"> · {{ job.salary }}</span>
          <span v-if="job.seniority"> · {{ job.seniority }}</span>
          <span v-if="!job.company && !job.location">原文未包含公司/地点信息</span>
        </p>

        <div style="display: flex; gap: 8px; margin-bottom: 16px">
          <NTag
            v-for="(label, key) in jobStatusLabels"
            :key="key"
            size="small"
            :type="job.status === key ? jobStatusType[key] : 'default'"
            :bordered="job.status !== key"
            style="cursor: pointer"
            @click="setStatus(key)"
          >{{ label }}</NTag>
        </div>

        <div v-if="job.parse_error" class="mono" style="color: #b3261e; margin-bottom: 16px">
          {{ job.parse_error }}
        </div>

        <div style="display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr); gap: 16px">
          <div style="display: grid; gap: 16px">
            <NCard size="small" title="岗位摘要">
              <p style="margin: 0; line-height: 1.7">{{ parsed.summary || '解析完成后显示' }}</p>
              <div v-if="parsed.ambiguities?.length" style="margin-top: 12px">
                <span style="font-size: 12px; color: #8a919c">需要确认</span>
                <ul style="margin: 6px 0 0; padding-left: 18px; color: #a35c00">
                  <li v-for="a in parsed.ambiguities" :key="a">{{ a }}</li>
                </ul>
              </div>
            </NCard>

            <NCard size="small" title="岗位职责">
              <ul v-if="parsed.responsibilities?.length" style="margin: 0; padding-left: 18px; line-height: 1.9">
                <li v-for="r in parsed.responsibilities" :key="r">{{ r }}</li>
              </ul>
              <NEmpty v-else size="small" description="暂无" />
            </NCard>

            <NCard size="small" title="硬性要求">
              <ul v-if="parsed.hard_requirements?.length" style="margin: 0; padding-left: 18px; line-height: 1.9">
                <li v-for="r in parsed.hard_requirements" :key="r">{{ r }}</li>
              </ul>
              <NEmpty v-else size="small" description="暂无" />
            </NCard>

            <NCard size="small" title="加分项">
              <ul v-if="parsed.nice_to_have?.length" style="margin: 0; padding-left: 18px; line-height: 1.9">
                <li v-for="r in parsed.nice_to_have" :key="r">{{ r }}</li>
              </ul>
              <NEmpty v-else size="small" description="暂无" />
            </NCard>
          </div>

          <div style="display: grid; gap: 16px; align-content: start">
            <NCard size="small" title="技能栈">
              <div v-if="parsed.skills?.length" style="display: flex; gap: 6px; flex-wrap: wrap">
                <NTag v-for="s in parsed.skills" :key="s" size="small" :bordered="false">{{ s }}</NTag>
              </div>
              <NEmpty v-else size="small" description="暂无" />
            </NCard>

            <NCard size="small" title="结构化字段">
              <NDescriptions :column="1" label-placement="left" size="small">
                <NDescriptionsItem label="岗位名">{{ job.title || '—' }}</NDescriptionsItem>
                <NDescriptionsItem label="公司">{{ job.company || '原文未提' }}</NDescriptionsItem>
                <NDescriptionsItem label="地点">{{ job.location || '—' }}</NDescriptionsItem>
                <NDescriptionsItem label="薪资">{{ job.salary || '—' }}</NDescriptionsItem>
                <NDescriptionsItem label="经验">{{ job.seniority || '—' }}</NDescriptionsItem>
                <NDescriptionsItem label="用工类型">{{ parsed.employment_type || '—' }}</NDescriptionsItem>
              </NDescriptions>
            </NCard>

            <NCard size="small" title="备注">
              <template v-if="!editingNotes">
                <p v-if="job.notes" style="margin: 0 0 8px; white-space: pre-wrap">{{ job.notes }}</p>
                <NButton size="tiny" quaternary @click="editingNotes = true">
                  {{ job.notes ? '编辑' : '添加备注' }}
                </NButton>
              </template>
              <template v-else>
                <NInput v-model:value="notesDraft" type="textarea" :autosize="{ minRows: 3 }" />
                <div style="display: flex; gap: 8px; margin-top: 8px">
                  <NButton size="tiny" type="primary" @click="saveNotes">保存</NButton>
                  <NButton size="tiny" quaternary @click="editingNotes = false">取消</NButton>
                </div>
              </template>
            </NCard>

            <NCard size="small" title="原始 JD">
              <div style="color: #8a919c; font-size: 12px; margin-bottom: 8px">
                {{ raw?.char_count ?? 0 }} 字
                <span v-if="raw?.source_url"> · {{ raw.source_url }}</span>
              </div>
              <NInput
                :value="raw?.content ?? ''"
                type="textarea"
                readonly
                :autosize="{ minRows: 8, maxRows: 20 }"
                class="mono"
              />
            </NCard>
          </div>
        </div>
      </template>

      <NSkeleton v-else text :repeat="6" />
    </NSpin>
  </div>
</template>
