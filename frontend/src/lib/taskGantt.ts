import type { NodePresentationStatus, TaskNode, WorkflowTask } from '../types'
import type { PlannedNode, TimeScheduleSnapshot } from './timeScheduleClient'
import { formatTimeScheduleReason } from './timeScheduleFormatting'
export { formatTimeScheduleReason as formatGanttReason } from './timeScheduleFormatting'

export interface GanttBar {
  id: string
  taskUuid: string
  nodeUuid: string
  name: string
  /** 稳定动作身份，可能与用于展示的名称不同。 */
  actionKey?: string
  deviceId: string
  status: NodePresentationStatus
  kind: 'actual' | 'running' | 'planned' | 'remaining'
  startMs: number
  endMs: number
  lane: number
  /** 开放区间只画到本次观察时间，右端不代表已完成。 */
  endIsOpen?: boolean
}

export interface GanttPendingNode {
  nodeUuid: string
  name: string
  reason: string
}

export interface GanttRow {
  task: WorkflowTask
  bars: GanttBar[]
  pending: GanttPendingNode[]
  laneCount: number
}

const terminalNodeStatuses = new Set(['succeeded', 'failed', 'canceled', 'skipped'])
const terminalTaskStatuses = new Set(['succeeded', 'failed', 'canceled', 'timeout'])
/** 时间轴和提示共用毫秒时间戳；跨日时调用方显示日期。 */
export function formatGanttTime(epochMs: number, includeDate = false): string {
  if (!validEpoch(epochMs)) return '时间待核对'
  return new Date(epochMs).toLocaleString('zh-CN', {
    ...(includeDate ? { month: '2-digit', day: '2-digit' } as const : {}),
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  })
}

export function formatGanttDuration(durationMs: number): string {
  if (!Number.isFinite(durationMs) || durationMs < 0) return '时长待定'
  if (durationMs === 0) return '0 秒'
  if (durationMs < 1000) return `${Math.round(durationMs)} 毫秒`
  const seconds = Math.round(durationMs / 1000)
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor(seconds % 3600 / 60)
  const rest = seconds % 60
  return [hours ? `${hours} 小时` : '', minutes ? `${minutes} 分` : '', rest ? `${rest} 秒` : ''].filter(Boolean).join(' ')
}

function validEpoch(value: number): boolean {
  return Number.isFinite(value) && Number.isFinite(new Date(value).getTime())
}

function timestamp(value: string | undefined): number | null {
  if (!value?.trim()) return null
  const parsed = Date.parse(value)
  return validEpoch(parsed) ? parsed : null
}

function plannedInterval(origin: number | undefined, node: PlannedNode | undefined): [number, number] | null {
  if (origin === undefined || !node || !Number.isFinite(origin)
    || !Number.isFinite(node.start_ms) || !Number.isFinite(node.end_ms)
    || node.start_ms < 0 || node.end_ms < node.start_ms) return null
  // 接口中的原点为 Unix 秒，区间偏移为毫秒，不可再次缩放偏移量。
  const start = origin * 1000 + node.start_ms
  const end = origin * 1000 + node.end_ms
  return validEpoch(start) && validEpoch(end) ? [start, end] : null
}

function defaultPendingReason(node: TaskNode, task: WorkflowTask): string {
  if (node.job?.manualConfirmation?.status === 'pending' || node.kind === 'manual_confirm') return '等待人工确认，后续时间待定'
  if (node.job?.uncertaintyReason || node.status === 'attention') return '实际执行状态需要核对，结束时间待定'
  if (node.waitReason?.message || node.waitReason?.title) return node.waitReason.message || node.waitReason.title
  if (node.kind === 'condition') return '等待条件结果，后续分支暂不确定'
  if (node.kind === 'repeat_until' || node.kind === 'loop') return '等待本轮循环结果，后续时间待定'
  if (node.status === 'skipped') return '已跳过，没有实际运行区间'
  if (terminalNodeStatuses.has(node.status)) return '节点已结束，未记录完整实际起止时间'
  if (terminalTaskStatuses.has(task.executionStatus || task.status)) return '任务已结束，此节点没有实际运行区间'
  if (task.dispatchGate === 'paused' || task.status === 'paused') return '任务派发已暂停'
  return '尚无可用时间安排，等待排程更新'
}

/** 判断时间区间是否相交，零时长事件保留原始时间，不伪造持续时间。 */
function overlaps(left: [number, number], right: [number, number]): boolean {
  if (left[0] === left[1]) return right[0] === right[1]
    ? left[0] === right[0] : left[0] >= right[0] && left[0] < right[1]
  if (right[0] === right[1]) return right[0] >= left[0] && right[0] < left[1]
  return left[0] < right[1] && right[0] < left[1]
}

function assignLanes(bars: GanttBar[]): number {
  const nodes = new Map<string, { bars: GanttBar[]; start: number; end: number; index: number }>()
  for (const bar of bars) {
    const node = nodes.get(bar.nodeUuid)
    if (node) {
      node.bars.push(bar)
      node.start = Math.min(node.start, bar.startMs)
      node.end = Math.max(node.end, bar.endMs)
    } else nodes.set(bar.nodeUuid, { bars: [bar], start: bar.startMs, end: bar.endMs, index: nodes.size })
  }
  const lanes: [number, number][][] = []
  // 同一节点的已运行部分与剩余预测共用一行；并行节点不会互相盖住。
  for (const node of [...nodes.values()].sort((left, right) => left.start - right.start || left.index - right.index)) {
    const interval: [number, number] = [node.start, node.end]
    let lane = lanes.findIndex((occupied) => occupied.every((other) => !overlaps(interval, other)))
    if (lane < 0) { lane = lanes.length; lanes.push([]) }
    lanes[lane].push(interval)
    for (const bar of node.bars) bar.lane = lane
  }
  return Math.max(1, lanes.length)
}

/**
 * 将任务事实与当前已验证候选合并，仅用于展示。调用方负责候选版本、连接状态校验。
 * 不修改输入对象，也不从缺失的时间或人工确认超时时间推断实际完成时间。
 */
export function buildTaskGantt(
  tasks: WorkflowTask[],
  candidate: TimeScheduleSnapshot['latest'],
  blocked: [string, string, string][],
  nowMs: number,
): GanttRow[] {
  const plans = new Map<string, Map<string, PlannedNode>>()
  for (const plan of candidate?.nodes || []) {
    if (!plans.has(plan.key[0])) plans.set(plan.key[0], new Map())
    plans.get(plan.key[0])!.set(plan.key[1], plan)
  }
  const reasons = new Map<string, Map<string, string[]>>()
  for (const [taskUuid, nodeUuid, reason] of blocked) {
    if (!reasons.has(taskUuid)) reasons.set(taskUuid, new Map())
    const taskReasons = reasons.get(taskUuid)!
    taskReasons.set(nodeUuid, [...(taskReasons.get(nodeUuid) || []), formatTimeScheduleReason(reason)])
  }

  return tasks.map((task): GanttRow => {
    const bars: GanttBar[] = []
    const pending: GanttPendingNode[] = []
    const taskPlans = plans.get(task.uuid)
    const taskReasons = reasons.get(task.uuid)
    const nodes = [...task.nodes]
    const knownNodeIds = new Set(nodes.map((node) => node.uuid))
    // 刚展开的循环节点可能先出现在候选中，保留其身份直至任务详情同步。
    for (const [uuid] of taskPlans || []) if (!knownNodeIds.has(uuid)) {
      nodes.push({ uuid, name: uuid, kind: 'action', index: nodes.length, status: 'pending' })
      knownNodeIds.add(uuid)
    }
    for (const node of nodes) {
      const plan = taskPlans?.get(node.uuid)
      const interval = plannedInterval(candidate?.time_origin, plan)
      const start = timestamp(node.job?.startedAt)
      const end = timestamp(node.job?.finishedAt)
      const nodeTerminal = terminalNodeStatuses.has(node.status)
        || terminalNodeStatuses.has(node.job?.executionStatus || '') || node.job?.executionStatus === 'timeout'
      // 正常结束的输入/输出是数据记账，不要求物理起止，也不伪装成待排设备动作。
      if (['workflow_input', 'workflow_output'].includes(node.kind)
        && ['succeeded', 'skipped'].includes(node.status) && start === null) continue
      const taskTerminal = terminalTaskStatuses.has(task.executionStatus || task.status)
      const beforeCount = bars.length
      const specificReasons = [...(taskReasons?.get(node.uuid) || [])]
      const requiresReview = node.status === 'attention' || Boolean(node.job?.uncertaintyReason)
        || node.job?.manualConfirmation?.status === 'pending'
      const deviceAction = node.kind === 'device_action' || Boolean(node.device) || Boolean(plan?.device_id)
      const addBar = (kind: GanttBar['kind'], startMs: number, endMs: number, endIsOpen = false): void => {
        bars.push({
          id: `${task.uuid}:${node.uuid}:${node.job?.uuid || 'plan'}:${kind}`,
          taskUuid: task.uuid, nodeUuid: node.uuid, name: node.name,
          ...(deviceAction ? { actionKey: node.actionName || node.name } : {}),
          deviceId: kind === 'planned' ? plan?.device_id || node.device || '' : node.device || plan?.device_id || '', status: node.status,
          kind, startMs, endMs, lane: 0, ...(endIsOpen ? { endIsOpen: true } : {}),
        })
      }

      if (start !== null && end !== null && end >= start) {
        // 有真实完成回报时优先显示事实，即使候选接口尚未刷新。
        addBar('actual', start, end)
        // 不确定结果即使有完整时间戳也必须保留待核对提示，避免实际条掩盖
        // 尚未确认的物理结果或人工确认请求。
        if (requiresReview) specificReasons.push(defaultPendingReason(node, task))
        else specificReasons.length = 0
      } else if (nodeTerminal || taskTerminal) {
        if (start !== null || end !== null) specificReasons.push('已结束，但实际起止时间不完整或顺序异常')
      } else {
        if (start !== null && validEpoch(nowMs) && start <= nowMs && end === null) {
          addBar('running', start, nowMs, true)
        } else if (node.job?.startedAt || node.job?.finishedAt) {
          specificReasons.push('实际起止时间缺失或异常，等待核对')
        }
        // 待核对和人工等待不能用旧预测伪装成确定的结束时间。
        if (!requiresReview && interval && plan) {
          if (plan.fixed || start !== null || node.status === 'running' || node.status === 'canceling') {
            const remainingStart = Math.max(interval[0], validEpoch(nowMs) ? nowMs : interval[0], start ?? interval[0])
            if (interval[1] >= remainingStart) addBar('remaining', remainingStart, interval[1])
            else specificReasons.push('已超过预计完成时间，等待实际回报')
            if (start === null) specificReasons.push('实际开始时间尚未回报')
          } else addBar('planned', interval[0], interval[1])
        }
        if (requiresReview) specificReasons.push(defaultPendingReason(node, task))
        if (bars.some((bar) => bar.nodeUuid === node.uuid && bar.endIsOpen)
          && !bars.some((bar) => bar.nodeUuid === node.uuid && bar.kind === 'remaining')
          && !specificReasons.length) specificReasons.push('实际结束时间待定，暂无剩余耗时预测')
        if (start === null && ['running', 'canceling'].includes(node.status)
          && !specificReasons.length) specificReasons.push('实际开始时间尚未回报')
      }

      if (bars.length === beforeCount || specificReasons.length) {
        pending.push({ nodeUuid: node.uuid, name: node.name, reason: [...new Set(specificReasons)].join('；') || defaultPendingReason(node, task) })
      }
    }
    for (const [nodeUuid, nodeReasons] of taskReasons || []) if (!knownNodeIds.has(nodeUuid)) {
      pending.push({ nodeUuid, name: nodeUuid || '任务', reason: [...new Set(nodeReasons)].join('；') })
    }
    const laneCount = assignLanes(bars)
    // 时间轴以预计/实际开始时间为主排序；同一时刻保持节点原始顺序，
    // 这样后续待运行节点会按排程顺序显示，而不是被 DAG 插入顺序打散。
    bars.sort((left, right) => left.startMs - right.startMs
      || left.endMs - right.endMs || left.lane - right.lane || left.nodeUuid.localeCompare(right.nodeUuid))
    pending.sort((left, right) => {
      const leftPlan = taskPlans?.get(left.nodeUuid)
      const rightPlan = taskPlans?.get(right.nodeUuid)
      const leftStart = leftPlan?.start_ms ?? Number.POSITIVE_INFINITY
      const rightStart = rightPlan?.start_ms ?? Number.POSITIVE_INFINITY
      return leftStart - rightStart || left.nodeUuid.localeCompare(right.nodeUuid)
    })
    return { task, bars, pending, laneCount }
  })
}
