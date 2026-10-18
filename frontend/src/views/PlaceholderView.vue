<script setup lang="ts">
import { useRoute } from 'vue-router'
import { NEmpty } from 'naive-ui'

const route = useRoute()
const titles: Record<string, string> = {
  '/jobs': '岗位库',
  '/resumes': '简历库',
  '/evaluations': '简历评价',
  '/projects': '项目导学',
  '/interviews': '模拟面试',
}
</script>

<template>
  <div class="page">
    <h1 class="page-title">{{ titles[route.path] ?? '功能' }}</h1>
    <p class="page-desc">该模块将在后续里程碑接入 Run 底座。</p>
    <NEmpty description="尚未实现" />
  </div>
</template>
