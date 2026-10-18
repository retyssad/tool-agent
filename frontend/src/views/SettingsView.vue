<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NCard, NDescriptions, NDescriptionsItem, NSpin, NTag, useMessage } from 'naive-ui'
import { api } from '@/api'

const settings = ref<Record<string, any> | null>(null)
const message = useMessage()

onMounted(async () => {
  try {
    settings.value = await api.settings()
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
})
</script>

<template>
  <div class="page">
    <h1 class="page-title">设置</h1>
    <p class="page-desc">密钥只从 .env 读取，界面仅显示掩码；修改后需重启后端。</p>
    <NSpin v-if="!settings" />
    <NCard v-else size="small" style="max-width: 720px">
      <NDescriptions :column="1" label-placement="left" bordered>
        <NDescriptionsItem label="模型">{{ settings.model }}</NDescriptionsItem>
        <NDescriptionsItem label="Base URL"><span class="mono">{{ settings.base_url }}</span></NDescriptionsItem>
        <NDescriptionsItem label="API Key">
          <NTag size="small">{{ settings.api_key_masked }}</NTag>
        </NDescriptionsItem>
        <NDescriptionsItem label="Redis"><span class="mono">{{ settings.redis_url }}</span></NDescriptionsItem>
        <NDescriptionsItem label="数据目录"><span class="mono">{{ settings.data_dir }}</span></NDescriptionsItem>
        <NDescriptionsItem label="源码白名单">
          <span class="mono">{{ settings.project_roots.join('   ') }}</span>
        </NDescriptionsItem>
      </NDescriptions>
    </NCard>
  </div>
</template>
