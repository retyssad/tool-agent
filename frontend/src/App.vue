<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { NIcon, NLayout, NLayoutSider, NMenu, NButton, NDrawer, NDrawerContent, NMessageProvider, NDialogProvider } from 'naive-ui'
import type { MenuOption } from 'naive-ui'
import RunDrawer from '@/components/RunDrawer.vue'
import { useRunsStore } from '@/stores/runs'

const route = useRoute()
const runs = useRunsStore()
const collapsed = ref(false)

function icon(path: string) {
  return () =>
    h(
      NIcon,
      { size: 18 },
      { default: () => h('svg', { viewBox: '0 0 24 24', fill: 'currentColor' }, [h('path', { d: path })]) },
    )
}

const menu: MenuOption[] = [
  { label: () => h(RouterLink, { to: '/assistant' }, { default: () => '助手' }), key: '/assistant', icon: icon('M4 5h16v11H9l-5 4zM8 9h8M8 12h5') },
  { label: () => h(RouterLink, { to: '/' }, { default: () => '总览' }), key: '/', icon: icon('M3 12l9-9 9 9v9H3z') },
  { label: () => h(RouterLink, { to: '/jobs' }, { default: () => '岗位库' }), key: '/jobs', icon: icon('M4 6h16v4H4zM4 12h16v6H4z') },
  { label: () => h(RouterLink, { to: '/resumes' }, { default: () => '简历库' }), key: '/resumes', icon: icon('M6 3h9l3 3v15H6z') },
  { label: () => h(RouterLink, { to: '/evidence' }, { default: () => '证据账本' }), key: '/evidence', icon: icon('M5 4h14v16H5zM9 9h6M9 13h6M9 17h3') },
  { label: () => h(RouterLink, { to: '/evaluations' }, { default: () => '简历评价' }), key: '/evaluations', icon: icon('M4 5h16v14H4zM8 9h8M8 13h5') },
  { label: () => h(RouterLink, { to: '/projects' }, { default: () => '项目导学' }), key: '/projects', icon: icon('M3 7h7l2 2h9v10H3z') },
  { label: () => h(RouterLink, { to: '/interviews' }, { default: () => '模拟面试' }), key: '/interviews', icon: icon('M4 5h16v11H9l-5 4z') },
  { label: () => h(RouterLink, { to: '/settings' }, { default: () => '设置' }), key: '/settings', icon: icon('M12 8a4 4 0 100 8 4 4 0 000-8zM4 12h2m12 0h2M12 4v2m0 12v2') },
]

// 助手是独立全高布局，不加内边距
const isAssistant = computed(() => route.path.startsWith('/assistant'))
const activeKey = computed(() => (route.path.startsWith('/runs') ? '/' : route.path))

onMounted(() => {
  runs.refresh().catch(() => {})
})
</script>

<template>
  <NMessageProvider>
    <NDialogProvider>
      <NLayout has-sider style="height: 100vh">
        <NLayoutSider
          bordered
          collapse-mode="width"
          :collapsed-width="64"
          :width="208"
          :collapsed="collapsed"
          show-trigger
          @collapse="collapsed = true"
          @expand="collapsed = false"
        >
          <div style="height: 56px; display: flex; align-items: center; gap: 8px; padding: 0 20px; font-weight: 600">
            求职工作台
          </div>
          <NMenu :value="activeKey" :options="menu" :collapsed="collapsed" :collapsed-width="64" />
          <div style="position: absolute; bottom: 16px; width: 100%; padding: 0 16px">
            <NButton size="small" block quaternary @click="runs.drawerOpen = true">Run 面板</NButton>
          </div>
        </NLayoutSider>
        <NLayout content-style="height: 100vh">
          <div :style="isAssistant ? { height: '100%' } : { height: '100%', overflow: 'auto' }">
            <RouterView />
          </div>
        </NLayout>
      </NLayout>

      <NDrawer v-model:show="runs.drawerOpen" :width="520" placement="right">
        <NDrawerContent title="Run 面板" closable>
          <RunDrawer />
        </NDrawerContent>
      </NDrawer>
    </NDialogProvider>
  </NMessageProvider>
</template>
