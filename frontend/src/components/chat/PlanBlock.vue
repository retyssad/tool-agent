<script setup lang="ts">
import { computed } from 'vue'
import { NIcon, NTag } from 'naive-ui'
import type { PlanStep } from '@/api/chat'

defineProps<{ steps: PlanStep[] }>()

const iconFor = (status: string) =>
  status === 'done' ? 'M5 12l4 4L19 6' :
  status === 'running' ? 'M12 3a9 9 0 109 9' :
  status === 'failed' ? 'M6 6l12 12M18 6L6 18' :
  'M12 7v5l3 2'

const colorFor = (status: string) =>
  status === 'done' ? '#1a7f43' :
  status === 'running' ? '#2458b8' :
  status === 'failed' ? '#b3261e' : '#a7adb6'
</script>

<template>
  <div v-if="steps.length" class="plan">
    <div class="plan-title">
      计划
      <NTag size="tiny" :bordered="false">
        {{ steps.filter((s) => s.status === 'done').length }}/{{ steps.length }}
      </NTag>
    </div>
    <div v-for="step in steps" :key="step.id" class="plan-row" :class="`plan-row--${step.status}`">
      <NIcon size="14" :color="colorFor(step.status)">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
          <path :d="iconFor(step.status)" />
        </svg>
      </NIcon>
      <span class="plan-label">{{ step.label }}</span>
      <span v-if="step.status === 'running'" class="plan-running">进行中</span>
    </div>
  </div>
</template>

<style scoped>
.plan {
  border: 1px solid #e8eaee;
  border-radius: 8px;
  background: #fbfcfd;
  padding: 8px 10px;
  margin: 6px 0;
}
.plan-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #8a919c;
  margin-bottom: 6px;
}
.plan-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  padding: 2px 0;
  color: #3a4048;
}
.plan-row--done .plan-label { color: #8a919c; text-decoration: line-through; }
.plan-row--running .plan-label { font-weight: 600; }
.plan-row--failed .plan-label { color: #b3261e; }
.plan-label { flex: 1; }
.plan-running { font-size: 11px; color: #2458b8; }
</style>
