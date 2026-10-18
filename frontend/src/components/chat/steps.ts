import type { RunEvent } from '@/api'

export interface StepView {
  key: string
  kind: 'model' | 'tool' | 'result' | 'note' | 'investigation' | 'compaction' | 'confirm'
  title: string
  detail: string
  status: 'running' | 'ok' | 'error' | 'info'
  time?: string
  seq: number
}

function short(value: unknown, limit = 180): string {
  let text = ''
  if (typeof value === 'string') text = value
  else {
    try {
      text = JSON.stringify(value)
    } catch {
      text = String(value)
    }
  }
  return text.length > limit ? `${text.slice(0, limit)}…` : text
}

/** 把 Run 事件渲染成用户能读的步骤。只展示有信息量的事件。 */
export function toSteps(events: RunEvent[], prefix = ''): StepView[] {
  const key = (k: string) => `${prefix}${k}`
  const out: StepView[] = []
  let modelIndex = 0

  for (const event of events) {
    const p = event.payload ?? {}
    const agent = String(p.agent ?? 'chat')
    switch (event.type) {
      case 'agent.started': {
        modelIndex += 1
        out.push({
          key: key(`a-${event.seq}`),
          kind: 'model',
          title: agent === 'investigator' ? '调查员思考' : `第 ${modelIndex} 轮`,
          detail: '',
          status: 'running',
          seq: event.seq,
        })
        break
      }
      case 'agent.finished': {
        const tools = (p.tools as string[] | undefined) ?? []
        for (let i = out.length - 1; i >= 0; i -= 1) {
          if (out[i].kind === 'model' && out[i].status === 'running') {
            out[i].status = 'ok'
            if (tools.length) out[i].title = `决定调用 ${tools.length} 个工具`
            else if (p.answered) out[i].title = '给出回答'
            break
          }
        }
        break
      }
      case 'tool.called': {
        out.push({
          key: key(`c-${event.seq}`),
          kind: 'tool',
          title: String(p.tool ?? '工具'),
          detail: short(p.args ?? {}),
          status: 'running',
          seq: event.seq,
        })
        break
      }
      case 'tool.result': {
        const ok = p.ok !== false
        const summary = String(p.preview ?? p.error ?? '')
        // 把同名的上一个 running 工具步骤收敛
        for (let i = out.length - 1; i >= 0; i -= 1) {
          if (out[i].kind === 'tool' && out[i].status === 'running') {
            out[i].status = ok ? 'ok' : 'error'
            if (summary) out[i].detail = summary
            break
          }
        }
        break
      }      case 'chat.plan': {
        const steps = (p.steps as { label: string; status: string }[]) ?? []
        out.push({
          key: key(`p-${event.seq}`),
          kind: 'note',
          title: '更新计划',
          detail: steps.map((s) => `${s.label}（${s.status}）`).join(' → '),
          status: 'info',
          seq: event.seq,
        })
        break
      }
      case 'chat.investigation': {
        const done = p.status === 'done'
        out.push({
          key: key(`i-${event.seq}`),
          kind: 'investigation',
          title: done ? '调查完成' : p.status === 'failed' ? '调查失败' : '派出只读调查',
          detail: String(p.conclusion ?? p.question ?? p.error ?? ''),
          status: p.status === 'failed' ? 'error' : done ? 'ok' : 'running',
          seq: event.seq,
        })
        break
      }
      case 'chat.compaction': {
        out.push({
          key: key(`k-${event.seq}`),
          kind: 'compaction',
          title: `压缩了 ${p.entries ?? 0} 条历史`,
          detail: `节省 ${Math.max(0, Number(p.tokens_before ?? 0) - Number(p.tokens_after ?? 0))} tokens（${p.trigger ?? 'auto'}）`,
          status: 'info',
          seq: event.seq,
        })
        break
      }
      case 'chat.confirmation': {
        out.push({
          key: key(`f-${event.seq}`),
          kind: 'confirm',
          title: `等待确认：${p.tool ?? ''}`,
          detail: String(p.preview ?? ''),
          status: 'info',
          seq: event.seq,
        })
        break
      }
      case 'run.log': {
        if (p.level === 'debug') break
        out.push({
          key: key(`l-${event.seq}`),
          kind: 'note',
          title: p.level === 'warn' ? '提示' : '日志',
          detail: String(p.message ?? ''),
          status: p.level === 'warn' ? 'error' : 'info',
          seq: event.seq,
        })
        break
      }
      default:
        break
    }
  }

  // 还在跑的工具行保持 running；已完成 run 的不留 running
  return out.map((s) =>
    s.kind === 'model' && s.status === 'running' ? { ...s, status: 'ok' } : s,
  )
}
