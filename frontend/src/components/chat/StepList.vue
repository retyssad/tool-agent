<script setup lang="ts">
import { computed, ref } from 'vue'
import { NButton, NCollapseTransition, NIcon, NTag } from 'naive-ui'
import type { StepView } from './steps'

const props = defineProps<{ steps: StepView[]; expanded?: boolean }>()

const open = ref(props.expanded ?? false)

const iconFor = (step: StepView) =>
  step.kind === 'tool' ? 'M4 7h16M4 12h10M4 17h7' :
  step.kind === 'result' ? 'M5 12l4 4L19 6' :
  step.kind === 'investigation' ? 'M11 4a7 7 0 105 12l4 4' :
  step.kind === 'compaction' ? 'M4 7h16M4 12h16M4 17h10' :
  step.kind === 'confirm' ? 'M12 8v5m0 3h.01' :
  'M12 3v18M3 12h18'

const statusColor = (step: StepView) =>
  step.status === 'error' ? '#b3261e' :
  step.status === 'running' ? '#2458b8' :
  step.status === 'ok' ? '#1a7f43' : '#8a919c'

const summary = computed(() => {
  const total = props.steps.length
  const failed = props.steps.filter((s) => s.status === 'error').length
  if (!total) return ''
  return failed ? `${total} 步，${failed} 步出错` : `${total} 步`
})
</script>

<template>
  <div v-if="steps.length" class="step-block">
    <button class="step-toggle" @click="open = !open">
      <NIcon size="14" :color="steps.some((s) => s.status === 'error') ? '#b3261e' : '#5c6470'">
        <svg viewBox="0 0 24 24" fill="currentColor"><path :d="iconFor(steps[steps.length - 1])" /></svg>
      </NIcon>
      <span>执行过程</span>
      <span class="step-summary">{{ summary }}</span>
      <span class="step-chevron" :class="{ 'step-chevron--open': open }">▾</span>
    </button>

    <NCollapseTransition :show="open">
      <div class="step-list">
        <div v-for="step in steps" :key="step.key" class="step-row">
          <span class="step-dot" :style="{ background: statusColor(step) }" />
          <div class="step-body">
            <div class="step-title">
              <span>{{ step.title }}</span>
              <NTag v-if="step.status === 'running'" size="tiny" :bordered="false" type="info">进行中</NTag>
            </div>
            <div v-if="step.detail" class="step-detail">{{ step.detail }}</div>
          </div>
        </div>
      </div>
    </NCollapseTransition>
  </div>
</template>

<style scoped>
.step-block {
  margin: 6px 0 10px;
  border: 1px solid #e8eaee;
  border-radius: 8px;
  background: #fafbfc;
  overflow: hidden;
}
.step-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 13px;
  color: #3a4048;
  text-align: left;
}
.step-toggle:hover { background: #f2f4f7; }
.step-summary { color: #8a919c; font-size: 12px; }
.step-chevron { margin-left: auto; color: #8a919c; transition: transform 0.15s; }
.step-chevron--open { transform: rotate(180deg); }
.step-list { padding: 2px 10px 10px; display: grid; gap: 6px; }
.step-row { display: flex; gap: 8px; }
.step-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  margin-top: 7px;
  flex: 0 0 auto;
}
.step-body { min-width: 0; flex: 1; }
.step-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #3a4048;
}
.step-detail {
  color: #8a919c;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: "JetBrains Mono", "Cascadia Code", Consolas, monospace;
}
</style>
