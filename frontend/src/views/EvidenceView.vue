<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  NButton, NCard, NCheckbox, NEmpty, NInput, NModal, NPopconfirm, NRadioButton,
  NRadioGroup, NSelect, NSpin, NTag, useMessage,
} from 'naive-ui'
import {
  claimKindLabels, claimStatusLabels, claimStatusType, generatedApi,
  type Claim, type Ledger,
} from '@/api/generated'
import { resumesApi, type ImportedResumeItem } from '@/api/resumes'
import { projectsApi, type ProjectEvidence, type ProjectSummary } from '@/api/projects'
import { useRunsStore } from '@/stores/runs'

const message = useMessage()
const runs = useRunsStore()

type Section = 'personal' | 'project'

const section = ref<Section>('personal')
const personalLedgers = ref<Ledger[]>([])
const projectLedgers = ref<Ledger[]>([])
const projects = ref<ProjectSummary[]>([])
const personalLedgerId = ref<number | null>(null)
const projectId = ref<number | null>(null)
const claims = ref<Claim[]>([])
const imported = ref<ImportedResumeItem[]>([])
const loading = ref(false)
const selected = ref<number[]>([])
const filterStatus = ref<string | null>(null)
const filterKind = ref<string | null>(null)
const newStatement = ref('')
const newKind = ref('experience')
const importingResumeId = ref<number | null>(null)

// 账本管理
const ledgerModalOpen = ref(false)
const ledgerModalMode = ref<'create' | 'rename'>('create')
const ledgerNameDraft = ref('')
const ledgerBusy = ref(false)

// 项目证据导入
const importOpen = ref(false)
const importEvidence = ref<ProjectEvidence[]>([])
const importSelected = ref<number[]>([])
const importBusy = ref(false)

// 项目条目转入个人账本
const copyOpen = ref(false)
const copyTarget = ref<number | null>(null)
const copyBusy = ref(false)

const kindOptions = Object.entries(claimKindLabels).map(([value, label]) => ({ value, label }))
const statusOptions = Object.entries(claimStatusLabels).map(([value, label]) => ({ value, label }))

const currentProject = computed(() => projects.value.find((p) => p.id === projectId.value) ?? null)
const currentProjectLedger = computed(
  () => projectLedgers.value.find((l) => l.project_id === projectId.value) ?? null,
)
const activeLedger = computed(() => {
  if (section.value === 'personal') {
    return personalLedgers.value.find((l) => l.id === personalLedgerId.value) ?? null
  }
  return currentProjectLedger.value
})
const visible = computed(() => claims.value)
const pendingCount = computed(() => claims.value.filter((c) => c.status === 'pending').length)
const confirmedCount = computed(() => claims.value.filter((c) => c.status === 'confirmed').length)
const personalOptions = computed(() =>
  personalLedgers.value.map((l) => ({
    label: `${l.name}（${l.confirmed_count}/${l.claim_count}）`,
    value: l.id,
  })),
)

function remember(key: string, value: number | null) {
  if (value == null) localStorage.removeItem(key)
  else localStorage.setItem(key, String(value))
}

function readRemembered(key: string): number | null {
  const raw = localStorage.getItem(key)
  if (!raw) return null
  const id = Number(raw)
  return Number.isFinite(id) ? id : null
}

async function loadLedgers() {
  const all = await generatedApi.ledgers()
  personalLedgers.value = all.filter((l) => l.kind === 'personal')
  projectLedgers.value = all.filter((l) => l.kind === 'project')
  if (!personalLedgers.value.length) {
    const created = await generatedApi.createLedger('默认账本')
    personalLedgers.value = [created]
  }
  const remembered = readRemembered('ledger.personal')
  if (personalLedgerId.value == null || !personalLedgers.value.some((l) => l.id === personalLedgerId.value)) {
    personalLedgerId.value =
      personalLedgers.value.find((l) => l.id === remembered)?.id ?? personalLedgers.value[0].id
  }
}

async function loadProjects() {
  projects.value = await projectsApi.list()
  const remembered = readRemembered('ledger.project')
  if (projectId.value == null || !projects.value.some((p) => p.id === projectId.value)) {
    projectId.value = projects.value.find((p) => p.id === remembered)?.id ?? projects.value[0]?.id ?? null
  }
}

async function loadClaims() {
  const ledger = activeLedger.value
  selected.value = []
  if (ledger == null) {
    claims.value = []
    return
  }
  loading.value = true
  try {
    claims.value = await generatedApi.claims(ledger.id, {
      status: filterStatus.value ?? undefined,
      kind: filterKind.value ?? undefined,
    })
    selected.value = selected.value.filter((id) => claims.value.some((c) => c.id === id))
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    const rememberedSection = localStorage.getItem('ledger.section')
    if (rememberedSection === 'project') section.value = 'project'
    const savedSection = section.value
    await Promise.all([loadLedgers(), loadProjects()])
    imported.value = await resumesApi.listImported()
    section.value = savedSection
    await loadClaims()
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
})

watch(section, async (value) => {
  localStorage.setItem('ledger.section', value)
  await loadClaims()
})
watch(personalLedgerId, async (value) => {
  remember('ledger.personal', value)
  if (section.value === 'personal') await loadClaims()
})
watch(projectId, async (value) => {
  remember('ledger.project', value)
  if (section.value === 'project') await loadClaims()
})
watch([filterStatus, filterKind], loadClaims)

function toggle(id: number, checked: boolean) {
  if (checked) {
    if (!selected.value.includes(id)) selected.value = [...selected.value, id]
  } else {
    selected.value = selected.value.filter((x) => x !== id)
  }
}

async function bulk(status: string) {
  if (!selected.value.length) {
    message.warning('先勾选证据条目')
    return
  }
  try {
    const result = await generatedApi.bulkStatus(selected.value, status)
    message.success(`已更新 ${result.updated} 条为「${claimStatusLabels[status]}」`)
    selected.value = []
    await loadClaims()
    await loadLedgers()
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}

async function setOne(claim: Claim, status: string) {
  try {
    const updated = await generatedApi.patchClaim(claim.id, {
      status: status as Claim['status'],
    })
    Object.assign(claim, updated)
    await loadLedgers()
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}

async function addClaim() {
  const ledger = activeLedger.value
  if (!newStatement.value.trim() || ledger == null) return
  try {
    await generatedApi.createClaim(ledger.id, {
      statement: newStatement.value.trim(),
      kind: newKind.value,
      status: 'pending',
    })
    newStatement.value = ''
    await loadClaims()
    await loadLedgers()
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}

async function extract() {
  if (importingResumeId.value == null || activeLedger.value == null) {
    message.warning('选择一份导入简历')
    return
  }
  try {
    const result = await generatedApi.extractFromResume(importingResumeId.value, activeLedger.value.id)
    message.info('已提交提炼任务，完成后自动刷新')
    runs.openRun(result.run_id)
    const timer = window.setInterval(async () => {
      const run = runs.runs.find((r) => r.id === result.run_id)
      if (run && ['succeeded', 'failed', 'cancelled', 'interrupted'].includes(run.status)) {
        window.clearInterval(timer)
        await loadClaims()
        await loadLedgers()
        if (run.status === 'failed') message.error(run.error || '提炼失败')
      } else {
        await runs.refresh()
      }
    }, 3000)
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}

async function remove(claim: Claim) {
  try {
    await generatedApi.deleteClaim(claim.id)
    await loadClaims()
    await loadLedgers()
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}

// ---------- 账本管理 ----------

function openCreate() {
  ledgerNameDraft.value = ''
  ledgerModalMode.value = 'create'
  ledgerModalOpen.value = true
}

function openRename() {
  const ledger = activeLedger.value
  if (ledger == null) return
  ledgerNameDraft.value = ledger.name
  ledgerModalMode.value = 'rename'
  ledgerModalOpen.value = true
}

async function submitLedgerModal() {
  const name = ledgerNameDraft.value.trim()
  if (!name) {
    message.warning('账本名不能为空')
    return
  }
  ledgerBusy.value = true
  try {
    if (ledgerModalMode.value === 'create') {
      const created = await generatedApi.createLedger(name)
      section.value = 'personal'
      await loadLedgers()
      personalLedgerId.value = created.id
      message.success('已新建账本')
    } else if (activeLedger.value) {
      await generatedApi.renameLedger(activeLedger.value.id, name)
      await loadLedgers()
      message.success('已重命名')
    }
    ledgerModalOpen.value = false
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    ledgerBusy.value = false
  }
}

async function removeLedger() {
  const ledger = activeLedger.value
  if (ledger == null) return
  try {
    await generatedApi.deleteLedger(ledger.id)
    message.success('已删除账本')
    personalLedgerId.value = null
    await loadLedgers()
    await loadClaims()
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}

// ---------- 项目证据导入 ----------

async function openImport() {
  if (projectId.value == null) {
    message.warning('先选一个项目')
    return
  }
  try {
    const detail = await projectsApi.get(projectId.value)
    importEvidence.value = detail.evidence
    importSelected.value = detail.evidence.map((e) => e.id)
    if (!detail.evidence.length) {
      message.warning('该项目还没有扫描证据，先到项目导学页扫描')
      return
    }
    importOpen.value = true
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}

async function submitImport() {
  const ledger = currentProjectLedger.value
  if (ledger == null) {
    message.error('该项目还没有账本')
    return
  }
  if (!importSelected.value.length) {
    message.warning('先勾选要导入的证据')
    return
  }
  importBusy.value = true
  try {
    const result = await generatedApi.importProjectEvidence(
      ledger.id, projectId.value as number, importSelected.value,
    )
    message.success(`导入 ${result.created} 条${result.skipped ? `，跳过重复 ${result.skipped} 条` : ''}`)
    importOpen.value = false
    await loadClaims()
    await loadLedgers()
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    importBusy.value = false
  }
}

function toggleImport(id: number, checked: boolean) {
  if (checked) {
    if (!importSelected.value.includes(id)) importSelected.value = [...importSelected.value, id]
  } else {
    importSelected.value = importSelected.value.filter((x) => x !== id)
  }
}

function openCopy() {
  if (!selected.value.length) {
    message.warning('先勾选要转入的条目')
    return
  }
  copyTarget.value = personalLedgerId.value ?? personalLedgers.value[0]?.id ?? null
  copyOpen.value = true
}

async function submitCopy() {
  if (copyTarget.value == null) {
    message.warning('先建一个个人账本')
    return
  }
  copyBusy.value = true
  try {
    const result = await generatedApi.copyClaims(copyTarget.value, selected.value)
    message.success(`转入 ${result.created} 条${result.skipped ? `，跳过重复 ${result.skipped} 条` : ''}`)
    copyOpen.value = false
    selected.value = []
    await loadLedgers()
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    copyBusy.value = false
  }
}
</script>

<template>
  <div class="page" style="max-width: 1200px">
    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 8px">
      <h1 class="page-title" style="margin: 0">证据账本</h1>
      <NRadioGroup v-model:value="section" size="small">
        <NRadioButton value="personal">个人账本</NRadioButton>
        <NRadioButton value="project">项目账本</NRadioButton>
      </NRadioGroup>
      <div style="flex: 1" />

      <template v-if="section === 'personal'">
        <NSelect
          v-model:value="personalLedgerId"
          :options="personalOptions"
          style="width: 240px"
          placeholder="选择账本"
        />
        <NButton size="small" @click="openCreate">新建</NButton>
        <NButton size="small" :disabled="activeLedger == null" @click="openRename">重命名</NButton>
        <NPopconfirm @positive-click="removeLedger">
          <template #trigger>
            <NButton size="small" type="error" quaternary :disabled="activeLedger == null">删除</NButton>
          </template>
          删除「{{ activeLedger?.name }}」及其全部证据条目？被简历引用的账本不能删除。
        </NPopconfirm>
      </template>

      <template v-else>
        <NSelect
          v-model:value="projectId"
          :options="projects.map(p => ({ label: p.name, value: p.id }))"
          style="width: 240px"
          placeholder="选择项目"
        />
        <NButton size="small" :disabled="currentProjectLedger == null" @click="openImport">从项目证据导入</NButton>
        <NButton size="small" type="primary" quaternary :disabled="!selected.length" @click="openCopy">
          转入个人账本
        </NButton>
      </template>
    </div>

    <p class="page-desc">
      <template v-if="section === 'personal'">
        个人账本汇总可写进简历的事实。只有「已确认」的条目可以进入简历，系统不会自动确认任何内容。
      </template>
      <template v-else>
        每个项目一本独立账本：先把扫描证据导入这里逐条确认，再转入个人账本供生成简历使用。
        <span v-if="currentProject" class="mono"> · {{ currentProject.path }}</span>
      </template>
    </p>

    <NCard v-if="section === 'personal'" size="small" style="margin-bottom: 16px">
      <div style="display: flex; gap: 10px; flex-wrap: wrap; align-items: center">
        <NSelect :options="imported.map(r => ({ label: `#${r.id} ${r.title}`, value: r.id }))"
                 v-model:value="importingResumeId" placeholder="从导入简历提炼" style="width: 240px" />
        <NButton @click="extract">提炼证据</NButton>
        <div style="width: 1px; height: 24px; background: #e8eaee" />
        <NInput v-model:value="newStatement" placeholder="手动新增一条证据陈述" style="flex: 1; min-width: 260px"
                @keyup.enter="addClaim" />
        <NSelect v-model:value="newKind" :options="kindOptions" style="width: 130px" />
        <NButton @click="addClaim">添加</NButton>
      </div>
    </NCard>

    <NEmpty
      v-else-if="!projects.length"
      description="还没有项目。先到「项目导学」添加本地代码目录。"
      style="margin: 40px 0"
    />
    <NEmpty
      v-else-if="currentProjectLedger == null"
      description="该项目还没有账本，重新保存一次项目即可自动创建"
      style="margin: 40px 0"
    />
    <NEmpty
      v-else-if="!loading && !claims.length"
      description="项目账本还是空的。点右上角「从项目证据导入」，把扫描结果导入后逐条确认。"
      style="margin: 40px 0"
    />

    <template v-if="section === 'personal' || (currentProjectLedger && claims.length) || loading">
      <div style="display: flex; gap: 10px; align-items: center; margin-bottom: 12px; flex-wrap: wrap">
        <NTag size="small" :bordered="false">共 {{ claims.length }}</NTag>
        <NTag size="small" type="warning" :bordered="false">待确认 {{ pendingCount }}</NTag>
        <NTag size="small" type="success" :bordered="false">已确认 {{ confirmedCount }}</NTag>
        <NSelect :options="statusOptions" v-model:value="filterStatus" clearable placeholder="全部状态" style="width: 130px" />
        <NSelect :options="kindOptions" v-model:value="filterKind" clearable placeholder="全部类型" style="width: 130px" />
        <div style="flex: 1" />
        <span style="color: #8a919c; font-size: 12px">已选 {{ selected.length }}</span>
        <NButton size="small" type="primary" :disabled="!selected.length" @click="bulk('confirmed')">批量确认</NButton>
        <NButton size="small" :disabled="!selected.length" @click="bulk('rejected')">批量不采用</NButton>
        <NButton size="small" quaternary :disabled="!selected.length" @click="bulk('pending')">退回待确认</NButton>
      </div>

      <NSpin :show="loading">
        <NEmpty v-if="!visible.length" description="账本还是空的。可以从导入简历提炼，或手动新增。" />
        <div v-else style="display: grid; gap: 8px">
          <NCard v-for="claim in visible" :key="claim.id" size="small">
            <div style="display: flex; gap: 10px; align-items: flex-start">
              <NCheckbox
                :checked="selected.includes(claim.id)"
                @update:checked="(v: boolean) => toggle(claim.id, v)"
                style="margin-top: 3px"
              />
              <div style="flex: 1; min-width: 0">
                <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 4px; flex-wrap: wrap">
                  <span class="mono" style="color: #8a919c">#{{ claim.id }}</span>
                  <NTag size="tiny" :bordered="false">{{ claimKindLabels[claim.kind] ?? claim.kind }}</NTag>
                  <NTag size="tiny" :type="claimStatusType[claim.status]">{{ claimStatusLabels[claim.status] }}</NTag>
                  <span v-if="claim.organization" style="font-size: 12px; color: #5c6470">
                    {{ claim.organization }}
                    <span v-if="claim.role"> · {{ claim.role }}</span>
                    <span v-if="claim.period"> · {{ claim.period }}</span>
                  </span>
                  <span v-if="claim.source_ref" class="mono" style="font-size: 12px; color: #a0a6b0">
                    {{ claim.source_ref }}
                  </span>
                  <div style="flex: 1" />
                  <NButton size="tiny" quaternary @click="setOne(claim, claim.status === 'confirmed' ? 'pending' : 'confirmed')">
                    {{ claim.status === 'confirmed' ? '退回' : '确认' }}
                  </NButton>
                  <NButton size="tiny" quaternary type="error" @click="remove(claim)">删除</NButton>
                </div>
                <div style="line-height: 1.7">{{ claim.statement }}</div>
                <div v-if="claim.tags.length" style="display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap">
                  <NTag v-for="t in claim.tags" :key="t" size="tiny" :bordered="false">{{ t }}</NTag>
                </div>
              </div>
            </div>
          </NCard>
        </div>
      </NSpin>
    </template>

    <NModal v-model:show="importOpen" preset="card" title="从项目证据导入" style="width: 720px; max-width: 94vw" :bordered="false">
      <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px">
        <span style="color: #8a919c; font-size: 12px">共 {{ importEvidence.length }} 条，已选 {{ importSelected.length }} 条</span>
        <div style="flex: 1" />
        <NButton size="tiny" quaternary @click="importSelected = importEvidence.map(e => e.id)">全选</NButton>
        <NButton size="tiny" quaternary @click="importSelected = []">清空</NButton>
      </div>
      <div style="max-height: 52vh; overflow: auto; display: grid; gap: 8px">
        <NCard v-for="item in importEvidence" :key="item.id" size="small">
          <div style="display: flex; gap: 8px; align-items: flex-start">
            <NCheckbox
              :checked="importSelected.includes(item.id)"
              @update:checked="(v: boolean) => toggleImport(item.id, v)"
              style="margin-top: 3px"
            />
            <div style="flex: 1">
              <div style="line-height: 1.6">{{ item.statement }}</div>
              <div style="font-size: 11px; color: #a0a6b0; margin-top: 4px">
                <span v-for="(ref, i) in item.refs.slice(0, 2)" :key="i" class="mono">
                  {{ ref.path }}:{{ ref.line }}<span v-if="i < Math.min(item.refs.length, 2) - 1"> · </span>
                </span>
              </div>
            </div>
          </div>
        </NCard>
      </div>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <NButton quaternary @click="importOpen = false">取消</NButton>
          <NButton type="primary" :loading="importBusy" @click="submitImport">导入到账本</NButton>
        </div>
      </template>
    </NModal>

    <NModal v-model:show="copyOpen" preset="card" title="转入个人账本" style="width: 460px" :bordered="false">
      <div style="display: grid; gap: 10px">
        <span style="font-size: 13px; color: #5c6470">
          将选中的 {{ selected.length }} 条复制到个人账本，状态一并保留。
        </span>
        <NSelect v-model:value="copyTarget" :options="personalOptions" placeholder="目标账本" />
      </div>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <NButton quaternary @click="copyOpen = false">取消</NButton>
          <NButton type="primary" :loading="copyBusy" @click="submitCopy">转入</NButton>
        </div>
      </template>
    </NModal>

    <NModal v-model:show="ledgerModalOpen" preset="card"
            :title="ledgerModalMode === 'create' ? '新建个人账本' : '重命名账本'"
            style="width: 420px" :bordered="false">
      <NInput v-model:value="ledgerNameDraft" placeholder="账本名称" @keyup.enter="submitLedgerModal" />
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <NButton quaternary @click="ledgerModalOpen = false">取消</NButton>
          <NButton type="primary" :loading="ledgerBusy" @click="submitLedgerModal">确定</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>
