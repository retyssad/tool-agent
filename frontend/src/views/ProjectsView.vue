<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton, NCard, NEmpty, NInput, NModal, NSpin, NTag, useMessage,
} from 'naive-ui'
import {
  projectsApi, scanStatusLabels, type ProjectSummary,
} from '@/api/projects'

const router = useRouter()
const message = useMessage()
const items = ref<ProjectSummary[]>([])
const loading = ref(false)
let timer: number | undefined

const showNew = ref(false)
const form = ref({ name: '', path: '', role: '' })
const creating = ref(false)

async function refresh() {
  loading.value = true
  try {
    items.value = await projectsApi.list()
    const busy = items.value.some((p) => p.scan_status === 'pending' || p.scan_status === 'running')
    window.clearTimeout(timer)
    if (busy) timer = window.setTimeout(refresh, 4000)
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    loading.value = false
  }
}

onMounted(refresh)
onBeforeUnmount(() => window.clearTimeout(timer))

async function create() {
  if (!form.value.path.trim()) {
    message.warning('填写绝对路径')
    return
  }
  creating.value = true
  try {
    if (!form.value.name.trim()) {
      form.value.name = form.value.path.split(/[\\/]/).filter(Boolean).pop() || '未命名项目'
    }
    const project = await projectsApi.create(form.value)
    showNew.value = false
    form.value = { name: '', path: '', role: '' }
    message.success('已添加，正在扫描')
    await projectsApi.scan(project.id)
    router.push(`/projects/${project.id}`)
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    creating.value = false
  }
}

function summaryLine(p: ProjectSummary) {
  const s = p.scan_summary || {}
  if (!s.file_count) return ''
  return `${s.file_count} 文件 · ${s.module_count} 模块 · ${s.total_lines} 行 · ${s.evidence_count ?? 0} 条证据`
}

const statusType = computed(() => (status: string) =>
  status === 'failed' ? 'error' : status === 'succeeded' ? 'success' : 'default')
</script>

<template>
  <div class="page" style="max-width: 1100px">
    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 4px">
      <h1 class="page-title" style="margin: 0">项目导学</h1>
      <div style="flex: 1" />
      <NButton type="primary" @click="showNew = true">添加项目</NButton>
    </div>
    <p class="page-desc">
      扫描本地源码目录，提取带 <span class="mono">file:line</span> 引用的项目证据，再生成面经与导学。只读，不执行任意命令。
    </p>

    <NSpin :show="loading">
      <NEmpty v-if="!items.length" description="还没有项目。添加一个本地仓库的绝对路径。" />
      <div v-else style="display: grid; gap: 12px">
        <NCard v-for="p in items" :key="p.id" size="small" hoverable @click="router.push(`/projects/${p.id}`)">
          <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 4px">
            <strong style="font-size: 15px">#{{ p.id }} {{ p.name }}</strong>
            <NTag size="tiny" :type="statusType(p.scan_status)">{{ scanStatusLabels[p.scan_status] }}</NTag>
            <NTag v-if="p.role" size="tiny" :bordered="false">{{ p.role }}</NTag>
            <div style="flex: 1" />
            <span class="mono" style="color: #8a919c; font-size: 11px">{{ p.path }}</span>
          </div>
          <div v-if="summaryLine(p)" style="color: #5c6470; font-size: 13px; margin-bottom: 6px">
            {{ summaryLine(p) }}
          </div>
          <div v-if="p.tech_stack.length" style="display: flex; gap: 6px; flex-wrap: wrap">
            <NTag v-for="t in p.tech_stack.slice(0, 10)" :key="t" size="tiny" :bordered="false">{{ t }}</NTag>
          </div>
          <div v-if="p.scan_error" class="mono" style="color: #b3261e; margin-top: 6px">{{ p.scan_error }}</div>
        </NCard>
      </div>
    </NSpin>

    <NModal v-model:show="showNew" preset="card" title="添加本地项目" style="width: 620px" :bordered="false">
      <div style="display: grid; gap: 10px">
        <NInput v-model:value="form.path" placeholder="绝对路径，如 D:\Agent-data\travel-bot" />
        <NInput v-model:value="form.name" placeholder="项目名（留空取目录名）" />
        <NInput v-model:value="form.role" placeholder="你的角色，如 独立开发 / 后端负责人（可选）" />
        <div style="color: #8a919c; font-size: 12px; line-height: 1.7">
          路径必须落在设置页的源码白名单内。扫描只读文件，忽略 node_modules、.venv、dist 等依赖目录。
        </div>
      </div>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <NButton quaternary @click="showNew = false">取消</NButton>
          <NButton type="primary" :loading="creating" @click="create">添加并扫描</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>
