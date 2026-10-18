<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton, NCard, NEmpty, NInput, NSpin, NTabPane, NTabs, NTag, useMessage,
} from 'naive-ui'
import { resumesApi, type ImportedResumeDetail } from '@/api/resumes'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const resumeId = computed(() => Number(route.params.id))
const detail = ref<ImportedResumeDetail | null>(null)
const loading = ref(true)
const tab = ref<'pdf' | 'text'>('pdf')

const editingTitle = ref(false)
const titleDraft = ref('')
const tagsDraft = ref('')
const notesDraft = ref('')
const saving = ref(false)

const pdfUrl = computed(() => (detail.value ? resumesApi.fileUrl(detail.value.id) : ''))

onMounted(async () => {
  try {
    detail.value = await resumesApi.getImported(resumeId.value)
    titleDraft.value = detail.value.title
    tagsDraft.value = detail.value.tags.join(', ')
    notesDraft.value = detail.value.notes
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    loading.value = false
  }
})

async function save() {
  if (!detail.value) return
  saving.value = true
  try {
    const tags = tagsDraft.value
      .split(/[,，]/)
      .map((t) => t.trim())
      .filter(Boolean)
    await resumesApi.updateImported(detail.value.id, {
      title: titleDraft.value,
      tags,
      notes: notesDraft.value,
    })
    detail.value = await resumesApi.getImported(detail.value.id)
    editingTitle.value = false
    message.success('已保存')
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    saving.value = false
  }
}

async function remove() {
  if (!detail.value) return
  try {
    await resumesApi.deleteImported(detail.value.id)
    message.success('已删除')
    router.push('/resumes')
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}
</script>

<template>
  <div class="page" style="max-width: 1400px">
    <NSpin :show="loading">
      <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 4px">
        <NButton quaternary size="small" @click="router.push('/resumes')">← 简历库</NButton>
        <div style="flex: 1" />
        <NButton size="small" :loading="saving" @click="save">保存</NButton>
        <NButton size="small" type="error" quaternary @click="remove">删除</NButton>
      </div>

      <template v-if="detail">
        <div v-if="!editingTitle" style="display: flex; align-items: center; gap: 10px; margin: 8px 0">
          <h1 class="page-title" style="margin: 0">#{{ detail.id }} {{ detail.title }}</h1>
          <NButton size="tiny" quaternary @click="editingTitle = true">改名</NButton>
          <NTag size="tiny">{{ detail.page_count }} 页</NTag>
        </div>
        <div v-else style="display: flex; gap: 8px; margin: 8px 0">
          <NInput v-model:value="titleDraft" style="max-width: 360px" />
          <NButton size="small" @click="editingTitle = false">完成</NButton>
        </div>

        <p class="page-desc">
          <span class="mono">{{ detail.original_name }}</span>
          · 抽取 {{ detail.text.length }} 字
          · 导入于 {{ new Date(detail.created_at).toLocaleString('zh-CN') }}
        </p>

        <div style="display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 16px">
          <NCard size="small" :bordered="false" style="padding: 0">
            <NTabs v-model:value="tab" type="line">
              <NTabPane name="pdf" tab="原文件">
                <iframe
                  :src="pdfUrl"
                  style="width: 100%; height: calc(100vh - 240px); border: 0; background: #f2f3f5"
                />
              </NTabPane>
              <NTabPane name="text" tab="抽取文本">
                <div
                  class="mono"
                  style="white-space: pre-wrap; line-height: 1.8; max-height: calc(100vh - 240px); overflow: auto"
                >{{ detail.text }}</div>
              </NTabPane>
            </NTabs>
          </NCard>

          <div style="display: grid; gap: 16px; align-content: start">
            <NCard size="small" title="标签">
              <NInput v-model:value="tagsDraft" placeholder="用逗号分隔" />
              <div v-if="detail.tags.length" style="display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px">
                <NTag v-for="t in detail.tags" :key="t" size="tiny" :bordered="false">{{ t }}</NTag>
              </div>
            </NCard>

            <NCard size="small" title="备注">
              <NInput v-model:value="notesDraft" type="textarea" :autosize="{ minRows: 5 }" />
            </NCard>

            <NCard size="small" title="说明">
              <p style="margin: 0; color: #8a919c; line-height: 1.7; font-size: 13px">
                这是导入简历，只保存原 PDF 与抽取文本。不会转成结构化 JSON，
                也不会参与生成简历的证据门禁；后续评价与面试会直接读取抽取文本。
              </p>
            </NCard>
          </div>
        </div>
      </template>
      <NEmpty v-else description="简历不存在" />
    </NSpin>
  </div>
</template>
