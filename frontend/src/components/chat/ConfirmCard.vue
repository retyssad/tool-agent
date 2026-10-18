<script setup lang="ts">
import { NButton, NCard, NTag } from 'naive-ui'
import type { TurnAction } from '@/api/chat'

defineProps<{ action: TurnAction; resolving?: boolean }>()
const emit = defineEmits<{ confirm: [id: number]; reject: [id: number] }>()
</script>

<template>
  <NCard size="small" class="confirm-card">
    <div class="confirm-head">
      <NTag size="tiny" :type="action.status === 'pending' ? 'warning' : action.status === 'executed' ? 'success' : 'default'">
        {{ action.status === 'pending' ? '待确认' : action.status === 'executed' ? '已执行' : action.status === 'rejected' ? '已取消' : '执行失败' }}
      </NTag>
      <span class="mono confirm-tool">{{ action.tool }}</span>
    </div>
    <div class="confirm-preview">{{ action.preview }}</div>
    <div v-if="action.error" class="confirm-error">{{ action.error }}</div>
    <div v-if="action.status === 'pending'" class="confirm-actions">
      <NButton size="small" type="primary" :loading="resolving" @click="emit('confirm', action.id)">
        确认执行
      </NButton>
      <NButton size="small" quaternary :disabled="resolving" @click="emit('reject', action.id)">取消</NButton>
    </div>
  </NCard>
</template>

<style scoped>
.confirm-card {
  border-color: #f0c36d;
  background: #fffaf0;
  margin: 8px 0;
}
.confirm-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.confirm-tool { color: #8a919c; font-size: 11px; }
.confirm-status { margin-left: auto; font-size: 12px; color: #8a919c; }
.confirm-preview { line-height: 1.7; margin-bottom: 10px; font-size: 13px; }
.confirm-error { color: #b3261e; font-size: 12px; margin-bottom: 8px; }
.confirm-actions { display: flex; gap: 8px; }
</style>
