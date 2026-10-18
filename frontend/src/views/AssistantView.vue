<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton, NEmpty, NInput, NPopconfirm, NSpin, NTag, useMessage,
} from 'naive-ui'
import { chatApi, type ChatTool } from '@/api/chat'
import ConfirmCard from '@/components/chat/ConfirmCard.vue'
import PlanBlock from '@/components/chat/PlanBlock.vue'
import RunCard from '@/components/chat/RunCard.vue'
import StepList from '@/components/chat/StepList.vue'
import { toSteps } from '@/components/chat/steps'
import { useChatStore } from '@/stores/chat'
import { useRunsStore } from '@/stores/runs'

const router = useRouter()
const message = useMessage()
const chat = useChatStore()
const runs = useRunsStore()

const input = ref('')
const scroller = ref<HTMLElement | null>(null)
const resolving = ref<number | null>(null)
const showSessions = ref(true)
const tools = ref<ChatTool[]>([])
const showTools = ref(false)

const samples = [
  '现在工作台里有什么？',
  '帮我看一下最近失败的 Run 是什么原因',
  '把岗位 3 标记为面试中',
  '用草稿 1 生成一版简历',
  '扫描项目 2，扫完告诉我结果',
]

const activeTurn = computed(() => chat.activeTurn)
const busy = computed(() => activeTurn.value?.status === 'running' || activeTurn.value?.status === 'queued')

const pendingCount = computed(() => chat.pendingActions.length)

function stepViews(turnId: number) {
  const turn = chat.turns.find((t) => t.id === turnId)
  if (!turn) return []
  // 一轮可能跨多个 Run（确认续跑）：按发生顺序拼接，形成完整时间线
  return turn.run_ids.flatMap((runId) => toSteps(runs.events[runId] ?? [], `r${runId}-`))
}

function liveText(turnId: number) {
  const turn = chat.turns.find((t) => t.id === turnId)
  const id = turn?.current_run_id
  if (!id) return ''
  return runs.deltas[id] ?? ''
}

function childRunIds(turnId: number) {
  const turn = chat.turns.find((t) => t.id === turnId)
  return turn?.child_run_ids ?? []
}

async function scrollToBottom() {
  await nextTick()
  if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
}

async function send() {
  const text = input.value.trim()
  if (!text || chat.sending) return
  input.value = ''
  try {
    await chat.send(text)
    await scrollToBottom()
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}

async function confirm(id: number) {
  resolving.value = id
  try {
    await chat.confirm(id)
    message.success('已执行')
    await scrollToBottom()
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    resolving.value = null
  }
}

async function reject(id: number) {
  resolving.value = id
  try {
    await chat.reject(id)
    message.info('已取消')
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  } finally {
    resolving.value = null
  }
}

async function cancelTurn() {
  if (!activeTurn.value) return
  try {
    await chat.cancel(activeTurn.value.id)
    message.info('已取消本轮')
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}

async function compact() {
  try {
    const result = await chat.compact()
    if (!result?.ok) message.info(result?.message || '没有可压缩的内容')
    else message.success(`已压缩：${result.tokens_before} → ${result.tokens_after} tokens`)
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
}

async function openSession(id: number) {
  await chat.open(id)
  await scrollToBottom()
}

async function newSession() {
  await chat.newSession()
  message.success('已新建对话')
}

async function removeSession(id: number) {
  await chat.removeSession(id)
  message.success('已删除')
}

async function toggleTools() {
  showTools.value = !showTools.value
  if (showTools.value && !tools.value.length) {
    tools.value = await chatApi.tools().catch(() => [])
  }
}

function openLink(url?: string) {
  if (!url) return
  if (url.startsWith('/api/')) window.open(url, '_blank')
  else router.push(url)
}

watch(
  () => runs.finished,
  async () => {
    const turn = activeTurn.value
    if (!turn) return
    await chat.syncTurn(turn.id)
    await scrollToBottom()
  },
  { deep: true },
)

watch(
  () => activeTurn.value?.id,
  () => scrollToBottom(),
)

onMounted(async () => {
  try {
    await chat.bootstrap()
    await scrollToBottom()
  } catch (err) {
    message.error(String(err instanceof Error ? err.message : err))
  }
})

onBeforeUnmount(() => {
  for (const turn of chat.turns) {
    if (turn.current_run_id) runs.disconnect(turn.current_run_id)
  }
})

const riskLabel: Record<string, string> = {
  read: '只读',
  write: '轻写',
  destructive: '需确认',
  slow: '慢任务',
}
</script>

<template>
  <div class="assistant">
    <!-- 会话列表 -->
    <aside v-if="showSessions" class="sessions">
      <div class="sessions-head">
        <strong>对话</strong>
        <div style="flex: 1" />
        <NButton size="tiny" quaternary @click="newSession">新建</NButton>
      </div>
      <div class="session-list">
        <div
          v-for="s in chat.sessions"
          :key="s.id"
          class="session-row"
          :class="{ 'session-row--active': s.id === chat.activeId }"
          @click="openSession(s.id)"
        >
          <span class="session-title">{{ s.title || '新对话' }}</span>
          <span class="session-meta">{{ s.turns }} 轮</span>
          <NPopconfirm @positive-click="removeSession(s.id)">
            <template #trigger>
              <button class="session-del" title="删除" @click.stop>×</button>
            </template>
            删除这段对话？不可恢复。
          </NPopconfirm>
        </div>
        <NEmpty v-if="!chat.sessions.length" description="还没有对话" size="small" style="margin-top: 24px" />
      </div>
      <div class="sessions-foot">
        <NButton size="tiny" quaternary block @click="toggleTools">
          可用工具 {{ showTools ? '▴' : '▾' }}
        </NButton>
      </div>
    </aside>

    <!-- 主区 -->
    <main class="main">
      <header class="head">
        <div class="head-left">
          <NButton size="tiny" quaternary @click="showSessions = !showSessions">
            {{ showSessions ? '收起列表' : '会话' }}
          </NButton>
          <span class="head-title">{{ chat.detail?.title || '助手' }}</span>
          <NTag v-if="pendingCount" size="tiny" type="warning">{{ pendingCount }} 项待确认</NTag>
          <NTag v-if="busy" size="tiny" type="info">执行中</NTag>
        </div>
        <div style="flex: 1" />
        <NButton v-if="busy" size="tiny" quaternary type="error" @click="cancelTurn">取消本轮</NButton>
        <NButton size="tiny" quaternary @click="compact">压缩上下文</NButton>
      </header>

      <div ref="scroller" class="stream">
        <NSpin :show="chat.loading">
          <NEmpty
            v-if="!chat.turns.length"
            description="告诉我你要做什么。多步任务我会先列计划，每一步都能看见。"
            style="margin-top: 60px"
          >
            <template #extra>
              <div class="samples">
                <NButton
                  v-for="s in samples" :key="s" size="small" quaternary class="sample"
                  @click="input = s; send()"
                >{{ s }}</NButton>
              </div>
            </template>
          </NEmpty>

          <div class="turn-list">
            <div v-for="turn in chat.turns" :key="turn.id" class="turn">
              <!-- 用户 -->
              <div class="user-row">
                <div class="user-bubble">{{ turn.user_content }}</div>
              </div>

              <!-- 助手 -->
              <div class="assistant-row">
                <PlanBlock v-if="turn.plan?.length" :steps="turn.plan" />

                <StepList :steps="stepViews(turn.id)" />

                <!-- 慢任务子 Run -->
                <RunCard
                  v-for="rid in childRunIds(turn.id)"
                  :key="rid"
                  :run-id="rid"
                />

                <!-- 正在流式输出的文本 -->
                <div v-if="!turn.assistant_content && liveText(turn.id)" class="answer">
                  {{ liveText(turn.id) }}
                </div>

                <div v-if="turn.assistant_content" class="answer">{{ turn.assistant_content }}</div>

                <!-- 追问选项 / 链接卡（后端持久化） -->
                <template v-for="(c, ci) in turn.cards" :key="`c-${turn.id}-${ci}`">
                  <div v-if="c.type === 'ask'" class="ask-card">
                    <div class="ask-question">{{ c.question }}</div>
                    <div v-if="c.options?.length" class="ask-options">
                      <NButton
                        v-for="opt in c.options" :key="opt" size="small" secondary class="sample"
                        @click="input = opt; send()"
                      >{{ opt }}</NButton>
                    </div>
                  </div>
                  <div v-else-if="c.type === 'link'" class="link-card">
                    <NButton size="small" quaternary @click="openLink(c.url)">{{ c.label }} →</NButton>
                  </div>
                </template>

                <!-- 还在跑 -->
                <div v-if="turn.status === 'running' && !liveText(turn.id)" class="thinking">
                  <NSpin size="small" />
                  <span>正在执行…</span>
                </div>

                <!-- 确认卡 -->
                <ConfirmCard
                  v-for="a in turn.actions"
                  :key="a.id"
                  :action="a"
                  :resolving="resolving === a.id"
                  @confirm="confirm"
                  @reject="reject"
                />

                <div v-if="turn.status === 'failed'" class="turn-error">
                  执行失败：{{ turn.error }}
                </div>
                <div v-else-if="turn.status === 'cancelled'" class="turn-muted">本轮已取消</div>
              </div>
            </div>
          </div>
        </NSpin>
      </div>

      <footer class="composer">
        <NInput
          v-model:value="input"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 6 }"
          :disabled="busy"
          placeholder="告诉我要做什么，例如「扫描项目 2，扫完告诉我结果」。Ctrl+Enter 发送"
          @keydown.ctrl.enter.prevent="send"
        />
        <div class="composer-foot">
          <span class="hint">删除、创建、导出、取消 Run 会先弹确认；慢任务在这里就能看进度</span>
          <div style="flex: 1" />
          <NButton size="small" type="primary" :loading="chat.sending" :disabled="!input.trim() || busy" @click="send">
            发送
          </NButton>
        </div>
      </footer>
    </main>

    <!-- 工具清单 -->
    <aside v-if="showTools" class="tools">
      <div class="tools-head">
        <strong>可用工具</strong>
        <div style="flex: 1" />
        <NButton size="tiny" quaternary @click="showTools = false">×</NButton>
      </div>
      <div class="tool-list">
        <div v-for="tool in tools" :key="tool.name" class="tool-row">
          <div class="tool-name">
            <span class="mono">{{ tool.name }}</span>
            <NTag size="tiny" :bordered="false">{{ riskLabel[tool.risk] ?? tool.risk }}</NTag>
          </div>
          <div class="tool-desc">{{ tool.description }}</div>
        </div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.assistant {
  display: flex;
  height: 100vh;
  background: var(--wb-bg);
}
.sessions {
  width: 220px;
  border-right: 1px solid var(--wb-border);
  background: #fbfcfd;
  display: flex;
  flex-direction: column;
  flex: 0 0 auto;
}
.sessions-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 12px 8px;
}
.session-list { flex: 1; overflow: auto; padding: 4px 8px; }
.session-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 8px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
}
.session-row:hover { background: #f1f4f8; }
.session-row--active { background: #e8f0ff; }
.session-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session-meta { color: #a7adb6; font-size: 11px; }
.session-del {
  border: none;
  background: none;
  color: #a7adb6;
  cursor: pointer;
  font-size: 15px;
  line-height: 1;
  padding: 0 2px;
}
.session-del:hover { color: #b3261e; }
.sessions-foot { padding: 8px 12px 12px; }

.main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 20px;
  border-bottom: 1px solid var(--wb-border);
  background: #fff;
}
.head-left { display: flex; align-items: center; gap: 8px; min-width: 0; }
.head-title { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.stream { flex: 1; overflow: auto; padding: 16px 20px 8px; }
.turn-list { display: grid; gap: 18px; max-width: 860px; margin: 0 auto; }
.turn { display: grid; gap: 8px; }
.user-row { display: flex; justify-content: flex-end; }
.user-bubble {
  max-width: 80%;
  background: #2458b8;
  color: #fff;
  padding: 8px 12px;
  border-radius: 10px 10px 2px 10px;
  white-space: pre-wrap;
  line-height: 1.6;
  font-size: 14px;
}
.assistant-row { min-width: 0; }
.answer {
  white-space: pre-wrap;
  line-height: 1.75;
  font-size: 14px;
  background: #fff;
  border: 1px solid var(--wb-border);
  padding: 10px 12px;
  border-radius: 10px 10px 10px 2px;
}
.thinking { display: flex; align-items: center; gap: 8px; color: #8a919c; font-size: 13px; }
.turn-error { color: #b3261e; font-size: 13px; }
.turn-muted { color: #8a919c; font-size: 13px; }
.samples { display: grid; gap: 6px; margin-top: 8px; }
.sample { justify-content: flex-start; }
.ask-card {
  border: 1px solid #cfe0f7;
  border-radius: 8px;
  background: #f7faff;
  padding: 10px 12px;
  margin: 8px 0;
}
.ask-question { font-size: 13px; margin-bottom: 8px; }
.ask-options { display: grid; gap: 6px; }
.link-card { margin: 6px 0; }

.composer { border-top: 1px solid var(--wb-border); padding: 10px 20px 14px; background: #fff; }
.composer-foot { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.hint { color: #a7adb6; font-size: 11px; }

.tools {
  width: 260px;
  border-left: 1px solid var(--wb-border);
  background: #fbfcfd;
  display: flex;
  flex-direction: column;
  flex: 0 0 auto;
}
.tools-head { display: flex; align-items: center; gap: 8px; padding: 14px 12px 8px; }
.tool-list { flex: 1; overflow: auto; padding: 4px 12px 12px; display: grid; gap: 10px; }
.tool-row { font-size: 12px; }
.tool-name { display: flex; align-items: center; gap: 6px; }
.tool-desc { color: #8a919c; line-height: 1.6; margin-top: 2px; }
</style>
