<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton, NCard, NCollapse, NCollapseItem, NEmpty, NSpin, NTabPane, NTabs, NTag, useMessage,
} from 'naive-ui'
import {
  categoryLabels, categoryType, projectsApi, scanStatusLabels,
  type EvidenceSource, type ProjectDetail, type ProjectEvidence,
} from '@/api/projects'
import { useRunsStore } from '@/stores/runs'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const runs = useRunsStore()

const projectId = computed(() => Number(route.params.id))
const project = ref<ProjectDetail | null>(null)
const loading = ref(true)
const scanning = ref(false)
const guiding = ref(false)
const tab = ref<'evidence' | 'guide' | 'questions'>('evidence')
const categoryFilter = ref<string | null>(null)
const guideContent = ref<{ title: string; content_md: string } | null>(null)
const guideLoading = ref(false)
const sources = ref<Record<number, EvidenceSource[]>>({})
let timer: number | undefined

async function load() {
  try {
    project.value = await projectsApi.get(projectId.value)
    if (project.value.scan_status === 'pending' || project.value.scan_status === 'running') {
      window.clearTimeout(timer)
      timer = window.setTimeout(load, 4000)
    }
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    loading.value = false
  }
}

onMounted(load)
onBeforeUnmount(() => window.clearTimeout(timer))

const categories = ['architecture', 'feature', 'quality', 'data', 'ops']
const evidence = computed(() => {
  const list = project.value?.evidence ?? []
  return categoryFilter.value ? list.filter((e) => e.category === categoryFilter.value) : list
})

async function startScan() {
  scanning.value = true
  try {
    const { run_id } = await projectsApi.scan(projectId.value)
    runs.openRun(run_id)
    message.info('已提交扫描')
    await load()
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    scanning.value = false
  }
}

async function makeGuide() {
  guiding.value = true
  try {
    const { run_id } = await projectsApi.guide(projectId.value, 'interview')
    runs.openRun(run_id)
    message.info('已提交导学生成')
    const poll = window.setInterval(async () => {
      await runs.refresh()
      const run = runs.runs.find((r) => r.id === run_id)
      if (run && ['succeeded', 'failed', 'cancelled', 'interrupted'].includes(run.status)) {
        window.clearInterval(poll)
        guiding.value = false
        await load()
        if (run.status === 'succeeded') {
          message.success('导学已生成')
          if (project.value?.guides?.length) await openGuide(project.value.guides[0].id)
        } else {
          message.error(run.error || '生成失败')
        }
      }
    }, 3000)
  } catch (err) {
    guiding.value = false
    message.error(String(err instanceof Error ? err.message : err))
  }
}

async function openGuide(guideId: number) {
  guideLoading.value = true
  try {
    guideContent.value = await projectsApi.guideContent(projectId.value, guideId)
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    guideLoading.value = false
  }
}

async function showSource(item: ProjectEvidence) {
  if (sources.value[item.id]) {
    delete sources.value[item.id]
    return
  }
  try {
    const result = await projectsApi.evidenceSource(projectId.value, item.id)
    sources.value[item.id] = result.files
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}

const statusType = computed(() => (status: string) =>
  status === 'failed' ? 'error' : status === 'succeeded' ? 'success' : 'default')
</script>

<template>
  <div class="page" style="max-width: 1300px">
    <NSpin :show="loading">
      <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px">
        <NButton quaternary size="small" @click="router.push('/projects')">← 项目列表</NButton>
        <div style="flex: 1" />
        <NButton size="small" :loading="scanning" @click="startScan">重新扫描</NButton>
        <NButton size="small" type="primary" :loading="guiding" @click="makeGuide">生成导学</NButton>
      </div>

      <template v-if="project">
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 4px">
          <h1 class="page-title" style="margin: 0">#{{ project.id }} {{ project.name }}</h1>
          <NTag size="small" :type="statusType(project.scan_status)">{{ scanStatusLabels[project.scan_status] }}</NTag>
          <NTag v-if="project.role" size="small" :bordered="false">{{ project.role }}</NTag>
        </div>
        <p class="page-desc">
          <span class="mono" style="font-size: 12px">{{ project.path }}</span>
          <span v-if="project.scan_summary?.file_count">
            · {{ project.scan_summary.file_count }} 文件 · {{ project.scan_summary.total_lines }} 行
            · {{ project.evidence.length }} 条证据
          </span>
        </p>

        <div v-if="project.scan_error" class="mono" style="color: #b3261e; margin-bottom: 12px">
          {{ project.scan_error }}
        </div>

        <NCard v-if="project.description" size="small" style="margin-bottom: 16px">
          <div style="line-height: 1.8">{{ project.description }}</div>
          <div v-if="project.tech_stack.length" style="display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px">
            <NTag v-for="t in project.tech_stack" :key="t" size="tiny" :bordered="false">{{ t }}</NTag>
          </div>
        </NCard>

        <NTabs v-model:value="tab" type="line">
          <NTabPane name="evidence" :tab="`项目证据 ${project.evidence.length}`">
            <div style="display: flex; gap: 6px; margin: 8px 0 12px; flex-wrap: wrap">
              <NTag size="small" :bordered="categoryFilter !== null" style="cursor: pointer" @click="categoryFilter = null">
                全部
              </NTag>
              <NTag
                v-for="c in categories" :key="c" size="small" :type="categoryType[c]"
                :bordered="categoryFilter !== c" style="cursor: pointer" @click="categoryFilter = c"
              >
                {{ categoryLabels[c] }} {{ project.evidence.filter(e => e.category === c).length }}
              </NTag>
            </div>

            <NEmpty v-if="!evidence.length" description="暂无证据，先扫描项目" />
            <div v-else style="display: grid; gap: 10px">
              <NCard v-for="item in evidence" :key="item.id" size="small">
                <div style="display: flex; gap: 10px; align-items: flex-start">
                  <NTag size="tiny" :type="categoryType[item.category]">{{ categoryLabels[item.category] }}</NTag>
                  <div style="flex: 1; min-width: 0">
                    <div style="line-height: 1.75; margin-bottom: 6px">{{ item.statement }}</div>
                    <div style="display: flex; gap: 6px; flex-wrap: wrap; align-items: center">
                      <span
                        v-for="r in item.refs" :key="`${r.path}:${r.line}`"
                        class="mono" style="font-size: 11px; color: #5c6470; background: #f2f4f7; padding: 1px 5px; border-radius: 3px"
                      >{{ r.path }}:{{ r.line }}</span>
                      <NButton size="tiny" quaternary @click="showSource(item)">
                        {{ sources[item.id] ? '收起代码' : '查看代码' }}
                      </NButton>
                      <NTag v-for="c in item.capabilities" :key="c" size="tiny" :bordered="false">{{ c }}</NTag>
                    </div>

                    <div v-if="sources[item.id]" style="margin-top: 8px; display: grid; gap: 8px">
                      <div v-for="f in sources[item.id]" :key="f.path"
                           style="background: #fbfcfd; border: 1px solid #e8eaee; border-radius: 4px">
                        <div class="mono" style="font-size: 11px; color: #5c6470; padding: 4px 8px; border-bottom: 1px solid #eef0f3">
                          {{ f.path }}:{{ f.line }}
                          <span v-if="!f.exists" style="color: #b3261e">（文件已不存在）</span>
                        </div>
                        <pre v-if="f.exists" class="mono"
                             style="margin: 0; padding: 8px; overflow: auto; font-size: 11px; line-height: 1.6">{{ f.excerpt }}</pre>
                      </div>
                    </div>
                  </div>
                </div>
              </NCard>
            </div>
          </NTabPane>

          <NTabPane name="guide" :tab="`导学 ${project.guides.length}`">
            <NEmpty v-if="!project.guides.length" description="还没有导学，点右上角「生成导学」" />
            <div v-else style="padding-top: 8px">
              <div style="display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap">
                <NButton
                  v-for="g in project.guides" :key="g.id" size="small"
                  :type="guideContent?.title === g.title ? 'primary' : 'default'"
                  @click="openGuide(g.id)"
                >{{ g.title }}（{{ g.chars }} 字）</NButton>
              </div>
              <NSpin :show="guideLoading">
                <NCard v-if="guideContent" size="small">
                  <pre style="margin: 0; white-space: pre-wrap; line-height: 1.85; font-family: inherit">{{ guideContent.content_md }}</pre>
                </NCard>
                <NEmpty v-else description="选择一份导学查看" />
              </NSpin>
            </div>
          </NTabPane>

          <NTabPane name="questions" :tab="`面试问题 ${project.interview_questions.length}`">
            <NEmpty v-if="!project.interview_questions.length" description="扫描完成后生成" />
            <div v-else style="display: grid; gap: 10px; padding-top: 8px">
              <NCard v-for="(q, i) in project.interview_questions" :key="i" size="small">
                <div style="font-weight: 600; margin-bottom: 4px">{{ i + 1 }}. {{ q.question }}</div>
                <div style="font-size: 13px; color: #5c6470; margin-bottom: 4px">考察点：{{ q.angle }}</div>
                <div style="font-size: 13px; color: #2458b8; line-height: 1.7">回答要点：{{ q.hint }}</div>
              </NCard>
            </div>

            <NCard v-if="project.weak_spots.length" size="small" title="薄弱点（自己看）"
                   style="margin-top: 16px" type="warning">
              <ul style="margin: 0; padding-left: 18px; line-height: 1.9">
                <li v-for="w in project.weak_spots" :key="w">{{ w }}</li>
              </ul>
            </NCard>
          </NTabPane>
        </NTabs>
      </template>
      <NEmpty v-else description="项目不存在" />
    </NSpin>
  </div>
</template>
