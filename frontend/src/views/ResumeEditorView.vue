<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NAlert, NButton, NCard, NEmpty, NInput, NSelect, NSpin, NTag, useMessage,
} from 'naive-ui'
import {
  generatedApi, type Claim, type Draft, type ResumeEntry, type ResumeJson, type ResumeLine,
  type TemplateMeta,
} from '@/api/generated'
import { evaluationsApi } from '@/api/evaluations'
import { useRunsStore } from '@/stores/runs'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const runs = useRunsStore()

const draftId = computed(() => Number(route.params.id))
const draft = ref<Draft | null>(null)
const claims = ref<Claim[]>([])
const templates = ref<TemplateMeta[]>([])
const loading = ref(true)
const saving = ref(false)
const exporting = ref(false)
const generating = ref(false)
const previewKey = ref(0)
const template = ref('asu')
const titleDraft = ref('')
const evaluating = ref(false)
const focusKey = ref<string | null>(null)
let saveTimer: number | undefined
let pollTimer: number | undefined

const resume = computed<ResumeJson | null>(() => {
  const value = draft.value?.resume_json
  if (!value || !Object.keys(value).length) return null
  return value as ResumeJson
})

const gate = computed(() => draft.value?.gate ?? { ok: true, errors: [] })
const claimMap = computed(() => new Map(claims.value.map((c) => [c.id, c])))

const sections = [
  { key: 'education', label: '教育' },
  { key: 'experience', label: '工作' },
  { key: 'projects', label: '项目' },
  { key: 'skills', label: '技能' },
  { key: 'campus', label: '校园' },
  { key: 'summary', label: '自我评价' },
] as const

async function load() {
  try {
    draft.value = await generatedApi.getDraft(draftId.value)
    template.value = draft.value.template
    titleDraft.value = draft.value.title
    // 草稿还没有内容时，先用档案里的基本信息初始化，避免门禁因缺姓名阻断
    if (!draft.value.resume_json || !Object.keys(draft.value.resume_json).length) {
      const profile = await generatedApi.getProfile().catch(() => null)
      if (profile?.name) {
        draft.value = await generatedApi.patchDraft(draft.value.id, {
          resume_json: { profile },
        })
        previewKey.value += 1
      }
    }
    claims.value = await generatedApi.claims(draft.value.ledger_id)
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  templates.value = await generatedApi.templates().catch(() => [])
  await load()
  const focus = route.query.focus
  if (typeof focus === 'string' && focus) {
    focusKey.value = focus
    // 等渲染 + 布局稳定后再滚动，否则元素还没插入 DOM
    await nextTick()
    window.setTimeout(() => scrollToFocus(focus), 300)
  }
})
onBeforeUnmount(() => {
  window.clearTimeout(saveTimer)
  window.clearInterval(pollTimer)
})

function scheduleSave() {
  window.clearTimeout(saveTimer)
  saveTimer = window.setTimeout(save, 700)
}

async function save() {
  if (!draft.value || !resume.value) return
  saving.value = true
  try {
    const updated = await generatedApi.patchDraft(draft.value.id, {
      resume_json: resume.value,
      title: titleDraft.value,
    })
    draft.value = updated
    previewKey.value += 1
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    saving.value = false
  }
}

async function switchTemplate(value: string) {
  if (!draft.value) return
  template.value = value
  try {
    draft.value = await generatedApi.patchDraft(draft.value.id, { template: value })
    previewKey.value += 1
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}

async function generate() {
  if (!draft.value) return
  generating.value = true
  try {
    const { run_id } = await generatedApi.generateDraft(draft.value.id)
    runs.openRun(run_id)
    message.info('已提交生成任务')
    pollTimer = window.setInterval(async () => {
      await runs.refresh()
      const run = runs.runs.find((r) => r.id === run_id)
      if (!run) return
      if (['succeeded', 'failed', 'cancelled', 'interrupted'].includes(run.status)) {
        window.clearInterval(pollTimer)
        generating.value = false
        await load()
        previewKey.value += 1
        if (run.status === 'failed') message.error(run.error || '生成失败')
        else message.success('已生成，可继续手动编辑')
      }
    }, 3000)
  } catch (err) {
    generating.value = false
    message.error(String(err instanceof Error ? err.message : err))
  }
}

async function exportPdf() {
  if (!draft.value) return
  exporting.value = true
  try {
    const result = await generatedApi.exportDraft(draft.value.id, template.value)
    message.success(`导出成功 v${result.version_no}`)
    window.open(result.pdf_url, '_blank')
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    exporting.value = false
  }
}

async function evaluate() {
  if (!draft.value) return
  if (!resume.value) {
    message.warning('先有简历内容再评价')
    return
  }
  if (!draft.value.target_job_id) {
    message.warning('评价必须绑定岗位。请先在「简历库」重新创建绑定岗位的简历。')
    return
  }
  evaluating.value = true
  try {
    const { evaluation_id, run_id } = await evaluationsApi.create(draft.value.id)
    message.info('已提交评价任务，四个 critic 并行执行')
    runs.openRun(run_id)
    window.open(`/evaluations/${evaluation_id}`, '_blank')
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    evaluating.value = false
  }
}

function scrollToFocus(where: string) {
  const el = document.querySelector(`[data-where="${where}"]`)
  if (!(el instanceof HTMLElement)) {
    message.info(`定位到 ${where}：该条目可能已不存在`)
    return
  }
  // 编辑区是一个独立滚动容器，scrollIntoView 不会滚动它，manual 处理
  let scroller: HTMLElement | null = el.parentElement
  while (scroller) {
    const overflowY = getComputedStyle(scroller).overflowY
    if ((overflowY === 'auto' || overflowY === 'scroll') && scroller.scrollHeight > scroller.clientHeight) {
      break
    }
    scroller = scroller.parentElement
  }
  const targetTop = el.getBoundingClientRect().top
  if (scroller) {
    const containerTop = scroller.getBoundingClientRect().top
    scroller.scrollTop = Math.max(0, scroller.scrollTop + targetTop - containerTop - 24)
  } else {
    el.scrollIntoView({ block: 'center' })
  }
}

function claimTags(ids: number[]) {
  return ids.map((id) => claimMap.value.get(id)).filter(Boolean) as Claim[]
}

function addEntry(section: string) {
  if (!resume.value) return
  const target = (resume.value as unknown as Record<string, ResumeEntry[] | ResumeLine[]>)[section]
  if (!Array.isArray(target)) return
  if (section === 'skills' || section === 'summary') {
    ;(target as ResumeLine[]).push({ label: '', text: '', claim_ids: [] })
  } else {
    ;(target as ResumeEntry[]).push({ dates: '', bullets: [], organization: '', role: '', name: '', summary: '' })
  }
  scheduleSave()
}

function removeEntry(section: string, index: number) {
  if (!resume.value) return
  const target = (resume.value as unknown as Record<string, ResumeEntry[] | ResumeLine[]>)[section]
  if (!Array.isArray(target)) return
  target.splice(index, 1)
  scheduleSave()
}

function addBullet(entry: ResumeEntry) {
  entry.bullets.push({ label: '', text: '', claim_ids: [] })
  scheduleSave()
}

function removeBullet(entry: ResumeEntry, index: number) {
  entry.bullets.splice(index, 1)
  scheduleSave()
}
</script>

<template>
  <div class="page" style="max-width: 1600px">
    <NSpin :show="loading">
      <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap">
        <NButton quaternary size="small" @click="router.push('/resumes')">← 简历库</NButton>
        <NInput v-model:value="titleDraft" size="small" style="width: 240px" @update:value="scheduleSave" />
        <NTag size="tiny">rev {{ draft?.revision ?? 0 }}</NTag>
        <NTag v-if="saving" size="tiny" type="info">保存中…</NTag>
        <div style="flex: 1" />
        <NSelect
          :value="template"
          :options="templates.map(t => ({ label: t.name, value: t.key }))"
          size="small"
          style="width: 260px"
          @update:value="switchTemplate"
        />
        <NButton size="small" :loading="generating" @click="generate">AI 生成初稿</NButton>
        <NButton size="small" :loading="evaluating" @click="evaluate">评价</NButton>
        <NButton size="small" type="primary" :loading="exporting" @click="exportPdf">导出 PDF</NButton>
      </div>

      <NAlert
        v-if="gate && !gate.ok"
        type="error"
        :bordered="false"
        style="margin-bottom: 12px"
        title="证据门禁未通过，导出会被阻止"
      >
        <div style="max-height: 120px; overflow: auto; font-size: 12px">
          <div v-for="(err, i) in gate.errors.slice(0, 12)" :key="i">
            · {{ err.message }}<span v-if="err.where" class="mono"> （{{ err.where }}）</span>
          </div>
        </div>
      </NAlert>

      <div v-if="!resume" style="margin-bottom: 12px">
        <NEmpty description="还没有内容。点「AI 生成初稿」基于已确认证据生成，或手动添加。" />
        <div style="display: flex; justify-content: center; margin-top: 12px">
          <NButton type="primary" :loading="generating" @click="generate">AI 生成初稿</NButton>
        </div>
      </div>

      <div v-else style="display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px">
        <!-- 左：结构化编辑 -->
        <div style="display: grid; gap: 12px; align-content: start; max-height: calc(100vh - 150px); overflow: auto">
          <NCard size="small" title="基本信息">
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px">
              <NInput v-model:value="resume.profile.name" size="small" placeholder="姓名" data-where="profile.name" @update:value="scheduleSave" />
              <NInput v-model:value="resume.profile.headline" size="small" placeholder="一句话定位" data-where="profile.headline" @update:value="scheduleSave" />
              <NInput v-model:value="resume.profile.phone" size="small" placeholder="电话" data-where="profile.phone" @update:value="scheduleSave" />
              <NInput v-model:value="resume.profile.email" size="small" placeholder="邮箱" data-where="profile.email" @update:value="scheduleSave" />
              <NInput v-model:value="resume.profile.location" size="small" placeholder="城市" data-where="profile.location" @update:value="scheduleSave" />
              <NInput v-model:value="resume.profile.github" size="small" placeholder="GitHub" data-where="profile.github" @update:value="scheduleSave" />
            </div>
            <div style="color: #8a919c; font-size: 12px; margin-top: 6px">
              基本信息来自档案，不参与证据门禁。
            </div>
          </NCard>

          <NCard v-for="section in sections" :key="section.key" size="small">
            <template #header>
              <div style="display: flex; align-items: center; gap: 8px">
                <span>{{ section.label }}</span>
                <NTag size="tiny" :bordered="false">
                  {{ ((resume as any)[section.key] || []).length }}
                </NTag>
                <div style="flex: 1" />
                <NButton size="tiny" quaternary @click="addEntry(section.key)">+ 条目</NButton>
              </div>
            </template>

            <template v-if="section.key === 'skills' || section.key === 'summary'">
              <div v-for="(line, i) in ((resume as any)[section.key] as ResumeLine[])" :key="i"
                   :data-where="`${section.key}[${i}]`"
                   :style="focusKey === `${section.key}[${i}]` ? { background: '#fff8e6', outline: '2px solid #f0c36d' } : undefined"
                   style="border-bottom: 1px dashed #eef0f3; padding: 6px 0">
                <div style="display: flex; gap: 6px">
                  <NInput v-model:value="line.label" size="tiny" placeholder="前缀" style="width: 110px"
                          @update:value="scheduleSave" />
                  <NInput v-model:value="line.text" size="tiny" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }"
                          style="flex: 1" @update:value="scheduleSave" />
                  <NButton size="tiny" quaternary type="error" @click="removeEntry(section.key, i)">×</NButton>
                </div>
                <div style="display: flex; gap: 4px; margin-top: 4px; flex-wrap: wrap">
                  <NTag v-for="c in claimTags(line.claim_ids)" :key="c.id" size="tiny"
                        :type="c.status === 'confirmed' ? 'success' : 'warning'" :bordered="false">
                    #{{ c.id }}
                  </NTag>
                  <NTag v-if="!line.claim_ids.length" size="tiny" type="error" :bordered="false">未绑定证据</NTag>
                </div>
              </div>
            </template>

            <template v-else>
              <div v-for="(entry, ei) in ((resume as any)[section.key] as ResumeEntry[])" :key="ei"
                   :data-where="`${section.key}[${ei}]`"
                   :style="focusKey === `${section.key}[${ei}]` ? { background: '#fff8e6', outline: '2px solid #f0c36d' } : undefined"
                   style="border-bottom: 1px solid #eef0f3; padding: 8px 0">
                <div style="display: flex; gap: 6px; margin-bottom: 6px">
                  <NInput
                    v-if="section.key === 'education'"
                    v-model:value="entry.school" size="tiny" placeholder="学校" style="width: 150px"
                    @update:value="scheduleSave"
                  />
                  <NInput
                    v-else
                    v-model:value="entry.organization" size="tiny" placeholder="公司/项目"
                    style="width: 150px" @update:value="scheduleSave"
                  />
                  <NInput
                    v-if="section.key === 'education'"
                    v-model:value="entry.major_degree" size="tiny" placeholder="专业/学位" style="width: 130px"
                    @update:value="scheduleSave"
                  />
                  <NInput
                    v-else
                    v-model:value="entry.role" size="tiny" placeholder="角色"
                    style="width: 130px" @update:value="scheduleSave"
                  />
                  <NInput v-model:value="entry.dates" size="tiny" placeholder="时间" style="width: 120px"
                          @update:value="scheduleSave" />
                  <div style="flex: 1" />
                  <NButton size="tiny" quaternary @click="addBullet(entry)">+ 要点</NButton>
                  <NButton size="tiny" quaternary type="error" @click="removeEntry(section.key, ei)">×</NButton>
                </div>

                <div v-for="(bullet, bi) in entry.bullets" :key="bi"
                     :data-where="`${section.key}[${ei}].bullets[${bi}]`"
                     :style="focusKey === `${section.key}[${ei}].bullets[${bi}]` ? { background: '#fff8e6', outline: '2px solid #f0c36d', borderRadius: '4px' } : undefined"
                     style="margin-bottom: 4px">
                  <div style="display: flex; gap: 6px">
                    <NInput v-model:value="bullet.label" size="tiny" placeholder="前缀" style="width: 90px"
                            @update:value="scheduleSave" />
                    <NInput v-model:value="bullet.text" size="tiny" type="textarea"
                            :autosize="{ minRows: 1, maxRows: 4 }" style="flex: 1"
                            @update:value="scheduleSave" />
                    <NButton size="tiny" quaternary type="error" @click="removeBullet(entry, bi)">×</NButton>
                  </div>
                  <div style="display: flex; gap: 4px; margin-top: 3px; flex-wrap: wrap; padding-left: 96px">
                    <NTag v-for="c in claimTags(bullet.claim_ids)" :key="c.id" size="tiny"
                          :type="c.status === 'confirmed' ? 'success' : 'warning'" :bordered="false">
                      #{{ c.id }}
                    </NTag>
                    <NTag v-if="!bullet.claim_ids.length" size="tiny" type="error" :bordered="false">未绑定证据</NTag>
                  </div>
                </div>
              </div>
            </template>
          </NCard>
        </div>

        <!-- 右：实时预览 -->
        <NCard size="small" :bordered="false" style="padding: 0; height: calc(100vh - 150px)">
          <iframe
            :key="previewKey"
            :src="generatedApi.previewUrl(draftId, template)"
            style="width: 100%; height: 100%; border: 0; background: #eef0f3"
          />
        </NCard>
      </div>
    </NSpin>
  </div>
</template>
