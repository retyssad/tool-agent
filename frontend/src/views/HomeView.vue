<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton, NCard, NEmpty, NGrid, NGridItem, NSpin, NStatistic, NTag, useMessage,
} from 'naive-ui'
import { api, type Overview } from '@/api'
import { jobStatusLabels, jobStatusType } from '@/api/jobs'
import { useRunsStore } from '@/stores/runs'

const runs = useRunsStore()
const router = useRouter()
const message = useMessage()
const health = ref<{ ok: boolean; db: any; redis: any; model: string } | null>(null)
const overview = ref<Overview | null>(null)
const loading = ref(true)

onMounted(async () => {
  try {
    const [h, o] = await Promise.all([
      api.health().catch(() => null),
      api.overview(),
    ])
    health.value = h
    overview.value = o
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    loading.value = false
  }
  await runs.refresh().catch(() => {})
})

const busyRuns = computed(
  () => runs.runs.filter((r) => r.status === 'running' || r.status === 'queued').length,
)
// 只提示最近 24 小时的失败，且排除「后来同目标已经成功」的情况
const failedRuns = computed(() => {
  const cutoff = Date.now() - 24 * 60 * 60 * 1000
  const key = (r: { kind: string; target: Record<string, unknown> }) =>
    `${r.kind}:${JSON.stringify(r.target ?? {})}`
  const succeeded = new Set(
    runs.runs.filter((r) => r.status === 'succeeded').map(key),
  )
  return runs.runs.filter((r) => {
    if (r.status !== 'failed') return false
    if (new Date(r.created_at).getTime() < cutoff) return false
    return !succeeded.has(key(r))
  }).length
})

const modules = computed(() => {
  const o = overview.value
  return [
    {
      title: '岗位库',
      to: '/jobs',
      primary: `${o?.jobs.total ?? 0} 个岗位`,
      secondary: o
        ? `已投 ${o.jobs.by_status.applied ?? 0} · 面试中 ${o.jobs.by_status.interviewing ?? 0} · 收藏 ${o.jobs.by_status.saved ?? 0}`
        : '',
      action: '粘贴 JD',
      actionTo: '/jobs',
    },
    {
      title: '简历库',
      to: '/resumes',
      primary: `${o?.resumes.drafts ?? 0} 份生成简历`,
      secondary: o
        ? `已导出 ${o.resumes.versions} 个版本 · 导入 ${o.resumes.imported} 份`
        : '',
      action: '打开简历库',
      actionTo: '/resumes',
    },
    {
      title: '证据账本',
      to: '/evidence',
      primary: `${o?.claims.confirmed ?? 0} 条已确认`,
      secondary: o
        ? o.claims.pending > 0
          ? `${o.claims.pending} 条待确认，确认后才能进简历`
          : `共 ${o.claims.total} 条，全部处理完`
        : '',
      action: o?.claims.pending ? '去确认' : '查看账本',
      actionTo: '/evidence',
      highlight: (o?.claims.pending ?? 0) > 0,
    },
    {
      title: '简历评价',
      to: '/evaluations',
      primary: `${o?.evaluations.total ?? 0} 次评价`,
      secondary: o?.evaluations.avg_score != null
        ? `平均 ${o.evaluations.avg_score} 分`
        : '还没有评价',
      action: '发起评价',
      actionTo: '/resumes',
    },
    {
      title: '项目导学',
      to: '/projects',
      primary: `${o?.projects.total ?? 0} 个项目`,
      secondary: o
        ? `${o.projects.evidence} 条证据${o.projects.scanning ? ` · ${o.projects.scanning} 个扫描中` : ''}`
        : '',
      action: '查看项目',
      actionTo: '/projects',
    },
    {
      title: '模拟面试',
      to: '/interviews',
      primary: `${o?.interviews.completed ?? 0} 场已完成`,
      secondary: o
        ? `${o.interviews.active} 场进行中${o.interviews.avg_score != null ? ` · 平均 ${o.interviews.avg_score} 分` : ''}`
        : '',
      action: '新建面试',
      actionTo: '/interviews',
      highlight: (o?.interviews.active ?? 0) > 0,
    },
  ]
})

const todos = computed(() => {
  const o = overview.value
  if (!o) return []
  const list: { text: string; to: string; tag: string; type: 'warning' | 'info' | 'error' }[] = []
  if (o.claims.pending > 0) {
    list.push({
      text: `${o.claims.pending} 条证据待确认`,
      to: '/evidence',
      tag: '证据',
      type: 'warning',
    })
  }
  if (o.jobs.pending_parse > 0) {
    list.push({
      text: `${o.jobs.pending_parse} 个岗位正在解析`,
      to: '/jobs',
      tag: '岗位',
      type: 'info',
    })
  }
  if (o.projects.scanning > 0) {
    list.push({
      text: `${o.projects.scanning} 个项目正在扫描`,
      to: '/projects',
      tag: '项目',
      type: 'info',
    })
  }
  if (o.interviews.active > 0) {
    list.push({
      text: `${o.interviews.active} 场面试未完成`,
      to: '/interviews',
      tag: '面试',
      type: 'info',
    })
  }
  if (o.evaluations.total > 0 && o.resumes.drafts === 0) {
    list.push({
      text: '有评价记录但没有进行中的简历',
      to: '/resumes',
      tag: '简历',
      type: 'warning',
    })
  }
  return list
})

const scoreColor = (v?: number | null) => {
  if (v == null) return '#8a919c'
  if (v >= 80) return '#1a7f43'
  if (v >= 55) return '#a35c00'
  return '#b3261e'
}
</script>

<template>
  <div class="page" style="max-width: 1240px">
    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 4px">
      <h1 class="page-title" style="margin: 0">工作台总览</h1>
      <div style="flex: 1" />
      <NTag v-if="busyRuns" size="small" type="info">{{ busyRuns }} 个任务执行中</NTag>
      <NTag v-if="failedRuns" size="small" type="error">{{ failedRuns }} 个任务失败</NTag>
    </div>
    <p class="page-desc">
      JD 解析 → 证据账本 → 简历生成与评价 → 项目导学 → 模拟面试，一条链路走完。
    </p>

    <NSpin :show="loading">
      <!-- 待办 -->
      <NCard v-if="todos.length" size="small" title="现在要做的" style="margin-bottom: 16px">
        <div style="display: grid; gap: 8px">
          <div
            v-for="t in todos"
            :key="t.text"
            style="display: flex; align-items: center; gap: 10px; cursor: pointer"
            @click="router.push(t.to)"
          >
            <NTag size="tiny" :type="t.type">{{ t.tag }}</NTag>
            <span>{{ t.text }}</span>
            <div style="flex: 1" />
            <span style="color: #2458b8; font-size: 13px">去处理 →</span>
          </div>
        </div>
      </NCard>

      <!-- 六个模块 -->
      <NGrid :cols="3" :x-gap="16" :y-gap="16" style="margin-bottom: 20px">
        <NGridItem v-for="m in modules" :key="m.to">
          <NCard
            size="small" hoverable
            :style="m.highlight ? { borderColor: '#f0c36d' } : undefined"
            @click="router.push(m.to)"
          >
            <div style="display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px">
              <strong style="font-size: 15px">{{ m.title }}</strong>
              <div style="flex: 1" />
              <NTag v-if="m.highlight" size="tiny" type="warning">待处理</NTag>
            </div>
            <div style="font-size: 20px; font-weight: 600; margin-bottom: 4px">{{ m.primary }}</div>
            <div style="color: #8a919c; font-size: 13px; min-height: 20px">{{ m.secondary }}</div>
            <NButton size="tiny" quaternary style="margin-top: 8px; padding-left: 0" @click.stop="router.push(m.actionTo)">
              {{ m.action }} →
            </NButton>
          </NCard>
        </NGridItem>
      </NGrid>

      <NGrid :cols="3" :x-gap="16" :y-gap="16">
        <!-- 最近岗位 -->
        <NGridItem>
          <NCard size="small" title="最近岗位">
            <NEmpty v-if="!overview?.recent_jobs.length" description="还没有岗位" size="small" />
            <div v-else style="display: grid; gap: 8px">
              <div
                v-for="j in overview.recent_jobs"
                :key="j.id"
                style="display: flex; align-items: center; gap: 8px; cursor: pointer"
                @click="router.push(`/jobs/${j.id}`)"
              >
                <span class="mono" style="color: #8a919c">#{{ j.id }}</span>
                <span style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
                  {{ j.title }}
                </span>
                <NTag size="tiny" :type="jobStatusType[j.status]">{{ jobStatusLabels[j.status] }}</NTag>
              </div>
            </div>
          </NCard>
        </NGridItem>

        <!-- 最近面试 -->
        <NGridItem>
          <NCard size="small" title="最近面试">
            <NEmpty v-if="!overview?.recent_interviews.length" description="还没有面试" size="small" />
            <div v-else style="display: grid; gap: 8px">
              <div
                v-for="i in overview.recent_interviews"
                :key="i.id"
                style="display: flex; align-items: center; gap: 8px; cursor: pointer"
                @click="router.push(i.status === 'completed' ? `/interviews/${i.id}/report` : `/interviews/${i.id}`)"
              >
                <span class="mono" style="color: #8a919c">#{{ i.id }}</span>
                <span style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
                  {{ i.title }}
                </span>
                <span v-if="i.score != null" style="font-weight: 600" :style="{ color: scoreColor(i.score) }">
                  {{ i.score }}
                </span>
                <NTag v-else size="tiny" :bordered="false">
                  {{ i.status === 'active' ? '进行中' : i.status === 'abandoned' ? '已放弃' : '—' }}
                </NTag>
              </div>
            </div>
          </NCard>
        </NGridItem>

        <!-- 运行环境 -->
        <NGridItem>
          <NCard size="small" title="运行环境">
            <NSpin v-if="!health" size="small" />
            <template v-else>
              <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px">
                <NTag size="small" :type="health.db.ok ? 'success' : 'error'">
                  SQLite {{ health.db.ok ? '正常' : '异常' }}
                </NTag>
                <NTag size="small" :type="health.redis.ok ? 'success' : 'error'">
                  Redis {{ health.redis.ok ? '正常' : '异常' }}
                </NTag>
              </div>
              <div class="mono" style="color: #8a919c; font-size: 12px; margin-bottom: 10px">
                {{ health.model }}
              </div>
              <div style="display: flex; gap: 20px">
                <NStatistic label="Run 总数" :value="runs.runs.length" size="small" />
                <NStatistic label="执行中" :value="busyRuns" size="small" />
                <NStatistic label="失败" :value="failedRuns" size="small" />
              </div>
              <NButton size="tiny" quaternary style="margin-top: 10px; padding-left: 0" @click="runs.drawerOpen = true">
                打开 Run 面板 →
              </NButton>
            </template>
          </NCard>
        </NGridItem>
      </NGrid>
    </NSpin>
  </div>
</template>
