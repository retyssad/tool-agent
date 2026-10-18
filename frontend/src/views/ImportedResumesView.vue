<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton, NCard, NEmpty, NInput, NModal, NPopconfirm, NSpin, NTag, NUpload, useMessage,
  type UploadFileInfo,
} from 'naive-ui'
import { resumesApi, type ImportedResumeItem } from '@/api/resumes'

const router = useRouter()
const message = useMessage()

const items = ref<ImportedResumeItem[]>([])
const loading = ref(false)
const keyword = ref('')

const showUpload = ref(false)
const uploadFiles = ref<UploadFileInfo[]>([])
const uploadTitle = ref('')
const uploadTags = ref('')
const uploading = ref(false)
const deletingId = ref<number | null>(null)

async function refresh() {
  loading.value = true
  try {
    items.value = await resumesApi.listImported(keyword.value.trim() || undefined)
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    loading.value = false
  }
}

onMounted(refresh)

let debounce: number | undefined
watch(keyword, (value) => {
  window.clearTimeout(debounce)
  debounce = window.setTimeout(() => {
    if (!value.trim()) refresh()
  }, 300)
})
onBeforeUnmount(() => window.clearTimeout(debounce))

function selectFile(options: { file: UploadFileInfo }) {
  uploadFiles.value = [options.file]
  if (!uploadTitle.value && options.file.name) {
    uploadTitle.value = options.file.name.replace(/\.pdf$/i, '')
  }
}

async function doUpload() {
  const file = uploadFiles.value[0]?.file
  if (!file) {
    message.warning('请选择 PDF 文件')
    return
  }
  uploading.value = true
  try {
    const created = await resumesApi.upload(file, uploadTitle.value, uploadTags.value)
    message.success(`已导入「${created.title}」，抽取 ${created.text.length} 字`)
    showUpload.value = false
    uploadFiles.value = []
    uploadTitle.value = ''
    uploadTags.value = ''
    await refresh()
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    uploading.value = false
  }
}

async function remove(item: ImportedResumeItem) {
  deletingId.value = item.id
  try {
    await resumesApi.deleteImported(item.id)
    items.value = items.value.filter((i) => i.id !== item.id)
    message.success('已删除导入简历')
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    deletingId.value = null
  }
}

function kb(bytes: number) {
  return bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`
}
</script>

<template>
  <div class="page" style="max-width: 1100px">
    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 4px">
      <NButton quaternary size="small" @click="router.push('/resumes')">← 简历库</NButton>
      <h1 class="page-title" style="margin: 0">导入简历</h1>
      <div style="flex: 1" />
      <NButton type="primary" @click="showUpload = true">导入 PDF</NButton>
    </div>
    <p class="page-desc">只保留原 PDF 与抽取文本，不做结构化改写；扫描件会被拒绝。</p>

    <div style="display: flex; gap: 12px; margin-bottom: 16px">
      <NInput
        v-model:value="keyword"
        placeholder="在正文中搜索，如：P99 / 转接率 / 某个技能"
        clearable
        style="max-width: 360px"
        @keyup.enter="refresh"
      />
      <NButton :loading="loading" @click="refresh">搜索</NButton>
    </div>

    <NSpin :show="loading">
      <NEmpty
        v-if="!items.length"
        :description="keyword.trim() ? '没有匹配的简历' : '还没有导入简历'"
      />
      <div v-else style="display: grid; gap: 12px">
        <NCard v-for="item in items" :key="item.id" size="small" hoverable
               @click="router.push(`/resumes/imported/${item.id}`)">
          <div style="display: flex; align-items: center; gap: 10px">
            <strong style="font-size: 15px">#{{ item.id }} {{ item.title }}</strong>
            <NTag size="tiny">{{ item.page_count }} 页</NTag>
            <NTag size="tiny" :bordered="false">{{ kb(item.file_size) }}</NTag>
            <NTag v-for="t in item.tags" :key="t" size="tiny" :bordered="false">{{ t }}</NTag>
            <div style="flex: 1" />
            <span class="mono" style="color: #8a919c">{{ item.original_name }}</span>
            <NPopconfirm @positive-click="remove(item)">
              <template #trigger>
                <NButton size="tiny" type="error" quaternary :loading="deletingId === item.id"
                         @click.stop>删除</NButton>
              </template>
              删除「{{ item.title }}」及其原 PDF？不可恢复。
            </NPopconfirm>
          </div>
        </NCard>
      </div>
    </NSpin>

    <NModal
      v-model:show="showUpload"
      preset="card"
      title="导入 PDF 简历"
      style="width: 560px; max-width: 92vw"
      :bordered="false"
    >
      <NUpload :default-upload="false" accept=".pdf" :max="1" :file-list="uploadFiles" @change="selectFile">
        <NButton>选择 PDF</NButton>
      </NUpload>
      <div style="color: #8a919c; font-size: 12px; margin: 8px 0 12px">
        仅支持带文字层的 PDF；扫描件会被拒绝，不做 OCR。
      </div>
      <NInput v-model:value="uploadTitle" placeholder="标题（留空则用文件名）" style="margin-bottom: 8px" />
      <NInput v-model:value="uploadTags" placeholder="标签，用逗号分隔（可选）" />
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <NButton quaternary @click="showUpload = false">取消</NButton>
          <NButton type="primary" :loading="uploading" @click="doUpload">导入</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>
