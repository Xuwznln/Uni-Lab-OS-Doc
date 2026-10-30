import { EDGE_API_BASE } from './edgeClient'

export interface PlannedNode {
  key: [string, string]
  device_id: string
  start_ms: number
  end_ms: number
  fixed: boolean
  /** false 表示仅用于时间线预测，尚未通过真实参数解析，不具备下发资格。 */
  parameters_final?: boolean
}

/** 候选的完成语义；information 只表示已知信息前缀，不代表整个 Task 完成。 */
export type CompletionTargetKind = 'task_completion' | 'information' | 'partial_scope'

export interface CompletionTarget {
  task_id: string
  node_keys: [string, string][]
  kind: CompletionTargetKind
  prefix_id: string
}

export interface TimeScheduleSnapshot {
  enabled: boolean
  algorithm: string
  active_solves: number
  input_error: string
  input: {
    revision: string
    partial_tasks: string[]
    blocked: [string, string, string][]
    completion_targets: CompletionTarget[]
  }
  latest: null | {
    revision: string
    time_origin: number
    status: string
    validated: boolean
    error: string
    nodes: PlannedNode[]
    completion_targets: CompletionTarget[]
    /** 服务端可能在候选本身重复声明部分任务，旧版本接口没有此字段。 */
    partial_tasks?: string[]
  }
}

export type TimeScheduleCandidate = NonNullable<TimeScheduleSnapshot['latest']>

/** 计划与当前输入一致且已验证时才能展示，连接中断或读取错误时不沿用旧预测。 */
export function currentTimeCandidate(snapshot: TimeScheduleSnapshot | undefined, connected: boolean, failed: boolean) {
  const latest = snapshot?.latest
  return connected && !failed && snapshot?.enabled && !snapshot.input_error
    && latest?.revision === snapshot.input.revision && latest?.validated
    && ['FEASIBLE', 'OPTIMAL'].includes(latest.status) ? latest : null
}

/**
 * 部分候选只包含某个任务当前已知的前缀。规划器下一轮重新计算时，后缀
 * 可能暂时没有区间；直接替换候选会让时间轴上的预计条反复消失。对这些
 * 任务保留上一轮已验证的节点，并将相对 time_origin 重基到当前候选。
 * 这只影响只读展示，实际下发仍由服务端严格按当前 revision 决定。
 */
export function mergePartialTimeCandidate(
  current: TimeScheduleCandidate | null | undefined,
  previous: TimeScheduleCandidate | null | undefined,
  partialTaskIds: readonly string[],
): TimeScheduleCandidate | null {
  if (!current) return null
  if (!previous || partialTaskIds.length === 0) return current

  const partial = new Set(partialTaskIds)
  const currentKeys = new Set(current.nodes.map((node) => JSON.stringify(node.key)))
  const originDelta = (previous.time_origin - current.time_origin) * 1000
  const retained = previous.nodes
    .filter((node) => partial.has(node.key[0]) && !currentKeys.has(JSON.stringify(node.key)))
    .map((node) => ({
      ...node,
      start_ms: node.start_ms + originDelta,
      end_ms: node.end_ms + originDelta,
    }))
    // A previous interval that has already completely fallen before the new
    // origin is historical data; actual execution records render that part.
    .filter((node) => node.end_ms > 0)
    .map((node) => ({ ...node, start_ms: Math.max(0, node.start_ms) }))

  return retained.length ? { ...current, nodes: [...current.nodes, ...retained] } : current
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('时间计划数据格式无效')
  return value as Record<string, unknown>
}

function finite(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error('时间计划包含无效时间')
  return value
}

function text(value: unknown): string {
  if (typeof value !== 'string') throw new Error('时间计划缺少身份或状态')
  return value
}

function strings(value: unknown): string[] {
  if (!Array.isArray(value)) throw new Error('时间计划列表格式无效')
  return value.map(text)
}

function isCompletionTargetKind(value: string): value is CompletionTargetKind {
  return value === 'task_completion' || value === 'information' || value === 'partial_scope'
}

function completionTargets(value: unknown): CompletionTarget[] {
  if (value === undefined) return []
  if (!Array.isArray(value)) throw new Error('时间计划完成目标格式无效')
  return value.map((item) => {
    const target = record(item)
    const keys = target.node_keys === undefined ? [] : target.node_keys
    if (!Array.isArray(keys)) throw new Error('时间计划完成目标节点格式无效')
    const nodeKeys = keys.map((key) => {
      const fields = strings(key)
      if (fields.length !== 2) throw new Error('时间计划完成目标节点身份无效')
      return [fields[0], fields[1]] as [string, string]
    })
    const kind = target.kind === undefined ? 'task_completion' : text(target.kind)
    if (!isCompletionTargetKind(kind)) {
      throw new Error('时间计划完成目标类型无效')
    }
    return {
      task_id: text(target.task_id),
      node_keys: nodeKeys,
      kind,
      prefix_id: target.prefix_id === undefined ? '' : text(target.prefix_id),
    }
  })
}

/** 此接口直接返回只读候选，不使用任务接口的 envelope，也不产生执行授权。 */
export async function loadTimeSchedule(signal?: AbortSignal): Promise<TimeScheduleSnapshot> {
  const response = await fetch(`${EDGE_API_BASE}/scheduler/time-plan`, { signal, headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`时间计划暂不可用（HTTP ${response.status}）`)
  const raw = record(await response.json())
  if (typeof raw.enabled !== 'boolean') throw new Error('时间计划缺少启用状态')
  const input = raw.input === undefined ? {} : record(raw.input)
  const blocked = (input.blocked === undefined ? [] : input.blocked)
  if (!Array.isArray(blocked)) throw new Error('时间计划待定原因格式无效')
  const result: TimeScheduleSnapshot = {
    enabled: raw.enabled,
    algorithm: text(raw.algorithm),
    active_solves: raw.active_solves === undefined ? 0 : finite(raw.active_solves),
    input_error: raw.input_error === undefined ? '' : text(raw.input_error),
    input: {
      revision: input.revision === undefined ? '' : text(input.revision),
      partial_tasks: input.partial_tasks === undefined ? [] : strings(input.partial_tasks),
      blocked: blocked.map((item) => {
        const fields = strings(item)
        if (fields.length !== 3) throw new Error('时间计划待定原因缺少节点身份')
        return [fields[0], fields[1], fields[2]]
      }),
      completion_targets: completionTargets(input.completion_targets),
    },
    latest: null,
  }
  if (raw.latest != null) {
    const latest = record(raw.latest)
    if (!Array.isArray(latest.nodes) || typeof latest.validated !== 'boolean') throw new Error('时间候选格式无效')
    result.latest = {
      revision: text(latest.revision), time_origin: finite(latest.time_origin),
      status: text(latest.status), validated: latest.validated,
      error: text(latest.error),
      partial_tasks: latest.partial_tasks === undefined ? [] : strings(latest.partial_tasks),
      completion_targets: completionTargets(latest.completion_targets),
      nodes: latest.nodes.map((value) => {
        const node = record(value)
        const key = strings(node.key)
        const start = finite(node.start_ms)
        const end = finite(node.end_ms)
        const parametersFinal = node.parameters_final === undefined ? true : node.parameters_final
        if (key.length !== 2 || start < 0 || end < start || typeof node.fixed !== 'boolean'
          || typeof parametersFinal !== 'boolean') throw new Error('候选节点身份或时间区间无效')
        return {
          key: [key[0], key[1]], device_id: text(node.device_id), start_ms: start,
          end_ms: end, fixed: node.fixed, parameters_final: parametersFinal,
        }
      }),
    }
  }
  return result
}
