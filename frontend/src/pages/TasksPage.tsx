import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { LucideIcon } from 'lucide-react'
import {
  Activity,
  AlertCircle,
  Check,
  ChevronRight,
  ChartGantt,
  Circle,
  Clock3,
  ExternalLink,
  FlaskConical,
  LoaderCircle,
  Lock,
  LayoutList,
  Plus,
  Pause,
  Play,
  RefreshCw,
  Send,
  ShieldCheck,
  ShieldAlert,
  Square,
  StepForward,
  Trash2,
  X,
} from 'lucide-react'
import {
  commandWorkflowTask,
  createWorkflowTask,
  deleteWorkflowTask,
  decideManualConfirmation,
  decideWorkflowIntervention,
  forceReleaseWorkflowTaskExecutionLock,
  loadFailedMaterialTransferSettlementContext,
  loadWorkflowGraph,
  loadWorkflowInterventions,
  loadWorkflowTaskDetail,
  loadWorkflowTaskDeleteEligibility,
  loadWorkflowTaskExecutionLocks,
  loadWorkflowTaskStepState,
  settleFailedMaterialTransfer,
  updateWorkflowTaskJobParam,
  TaskCommandRequestError,
} from '../lib/edgeClient'
import { DEFAULT_RUNTIME_CAPABILITIES } from '../types'
import type {
  ContractField,
  FailedMaterialTransferSettlementContext,
  MaterialRecord,
  TaskNode,
  WorkflowDefinition,
  WorkflowIntervention,
  WorkflowTarget,
  WorkflowTask,
  WorkflowTaskExecutionLock,
  RuntimeCapabilities,
} from '../types'
import { sourceSiteOptions } from '../lib/sourceSiteOptions'
import { taskColor } from '../lib/resourceUsage'
import { Button, EmptyState, PageHeader, Panel, PanelHeader, StatusBadge } from '../components/ui'
import { HistoricalManualActionReview } from '../components/HistoricalManualActionReview'
import { TaskResourceUnlock } from '../components/TaskResourceUnlock'
import { TimeSchedulePanel } from '../components/TimeSchedulePanel'
import { TaskGantt } from '../components/TaskGantt'
import { HumanInteractionPanel } from '../components/HumanInteractionPanel'

type TaskFilter = 'all' | 'running' | 'waiting' | 'failed' | 'succeeded'

type TaskCancelIntentState = 'pending' | 'accepted' | 'unknown'

interface TaskCancelIntent {
  taskUuid: string
  idempotencyKey: string
  state: TaskCancelIntentState
}

const taskCancelIntentStorageKey = 'unilab:task-cancel-intents'

function readTaskCancelIntents(): Record<string, TaskCancelIntent> {
  if (typeof window === 'undefined') return {}
  try {
    const raw = window.sessionStorage.getItem(taskCancelIntentStorageKey)
    const parsed = raw ? JSON.parse(raw) as Record<string, TaskCancelIntent> : {}
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function storeTaskCancelIntents(intents: Record<string, TaskCancelIntent>) {
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.setItem(taskCancelIntentStorageKey, JSON.stringify(intents))
  } catch {
    // sessionStorage 不可用时仍保留当前页状态。
  }
}

function taskCommandUuid(prefix: string) {
  return globalThis.crypto?.randomUUID?.()
    || `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

const TASK_IDENTITY_COLUMN_WIDTH = 300
const TASK_NODE_COLUMN_WIDTH = 160
const TASK_PROGRESS_COLUMN_WIDTH = 86

const nodeStatusLabels: Record<TaskNode['status'], string> = {
  succeeded: '已完成',
  running: '正在运行',
  waiting: '等待资源',
  failed: '失败',
  pending: '待运行',
  skipped: '已跳过',
  canceling: '取消中',
  canceled: '已取消',
  attention: '需要处理',
}

function nodeStatusLabel(node?: TaskNode) {
  if (!node) return nodeStatusLabels.pending
  if (node.job?.manualConfirmation?.status === 'pending') {
    const mode = materialIoMode(node)
    if (mode === 'load') return '等待确认上料'
    if (mode === 'unload') return '等待确认下料'
    if (mode === 'refill') return '等待确认补液'
    return '需要人工确认'
  }
  if (node.status === 'attention' && node.job?.uncertaintyReason && !node.job.resultReviewed) return '结果待核对'
  const label = nodeStatusLabels[node.status]
  return node.job?.resultReviewed ? `${label} · 已核对` : label
}

function materialIoMode(node?: TaskNode): 'load' | 'unload' | 'refill' | undefined {
  if (String(node?.kind || '').toLowerCase() !== 'material_io') return undefined
  const param = node?.job?.param
  if (!param || typeof param !== 'object') return undefined
  const mode = String((param as Record<string, unknown>).mode || '').toLowerCase()
  return mode === 'load' || mode === 'unload' || mode === 'refill' ? mode : undefined
}

const taskPriorityLabels = {
  urgent: '紧急优先级',
  high: '高优先级',
  normal: '普通优先级',
  low: '低优先级',
  unknown: '优先级未知',
} as const

function presentTaskPriority(priority: WorkflowTask['priority']) {
  if (typeof priority === 'number') return { label: `权重 ${priority}`, tone: 'custom' }
  return { label: taskPriorityLabels[priority], tone: priority }
}

function matchesFilter(task: WorkflowTask, filter: TaskFilter) {
  if (filter === 'all') return true
  if (filter === 'running') return task.status === 'running' || task.status === 'canceling'
  if (filter === 'waiting') return ['admission_blocked', 'paused'].includes(task.status)
  if (filter === 'failed') return ['failed', 'timeout', 'intervention_required', 'execution_unknown', 'unknown'].includes(task.status)
  return task.status === 'succeeded'
}

function NodeMarker({
  node,
  index,
  selected,
  ready,
  writable,
  onSelect,
  onNotify,
}: {
  node?: TaskNode
  index: number
  selected: boolean
  ready?: boolean
  writable: boolean
  onSelect: () => void
  onNotify: (message: string) => void
}) {
  const status = node?.status || 'pending'
  const waitReason = node?.waitReason
  const markerRef = useRef<HTMLButtonElement>(null)
  const tooltipId = useId()
  const [tooltipVisible, setTooltipVisible] = useState(false)
  const [tooltipPosition, setTooltipPosition] = useState({ left: 0, top: 0, above: false })
  const [now, setNow] = useState(() => Date.now())
  const queryClient = useQueryClient()
  const confirmation = node?.job?.manualConfirmation
  const awaitingConfirmation = confirmation?.status === 'pending'
  const materialOperationMode = materialIoMode(node)
  const materialOperationInstruction = materialOperationMode === 'load'
    ? '请确认新物料已实际放到目标库位；批准后系统才会按模板创建容器及默认内容。'
    : materialOperationMode === 'unload'
      ? '请确认目标物料已从现场取走；批准后系统才会解除占位或软删除物料及内容。'
      : materialOperationMode === 'refill'
        ? '请确认已向当前容器补液；批准后系统才会增加试剂余量，不改库位占用。'
        : undefined
  const decision = useMutation({
    mutationFn: (action: 'approve' | 'reject') => {
      if (!writable) throw new Error('Edge 未连接，写操作已暂停')
      return decideManualConfirmation(node?.job?.uuid || '', action)
    },
    onSuccess: (_result, action) => {
      void queryClient.invalidateQueries({ queryKey: ['edge-tasks'] }, { cancelRefetch: false })
      onNotify(action === 'approve'
        ? materialOperationMode === 'load'
          ? '已确认现场上料，系统正在按模板建账。'
          : materialOperationMode === 'unload'
            ? '已确认现场下料，系统正在更新物料记录。'
            : materialOperationMode === 'refill'
              ? '已确认现场补液，系统正在更新余量。'
              : '人工确认已批准，设备动作将继续执行。'
        : '人工确认已拒绝，任务正在取消。')
    },
    onError: (error) => onNotify(error instanceof Error ? error.message : '人工确认提交失败'),
  })
  useEffect(() => {
    if (!awaitingConfirmation) return undefined
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [awaitingConfirmation])
  const showTooltip = useCallback(() => {
    if (!markerRef.current) return
    const rect = markerRef.current.getBoundingClientRect()
    const above = rect.top > 190
    const halfWidth = 142
    setTooltipPosition({
      left: Math.min(Math.max(rect.left + rect.width / 2, halfWidth + 10), window.innerWidth - halfWidth - 10),
      top: above ? rect.top - 10 : rect.bottom + 10,
      above,
    })
    setTooltipVisible(true)
  }, [])

  useEffect(() => {
    if (!tooltipVisible) return undefined
    const hideTooltip = () => setTooltipVisible(false)
    document.addEventListener('scroll', hideTooltip, true)
    window.addEventListener('resize', hideTooltip)
    return () => {
      document.removeEventListener('scroll', hideTooltip, true)
      window.removeEventListener('resize', hideTooltip)
    }
  }, [tooltipVisible])

  const statusLabel = nodeStatusLabel(node)
  const label = `${node?.name || `节点 ${index + 1}`}，${statusLabel}`
  const remainingSeconds = awaitingConfirmation && confirmation.deadlineAt
    ? Math.max(0, Math.ceil((new Date(confirmation.deadlineAt).getTime() - now) / 1000))
    : 0
  return (
    <div
      className={`matrix-node matrix-node-${status} ${awaitingConfirmation ? 'matrix-node-manual-confirmation' : ''} ${selected ? 'matrix-node-selected' : ''} ${ready ? 'matrix-node-step-ready' : ''}`}
    >
      <button
        type="button"
        ref={markerRef}
        className="matrix-node-select"
        aria-label={label}
        aria-pressed={selected}
        aria-describedby={tooltipVisible ? tooltipId : undefined}
        onClick={(event) => {
          event.stopPropagation()
          onSelect()
        }}
        onMouseEnter={showTooltip}
        onMouseLeave={() => setTooltipVisible(false)}
        onFocus={showTooltip}
        onBlur={() => setTooltipVisible(false)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') setTooltipVisible(false)
        }}
      >
        <span className="matrix-node-marker">
          {status === 'succeeded' || status === 'skipped'
            ? <Check size={13} />
            : status === 'running' || status === 'canceling'
              ? <LoaderCircle size={13} />
              : status === 'failed' || status === 'canceled'
                ? <X size={13} />
              : status === 'attention' && awaitingConfirmation
                  ? <ShieldAlert size={13} />
                  : status === 'attention'
                    ? <X size={13} />
                    : index + 1}
        </span>
        <small className="matrix-node-meta">{String(index + 1).padStart(2, '0')} · {statusLabel}</small>
        <strong className="matrix-node-title">{node?.name || `节点 ${index + 1}`}</strong>
      </button>
      {awaitingConfirmation ? (
        <div className="manual-confirmation-actions" onClick={(event) => event.stopPropagation()}>
          <small>{materialOperationInstruction || `剩余 ${remainingSeconds}s`}</small>
          <span>
            <button type="button" disabled={!writable || decision.isPending} onClick={() => decision.mutate('reject')}>拒绝</button>
            <button type="button" disabled={!writable || decision.isPending} onClick={() => decision.mutate('approve')}>
              {materialOperationMode === 'load' ? '确认已上料并建账' : materialOperationMode === 'unload' ? '确认已下料并更新' : materialOperationMode === 'refill' ? '确认已补液并更新余量' : '批准'}
            </button>
          </span>
        </div>
      ) : null}
      {tooltipVisible && typeof document !== 'undefined' && createPortal(
        <div
          id={tooltipId}
          role="tooltip"
          className={`node-wait-tooltip ${tooltipPosition.above ? 'node-wait-tooltip-above' : 'node-wait-tooltip-below'}`}
          style={{ left: tooltipPosition.left, top: tooltipPosition.top }}
        >
          <strong>{node?.name || `节点 ${index + 1}`}</strong>
          <p>{statusLabel}</p>
          {waitReason ? (
            <>
              <em>{waitReason.title}</em>
              <p>{waitReason.message}</p>
              {waitReason.details.length > 0 && (
                <ul>{waitReason.details.map((detail) => <li key={detail}>{detail}</li>)}</ul>
              )}
              {waitReason.waitingSince && <small>等待开始：{waitReason.waitingSince}</small>}
            </>
          ) : null}
        </div>,
        document.body,
      )}
    </div>
  )
}

function TaskMatrix({
  tasks,
  selectedId,
  selectedNode,
  onSelect,
  onSelectNode,
  onOpenWorkflow,
  onNotify,
  writable,
  onTaskDeleted,
  readyNodeUuids = new Set<string>(),
  selectedTaskControl,
  onViewResources,
}: {
  tasks: WorkflowTask[]
  selectedId: string
  selectedNode?: { taskUuid: string; nodeUuid: string }
  onSelect: (id: string) => void
  onSelectNode: (taskUuid: string, nodeUuid: string) => void
  onOpenWorkflow: (target: WorkflowTarget) => void
  onNotify: (message: string) => void
  writable: boolean
  onTaskDeleted: (taskUuid: string) => void
  readyNodeUuids?: ReadonlySet<string>
  selectedTaskControl?: ReactNode
  onViewResources?: (taskUuid: string) => void
}) {
  const maxNodeCount = Math.max(1, ...tasks.map((task) => task.nodes.length))
  const matrixWidth = TASK_IDENTITY_COLUMN_WIDTH
    + maxNodeCount * TASK_NODE_COLUMN_WIDTH
    + TASK_PROGRESS_COLUMN_WIDTH

  return (
    <div className="task-matrix-scroll">
      <div className="matrix-body" style={{ minWidth: matrixWidth }}>
        {tasks.map((task) => {
          const nodes: (TaskNode | undefined)[] = task.nodes.length ? task.nodes : [undefined]
          const columns = `${TASK_IDENTITY_COLUMN_WIDTH}px repeat(${nodes.length}, ${TASK_NODE_COLUMN_WIDTH}px) ${TASK_PROGRESS_COLUMN_WIDTH}px minmax(0, 1fr)`
          const priority = presentTaskPriority(task.priority)
          return (
            <div
              key={task.uuid}
              className={`matrix-row ${selectedId === task.uuid ? 'selected' : ''}`}
              style={{ gridTemplateColumns: columns }}
              onClick={() => onSelect(task.uuid)}
            >
              <div className={`matrix-task-cell ${selectedId === task.uuid && selectedTaskControl ? 'matrix-task-cell-with-control' : ''}`} style={{ borderLeft: `4px solid ${taskColor(task.uuid)}` }}>
                <button
                  type="button"
                  className="matrix-task-select"
                  aria-label={`打开工作流 ${task.workflowName}，Task ${task.uuid}`}
                  onClick={(event) => {
                    event.stopPropagation()
                    onOpenWorkflow({
                      workflowUuid: task.workflowUuid,
                      revision: task.workflowRevision,
                      taskUuid: task.uuid,
                    })
                  }}
                >
                  <span className={`task-state-dot task-state-${task.status}`} />
                  <span>
                    <strong>{task.workflowName}</strong>
                    <small>{task.uuid}</small>
                    <small>{task.sample} · {task.updatedAt}</small>
                  </span>
                  <span className="matrix-task-badges">
                    <span
                      className={`matrix-task-priority matrix-task-priority-${priority.tone}`}
                      title={`任务优先级：${priority.label}`}
                    >
                      {priority.label}
                    </span>
                    <em className="matrix-task-revision">{task.workflowRevision ? `r${task.workflowRevision}` : '—'}</em>
                  </span>
                </button>
                {onViewResources ? <button type="button" className="matrix-resource-link" onClick={(event) => { event.stopPropagation(); onViewResources(task.uuid) }}>占用资源</button> : null}
                {task.trace ? (
                  <a
                    className="matrix-trace-link"
                    href={task.trace.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={`在 SigNoz 中查看 ${task.uuid} 的 Trace`}
                    onClick={(event) => event.stopPropagation()}
                  >
                    <ExternalLink size={12} />Trace
                  </a>
                ) : (
                  <button type="button" className="matrix-trace-disabled" disabled title="Trace 服务未配置">Trace</button>
                )}
                <TaskDeleteAction
                  task={task}
                  connected={writable}
                  compact
                  onNotify={onNotify}
                  onDeleted={() => onTaskDeleted(task.uuid)}
                />
                {selectedId === task.uuid && selectedTaskControl ? (
                  <div
                    className="matrix-task-inline-control"
                    onClick={(event) => event.stopPropagation()}
                  >
                    {selectedTaskControl}
                  </div>
                ) : null}
              </div>
              {nodes.map((node, index) => (
                <NodeMarker
                  key={node?.uuid || `empty-${task.uuid}`}
                  node={node}
                  index={index}
                  selected={Boolean(node && selectedNode?.taskUuid === task.uuid && selectedNode.nodeUuid === node.uuid)}
                  ready={Boolean(
                    node
                    && task.uuid === selectedId
                    && readyNodeUuids.has(node.uuid)
                  )}
                  writable={writable}
                  onSelect={() => {
                    if (node) onSelectNode(task.uuid, node.uuid)
                  }}
                  onNotify={onNotify}
                />
              ))}
              <div className="matrix-progress-cell"><strong>{task.progress}%</strong><span><i style={{ width: `${task.progress}%` }} /></span></div>
              <div className="matrix-row-tail" aria-hidden="true" />
            </div>
          )
        })}
      </div>
    </div>
  )
}

function TaskCancelDialog({
  task,
  existingIntent,
  connected,
  pending,
  onClose,
  onConfirm,
}: {
  task: WorkflowTask
  existingIntent?: TaskCancelIntent
  connected: boolean
  pending: boolean
  onClose: () => void
  onConfirm: () => void
}) {
  const activeNodes = task.nodes.filter((node) => node.status === 'running' || node.status === 'canceling')
  const hasInFlight = activeNodes.length > 0
  const [physicalAcknowledged, setPhysicalAcknowledged] = useState(!hasInFlight)

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section className="task-dialog task-cancel-dialog" role="dialog" aria-modal="true" aria-labelledby="task-cancel-title" aria-describedby="task-cancel-description">
        <form onSubmit={(event) => { event.preventDefault(); if (!hasInFlight || physicalAcknowledged) onConfirm() }}>
          <header>
            <div>
              <span>TASK CANCELLATION</span>
              <h2 id="task-cancel-title">{hasInFlight ? '停止后续调度并请求取消' : '取消任务'}</h2>
              <p id="task-cancel-description">Task {task.uuid}</p>
            </div>
            <button type="button" aria-label="关闭任务取消确认" onClick={onClose}><X size={18} /></button>
          </header>
          <div className="dialog-content task-cancel-content">
            {hasInFlight ? (
              <div className="task-cancel-warning" role="alert">
                <ShieldAlert size={18} />
                <div>
                  <strong>当前有 {activeNodes.length} 个设备动作已经开始</strong>
                  <p>{activeNodes.slice(0, 2).map((node) => node.name).join('、')}。本操作会阻止后续节点，但设备动作是否停止取决于设备驱动；在收到明确终态前，执行锁会继续保留。</p>
                </div>
              </div>
            ) : (
              <div className="task-cancel-safe-note"><Check size={16} /><span>当前没有已开始的设备动作，未派发节点可以直接取消。</span></div>
            )}
            {existingIntent ? (
              <div className="task-cancel-intent-note">
                <Clock3 size={15} />
                <span>{existingIntent.state === 'unknown' ? '上一次取消请求结果未知。确认后会复用同一个请求编号，不会重复创建物理动作。' : '取消请求已经提交，确认后会复用同一个请求编号查询/重放。'}</span>
              </div>
            ) : null}
            {hasInFlight ? (
              <label className="task-cancel-confirmation">
                <input type="checkbox" checked={physicalAcknowledged} onChange={(event) => setPhysicalAcknowledged(event.target.checked)} />
                <span>我知道当前设备动作可能继续运行，会按现场安全流程确认设备状态。</span>
              </label>
            ) : null}
          </div>
          <footer>
            <Button type="button" onClick={onClose} disabled={pending}>返回</Button>
            <Button type="submit" tone="danger" icon={pending ? <LoaderCircle className="spin" size={14} /> : <Square size={14} />} disabled={!connected || pending || (hasInFlight && !physicalAcknowledged)}>
              {pending ? '正在提交' : existingIntent?.state === 'unknown' ? '确认原取消请求' : hasInFlight ? '停止后续调度并提交取消' : '确认取消任务'}
            </Button>
          </footer>
        </form>
      </section>
    </div>
  )
}

function jsonEvidence(value: unknown, emptyLabel: string) {
  if (value === undefined || value === null) return emptyLabel
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function ErrorPolicyDialog({ intervention, connected, onNotify }: {
  intervention: WorkflowIntervention
  connected: boolean
  onNotify: (message: string) => void
}) {
  const queryClient = useQueryClient()
  const [now, setNow] = useState(() => Date.now())
  const timeout = Number(intervention.metaData.decision_timeout_seconds || 300)
  const defaultAction = String(intervention.metaData.default_on_decision_timeout || 'abort')
  const defaultActionLabel = { retry: '重试', skip: '跳过', abort: '终止' }[defaultAction] || '终止'
  const actionName = String(intervention.metaData.action_name || '')
  const exceptionType = String(intervention.metaData.exception_type || '')
  const errorMessage = String(intervention.metaData.error_message || '')
  const deadline = new Date(intervention.openedAt).getTime() + timeout * 1000
  const remaining = Math.max(0, Math.ceil((deadline - now) / 1000))
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  const mutation = useMutation({
    mutationFn: (optionId: string) => decideWorkflowIntervention(intervention, optionId),
    onSuccess: () => {
      onNotify('错误处理决定已发送到设备。')
      void queryClient.invalidateQueries({ queryKey: ['workflow-interventions'] })
      void queryClient.invalidateQueries({ queryKey: ['edge-tasks'] })
    },
    onError: (error) => onNotify(`提交错误处理决定失败：${error instanceof Error ? error.message : '未知错误'}`),
  })
  return (
    <div className="dialog-backdrop" role="presentation">
      <section className="task-dialog error-policy-dialog" role="dialog" aria-modal="true" aria-labelledby="error-policy-title">
        <form onSubmit={(event) => event.preventDefault()}>
          <header>
            <div>
              <span>ACTION ERROR</span>
              <h2 id="error-policy-title">设备动作需要处理</h2>
              <p>该节点已暂停。请选择下一步操作；剩余 {remaining}s 后将按默认策略{defaultActionLabel}。</p>
            </div>
            <span className="error-policy-icon"><ShieldAlert size={20} /></span>
          </header>
          <div className="dialog-content error-policy-content">
            {(actionName || exceptionType || errorMessage) ? <div className="error-policy-summary">
              <strong>{actionName || '设备动作'}{exceptionType ? ` · ${exceptionType}` : ''}</strong>
              {errorMessage ? <span>{errorMessage}</span> : null}
            </div> : null}
            <p>任务 <code>{intervention.workflowTaskUuid.slice(0, 8)}</code> · 节点作业 <code>{intervention.workflowNodeJobUuid.slice(0, 8)}</code></p>
            <div className="error-policy-options">
              {intervention.options.map((option) => <Button key={option.id} tone={option.action === 'abort' ? 'danger' : option.action === 'retry' ? 'primary' : undefined} disabled={!connected || mutation.isPending} onClick={() => mutation.mutate(option.id)}>{option.label}{option.description ? `：${option.description}` : ''}</Button>)}
            </div>
          </div>
        </form>
      </section>
    </div>
  )
}

function TaskNodeInspector({ task, node, connected, onNotify, onClose }: { task: WorkflowTask; node: TaskNode; connected: boolean; onNotify: (message: string) => void; onClose: () => void }) {
  const queryClient = useQueryClient()
  const editable = task.dispatchGate === 'paused' && Boolean(node.job?.uuid) && ['pending', 'waiting'].includes(node.status)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(() => JSON.stringify(node.job?.param || {}, null, 2))
  const mutation = useMutation({
    mutationFn: () => {
      let parsed: unknown
      try { parsed = JSON.parse(draft) } catch { throw new Error('参数必须是有效 JSON') }
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('参数必须是 JSON 对象')
      return updateWorkflowTaskJobParam(task.uuid, node.job!.uuid, parsed as Record<string, unknown>)
    },
    onSuccess: () => {
      onNotify('Job 参数已保存；继续任务后会按新参数派发')
      setEditing(false)
      void queryClient.invalidateQueries({ queryKey: ['edge-tasks'] }, { cancelRefetch: false })
      void queryClient.invalidateQueries({ queryKey: ['workflow-task-detail', task.uuid] }, { cancelRefetch: false })
      void queryClient.invalidateQueries({ queryKey: ['time-schedule-plan'] }, { cancelRefetch: false })
    },
    onError: (error) => onNotify(`参数保存失败：${error instanceof Error ? error.message : '未知错误'}`),
  })
  return (
    <section className="panel task-node-inspector" role="region" aria-label="节点运行详情">
      <header>
        <div>
          <span>NODE EXECUTION</span>
          <h2>{node.name}</h2>
          <p>{task.uuid} · {node.job?.uuid || '尚未创建 Job'} · 第 {node.job?.attempt || 1} 次尝试</p>
        </div>
        <div className="node-inspector-status">
          <span className={`task-state-dot task-state-${task.status}`} />
          <strong>{nodeStatusLabel(node)}</strong>
          <button type="button" aria-label="关闭节点运行详情" onClick={onClose}><X size={16} /></button>
        </div>
      </header>
      <div className="node-evidence-grid">
        <article>
          <div className="node-evidence-heading"><span><strong>实际运行参数</strong><small>WorkflowNodeJob.param</small></span>{editable && !editing ? <Button onClick={() => { setDraft(JSON.stringify(node.job?.param || {}, null, 2)); setEditing(true) }}>修改参数</Button> : null}</div>
          {editing ? (
            <div className="node-param-editor">
              <textarea aria-label="Job 参数 JSON" value={draft} onChange={(event) => setDraft(event.target.value)} />
              <div><Button type="button" onClick={() => setEditing(false)}>取消</Button><Button type="button" tone="primary" disabled={!connected || mutation.isPending} onClick={() => mutation.mutate()}>保存</Button></div>
              <small>仅暂停任务且 Job 尚未下发时可编辑；拓扑和已下发 Job 不会改变。</small>
            </div>
          ) : <pre>{jsonEvidence(node.job?.param, '节点尚未进入调度，暂无实际参数')}</pre>}
        </article>
        <article>
          <div><strong>运行结果</strong><small>WorkflowNodeJob.return_info</small></div>
          <pre>{jsonEvidence(node.job?.returnInfo, '节点尚未返回运行结果')}</pre>
        </article>
        <article>
          <div><strong>实时反馈</strong><small>feedback_data</small></div>
          <pre>{jsonEvidence(node.job?.feedbackData, '暂无反馈数据')}</pre>
        </article>
        <article>
          <div><strong>错误信息</strong><small>{node.job?.startedAt || '未开始'} → {node.job?.finishedAt || '未结束'}</small></div>
          <pre>{jsonEvidence(node.job?.errorInfo, '暂无错误')}</pre>
        </article>
      </div>
      {(node.manualReviewJobs || (node.job?.executionSource === 'error_manual' && (node.job.uncertaintyReason || node.job.manualReview) ? [node.job] : [])).map((job) => (
        <HistoricalManualActionReview key={job.uuid} job={job} taskUuid={task.uuid} taskNeedsAttention={['execution_unknown', 'intervention_required', 'unknown'].includes(task.status)} connected={connected} onNotify={onNotify} />
      ))}
    </section>
  )
}

/** 任务删除资格中由摘要状态即可判断的终态闭集，与 Edge 的终态门禁保持一致。 */
const taskDeleteTerminalStatuses = new Set(['succeeded', 'failed', 'canceled', 'timeout'])

function TaskDeleteAction({
  task,
  connected,
  compact = false,
  onNotify,
  onDeleted,
}: {
  task: WorkflowTask
  connected: boolean
  compact?: boolean
  onNotify: (message: string) => void
  onDeleted: () => void
}) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  // 任务列表只返回摘要，没有 can_delete；终态判断可以从摘要得出，因此用它决定
  // 按钮能否点开，真正的资格在对话框打开后按任务查询。
  const terminal = taskDeleteTerminalStatuses.has(task.executionStatus || task.status)
  const eligibilityQuery = useQuery({
    queryKey: ['workflow-task-delete-eligibility', task.uuid],
    queryFn: ({ signal }) => loadWorkflowTaskDeleteEligibility(task.uuid, signal),
    enabled: open && connected,
    staleTime: 0,
    gcTime: 0,
  })
  const eligibility = eligibilityQuery.data
  const mutation = useMutation({
    mutationFn: () => deleteWorkflowTask(task.uuid),
    onSuccess: (result) => {
      setOpen(false)
      onNotify(result.status === 'already_deleted' ? '任务已经删除，页面已同步。' : '任务已删除。')
      onDeleted()
      void queryClient.invalidateQueries({ queryKey: ['edge-tasks'] }, { cancelRefetch: false })
      void queryClient.invalidateQueries({ queryKey: ['workflow-task-detail', task.uuid] }, { cancelRefetch: false })
      void queryClient.invalidateQueries({ queryKey: ['time-schedule-plan'] }, { cancelRefetch: false })
      void queryClient.invalidateQueries({ queryKey: ['edge-snapshot'] }, { cancelRefetch: false })
    },
    onError: (error) => {
      onNotify(`删除任务失败：${error instanceof Error ? error.message : '未知错误'}`)
      // 删除被服务端门禁拒绝时立刻重取资格，把最新阻塞原因展示在对话框里。
      void eligibilityQuery.refetch()
      void queryClient.invalidateQueries({ queryKey: ['edge-tasks'] }, { cancelRefetch: false })
      void queryClient.invalidateQueries({ queryKey: ['workflow-task-detail', task.uuid] }, { cancelRefetch: false })
    },
  })
  const summaryBlockers = task.deleteBlockers?.length ? task.deleteBlockers : []
  const dialogBlockers = eligibility?.blockers?.length
    ? eligibility.blockers
    : summaryBlockers
  const buttonTitle = terminal
    ? '删除任务'
    : `任务状态为 ${task.executionStatus || task.status}，尚未进入终态，无法删除`
  const confirmable = Boolean(
    connected
    && eligibility
    && (eligibility.canDelete || eligibility.alreadyDeleted)
    && !mutation.isPending,
  )
  // 矩阵行里的删除按钮位于 position: sticky 的任务单元内，该单元自成层叠上下文，
  // 对话框留在原地会被行内其他层遮住，也会把点击冒泡成“选中任务”。
  const dialog = open ? (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false) }}>
      <section className="task-delete-dialog" role="dialog" aria-modal="true" aria-labelledby="task-delete-title">
        <header>
          <div><span>OPERATOR ACTION</span><h2 id="task-delete-title">删除任务</h2><p>{task.workflowName} · {task.uuid}</p></div>
          <button type="button" aria-label="关闭删除任务对话框" onClick={() => setOpen(false)}><X size={18} /></button>
        </header>
        <div className="task-delete-dialog-content">
          {eligibilityQuery.isPending ? (
            <div className="task-delete-warning"><LoaderCircle className="spin" size={16} /><span>正在核验服务端删除资格…</span></div>
          ) : eligibilityQuery.isError ? (
            <div className="task-delete-warning"><AlertCircle size={16} /><span>无法读取删除资格：{eligibilityQuery.error instanceof Error ? eligibilityQuery.error.message : '未知错误'}</span></div>
          ) : dialogBlockers.length ? (
            <div className="task-delete-warning"><AlertCircle size={16} /><span>该任务暂时不能删除：{dialogBlockers.join('；')}</span></div>
          ) : (
            <div className="task-delete-warning"><AlertCircle size={16} /><span>该任务已进入终态且资源已释放。删除只隐藏 Edge 任务记录，运行事件、Job、库存和工作流定义会保留。</span></div>
          )}
          <dl className="task-delete-facts">
            <div><dt>任务状态</dt><dd>{eligibility?.status || task.executionStatus || task.status}</dd></div>
            <div><dt>清理状态</dt><dd>{eligibility?.cleanupStatus || task.cleanupStatus || 'none'}</dd></div>
            <div><dt>资源检查</dt><dd>{eligibilityQuery.isPending ? '核验中' : dialogBlockers.length ? `${dialogBlockers.length} 项资源事实未收敛` : '无活动锁、Claim、托管或待处理命令'}</dd></div>
          </dl>
        </div>
        <footer>
          <Button type="button" onClick={() => setOpen(false)}>取消</Button>
          {dialogBlockers.length && !eligibilityQuery.isPending ? (
            <Button type="button" icon={<RefreshCw size={14} />} disabled={eligibilityQuery.isFetching} onClick={() => { void eligibilityQuery.refetch() }}>重新核验</Button>
          ) : null}
          <Button
            type="button"
            tone="danger"
            icon={mutation.isPending ? <LoaderCircle className="spin" size={14} /> : <Trash2 size={14} />}
            disabled={!confirmable}
            title={confirmable ? '确认删除' : dialogBlockers.join('；') || '正在核验服务端删除资格'}
            onClick={() => mutation.mutate()}
          >
            确认删除
          </Button>
        </footer>
      </section>
    </div>
  ) : null
  return (
    <div className={`task-delete-action${compact ? ' task-delete-action-compact' : ''}`}>
      <div className="task-delete-action-row">
        <Button
          tone="danger"
          icon={mutation.isPending ? <LoaderCircle className="spin" size={14} /> : <Trash2 size={14} />}
          disabled={!connected || !terminal || mutation.isPending}
          title={buttonTitle}
          onClick={(event) => { event.stopPropagation(); mutation.reset(); setOpen(true) }}
        >
          删除任务
        </Button>
        {!terminal ? <small className="task-delete-blocked">{buttonTitle}</small> : null}
      </div>
      {dialog ? (typeof document !== 'undefined' ? createPortal(dialog, document.body) : dialog) : null}
    </div>
  )
}

const executionLockStateLabels: Record<string, string> = {
  reserved: '已预留',
  running: '执行中',
  uncertain: '结果不确定',
  released: '已释放',
}

const executionLockScopeLabels: Record<string, string> = {
  device: '设备',
  material: '物料',
  material_site: '物料库位',
}

function executionLockStateLabel(value: string) {
  return executionLockStateLabels[value] || value || '未知状态'
}

function executionLockScopeLabel(value: string) {
  return executionLockScopeLabels[value] || value || '未知范围'
}

function lockGroups(locks: WorkflowTaskExecutionLock[]) {
  const groups = new Map<string, WorkflowTaskExecutionLock[]>()
  locks.forEach((lock) => {
    const current = groups.get(lock.workflowNodeJobUuid) || []
    current.push(lock)
    groups.set(lock.workflowNodeJobUuid, current)
  })
  return [...groups.entries()]
}

type MaterialTransferSettlementOption = {
  siteUuid: string
  siteName: string
  ownerMaterialUuid: string
  label: string
  phase: 'source' | 'target'
}

/**
 * 将作业冻结的来源/目标库位（Site）解析成可核验选项。
 * @param context 后端返回的失败转运结算上下文。
 * @param materials 当前库存权威投影中的物料和库位。
 * @returns 按来源、目标顺序排列的已发现库位选项；未知库位不会被猜测。
 */
function materialTransferSettlementOptions(
  context: FailedMaterialTransferSettlementContext,
  materials: MaterialRecord[],
): MaterialTransferSettlementOption[] {
  return ([
    ['source', context.sourceSiteUuid],
    ['target', context.targetSiteUuid],
  ] as const).flatMap(([phase, siteUuid]) => {
    const owner = materials.find((material) => material.sites.some((site) => site.uuid === siteUuid))
    const site = owner?.sites.find((candidate) => candidate.uuid === siteUuid)
    if (!owner || !site) return []
    return [{
      siteUuid,
      siteName: site.name,
      ownerMaterialUuid: owner.uuid,
      label: `${owner.name} / ${site.name}`,
      phase,
    }]
  })
}

/**
 * 展示工作流任务（WorkflowTask）的活动执行锁并提供安全人工处置。
 * @param taskUuid 工作流任务稳定身份。
 * @param materials 库存权威投影，用于解析实际库位和父物料身份。
 * @param connected Edge 是否可写。
 * @param onNotify 向控制台发布操作结果。
 * @returns 执行锁列表、物理结算对话框和强制释放对话框。
 */
function TaskExecutionLocks({
  taskUuid,
  materials,
  connected,
  cleanupStatus,
  manualReviewAttentionResolved,
  onNotify,
}: {
  taskUuid: string
  materials: MaterialRecord[]
  connected: boolean
  cleanupStatus?: string
  manualReviewAttentionResolved?: boolean
  onNotify: (message: string) => void
}) {
  const queryClient = useQueryClient()
  const [releaseTarget, setReleaseTarget] = useState<WorkflowTaskExecutionLock>()
  const [releaseReason, setReleaseReason] = useState('')
  const [physicalConfirmed, setPhysicalConfirmed] = useState(false)
  const [taskUnlockBusy, setTaskUnlockBusy] = useState(false)
  const [settlementContext, setSettlementContext] = useState<FailedMaterialTransferSettlementContext>()
  const [settlementOptions, setSettlementOptions] = useState<MaterialTransferSettlementOption[]>([])
  const [settlementSiteUuid, setSettlementSiteUuid] = useState('')
  const [settlementReason, setSettlementReason] = useState('')
  const [settlementConfirmed, setSettlementConfirmed] = useState(false)
  const locksQuery = useQuery({
    queryKey: ['workflow-task-execution-locks', taskUuid],
    queryFn: ({ signal }) => loadWorkflowTaskExecutionLocks(taskUuid, signal),
    enabled: connected && Boolean(taskUuid),
    staleTime: 5_000,
    refetchInterval: 10_000,
  })
  const releaseMutation = useMutation({
    mutationFn: () => {
      if (!connected) throw new Error('Edge 未连接，写操作已暂停')
      if (!releaseTarget) throw new Error('请选择要释放的执行锁')
      if (!releaseTarget.canRelease) throw new Error(releaseTarget.releaseBlockReason || '该执行锁当前不可释放')
      if (!releaseReason.trim()) throw new Error('请填写人工释放原因')
      if (!physicalConfirmed) throw new Error('请确认设备已停止且物理现场已安全')
      return forceReleaseWorkflowTaskExecutionLock(taskUuid, releaseTarget.uuid, {
        expectedClaimUuid: releaseTarget.claimUuid,
        expectedFencingToken: releaseTarget.fencingToken,
        reason: releaseReason.trim(),
        physicalSettlementConfirmed: true,
      })
    },
    onSuccess: (result) => {
      const message = result.status === 'already_released'
        ? '执行锁已经被其他操作释放，列表已刷新。'
        : `已释放该作业的 ${result.releasedLockUuids.length || 1} 把执行锁。`
      onNotify(message)
      setReleaseTarget(undefined)
      setReleaseReason('')
      setPhysicalConfirmed(false)
      void queryClient.invalidateQueries({ queryKey: ['workflow-task-execution-locks', taskUuid] })
      void queryClient.invalidateQueries({ queryKey: ['edge-tasks'] }, { cancelRefetch: false })
      void queryClient.invalidateQueries({ queryKey: ['workflow-task-detail', taskUuid] })
    },
    onError: (error) => {
      onNotify(`执行锁释放失败：${error instanceof Error ? error.message : '未知错误'}`)
      void queryClient.invalidateQueries({ queryKey: ['workflow-task-execution-locks', taskUuid] })
    },
  })
  const prepareSettlementMutation = useMutation({
    mutationFn: (jobUuid: string) => {
      if (!connected) throw new Error('Edge 未连接，写操作已暂停')
      return loadFailedMaterialTransferSettlementContext(jobUuid)
    },
    onSuccess: (context) => {
      const options = materialTransferSettlementOptions(context, materials)
      if (options.length !== 2) {
        onNotify('无法从当前库存投影解析原来源和目标库位，请刷新物料后重试。')
        return
      }
      setSettlementContext(context)
      setSettlementOptions(options)
      setSettlementSiteUuid('')
      setSettlementReason('')
      setSettlementConfirmed(false)
    },
    onError: (error) => onNotify(`读取物理结算信息失败：${error instanceof Error ? error.message : '未知错误'}`),
  })
  const settlementMutation = useMutation({
    mutationFn: () => {
      if (!connected) throw new Error('Edge 未连接，写操作已暂停')
      if (!settlementContext) throw new Error('缺少待结算作业')
      const selectedOption = settlementOptions.find((option) => option.siteUuid === settlementSiteUuid)
      if (!selectedOption) throw new Error('请选择现场核验后的实际库位')
      if (!settlementReason.trim()) throw new Error('请填写物理结算原因')
      if (!settlementConfirmed) throw new Error('请确认已经核验物料实际位置')
      return settleFailedMaterialTransfer(settlementContext.jobUuid, {
        actualChangeSet: {
          kind: 'material_transfer',
          material_uuid: settlementContext.materialUuid,
          target_owner_material_uuid: selectedOption.ownerMaterialUuid,
          target_site_uuid: selectedOption.siteUuid,
        },
        reason: settlementReason.trim(),
      })
    },
    onSuccess: () => {
      onNotify('物理结算已完成，相关执行锁已释放。')
      setSettlementContext(undefined)
      setSettlementOptions([])
      setSettlementSiteUuid('')
      setSettlementReason('')
      setSettlementConfirmed(false)
      void queryClient.invalidateQueries({ queryKey: ['workflow-task-execution-locks', taskUuid] })
      void queryClient.invalidateQueries({ queryKey: ['edge-tasks'] }, { cancelRefetch: false })
      void queryClient.invalidateQueries({ queryKey: ['edge-snapshot'] })
      void queryClient.invalidateQueries({ queryKey: ['workflow-task-detail', taskUuid] })
    },
    onError: (error) => {
      onNotify(`物理结算失败：${error instanceof Error ? error.message : '未知错误'}`)
      void queryClient.invalidateQueries({ queryKey: ['workflow-task-execution-locks', taskUuid] })
    },
  })

  const openReleaseDialog = (lock: WorkflowTaskExecutionLock) => {
    releaseMutation.reset()
    setReleaseTarget(lock)
    setReleaseReason('')
    setPhysicalConfirmed(false)
  }

  return (
    <section className="task-execution-locks" aria-label="任务执行锁">
      <header className="task-execution-locks-header">
        <div>
          <span className="task-lock-eyebrow"><Lock size={13} /> EXECUTION LOCKS</span>
          <h3>执行锁</h3>
          <p>任务异常结束后的持久设备、物料和库位占用。</p>
        </div>
        <Button
          icon={<RefreshCw size={13} />}
          onClick={() => void locksQuery.refetch()}
          disabled={!connected || locksQuery.isFetching}
        >
          刷新锁状态
        </Button>
      </header>
      {!connected ? (
        <div className="task-lock-empty"><AlertCircle size={15} />Edge 未连接，执行锁面板保持只读。</div>
      ) : null}
      {locksQuery.isPending ? <div className="task-lock-empty"><LoaderCircle className="spin" size={15} />正在读取任务执行锁…</div> : null}
      {locksQuery.isError ? (
        <div className="task-lock-error" role="alert">
          <AlertCircle size={15} />读取执行锁失败：{locksQuery.error instanceof Error ? locksQuery.error.message : '未知错误'}
        </div>
      ) : null}
      <TaskResourceUnlock taskUuid={taskUuid} snapshot={locksQuery.data} connected={connected} snapshotUnavailable={locksQuery.isError} cleanupStatus={cleanupStatus} manualReviewAttentionResolved={manualReviewAttentionResolved} busy={releaseMutation.isPending || prepareSettlementMutation.isPending || settlementMutation.isPending || Boolean(releaseTarget || settlementContext)} onBusyChange={setTaskUnlockBusy} onNotify={onNotify} />
      {locksQuery.data ? (
        <>
          <div className="task-lock-warning" role="alert">
            <ShieldAlert size={16} />
            <span><strong>人工释放是现场处置动作。</strong>只在设备已停止、物理现场安全且确认没有在途动作时操作；请按 Job 或整个 Task 选择释放范围。</span>
          </div>
          {locksQuery.data.activeDeviceTenancyCount > 0 ? (
            <div className="task-lock-tenancy-warning"><AlertCircle size={15} />当前任务仍有 {locksQuery.data.activeDeviceTenancyCount} 个活动设备托管，不能按单个 Job 解除；任务级解锁会重新检查全部资源。</div>
          ) : null}
          {locksQuery.data.locks.length ? (
            <div className="task-lock-groups">
              {lockGroups(locksQuery.data.locks).map(([jobUuid, locks]) => {
                const releaseCandidate = locks.find((lock) => lock.canRelease)
                const settlementCandidate = locks.find((lock) => (
                  lock.state === 'uncertain' || lock.claimState === 'uncertain'
                ))
                return (
                  <article className="task-lock-group" key={jobUuid}>
                    <header>
                      <div><strong>Job {jobUuid}</strong><small>Claim {locks[0].claimUuid || '未知'} · {locks[0].jobStatus}</small></div>
                      <span className={`task-lock-group-state ${releaseCandidate ? 'can-release' : 'blocked'}`}>
                        {releaseCandidate ? '可人工释放' : '暂不可释放'}
                      </span>
                    </header>
                    <div className="task-lock-list">
                      {locks.map((lock) => (
                        <div className="task-lock-row" key={lock.uuid}>
                          <div className="task-lock-row-main">
                            <span className="task-lock-scope">{executionLockScopeLabel(lock.scope)}</span>
                            <code title={lock.lockKey}>{lock.lockKey}</code>
                            <span className={`task-lock-state task-lock-state-${lock.state}`}>{executionLockStateLabel(lock.state)}</span>
                          </div>
                          <div className="task-lock-row-meta">
                            <span>Fence {lock.fencingToken}</span>
                            <span>{lock.canRelease ? '可释放' : lock.releaseBlockReason || '后端安全门禁阻止释放'}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                    <footer>
                      <small>{['failed', 'canceled', 'timeout'].includes(locksQuery.data.taskStatus) ? '任务已终止，可按后端资格处置' : '仅终态任务允许人工处置'}</small>
                      <div className="task-lock-group-actions">
                        {settlementCandidate ? (
                          <Button
                            icon={prepareSettlementMutation.isPending ? <LoaderCircle className="spin" size={13} /> : <ShieldAlert size={13} />}
                            disabled={!connected || taskUnlockBusy || prepareSettlementMutation.isPending || settlementMutation.isPending}
                            onClick={() => prepareSettlementMutation.mutate(jobUuid)}
                          >
                            {prepareSettlementMutation.isPending ? '读取结算信息' : '完成物理结算'}
                          </Button>
                        ) : null}
                        <Button
                          tone="danger"
                          icon={<ShieldCheck size={13} />}
                          disabled={!connected || taskUnlockBusy || !releaseCandidate || releaseMutation.isPending}
                          onClick={() => releaseCandidate && openReleaseDialog(releaseCandidate)}
                        >
                          解除这组锁
                        </Button>
                      </div>
                    </footer>
                  </article>
                )
              })}
            </div>
          ) : (
            <div className="task-lock-empty"><ShieldCheck size={15} />当前任务没有活动执行锁。</div>
          )}
        </>
      ) : null}
      {releaseTarget ? (
        <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setReleaseTarget(undefined) }}>
          <section className="task-lock-dialog" role="dialog" aria-modal="true" aria-labelledby="task-lock-release-title">
            <header>
              <div><span>OPERATOR ACTION</span><h2 id="task-lock-release-title">人工解除执行锁</h2><p>目标锁：{releaseTarget.lockKey}</p></div>
              <button type="button" aria-label="关闭执行锁释放对话框" onClick={() => setReleaseTarget(undefined)}><X size={18} /></button>
            </header>
            <form onSubmit={(event) => { event.preventDefault(); releaseMutation.mutate() }}>
              <div className="task-lock-dialog-content">
                <div className="task-lock-dialog-warning"><AlertCircle size={16} /><span>这会释放 Job {releaseTarget.workflowNodeJobUuid} 的全部活动执行锁，并写入操作审计。后端会再次校验 Claim 和 Fence。</span></div>
                {releaseMutation.isError ? <div className="task-lock-dialog-error" role="alert"><AlertCircle size={15} />{releaseMutation.error instanceof Error ? releaseMutation.error.message : '释放失败，请刷新锁列表后重试。'}<small>页面快照可能已过期；请关闭窗口并重新读取当前锁状态。</small></div> : null}
                <label className="form-field"><span>人工释放原因<em>必填</em></span><textarea maxLength={500} required value={releaseReason} onChange={(event) => setReleaseReason(event.target.value)} placeholder="例如：设备已断电，现场人员确认无在途动作。" rows={4} /></label>
                <label className="task-lock-confirmation"><input type="checkbox" checked={physicalConfirmed} onChange={(event) => setPhysicalConfirmed(event.target.checked)} /><span>我已确认设备已停止，物理现场安全，且不存在未上报的在途动作。</span></label>
              </div>
              <footer><Button type="button" onClick={() => setReleaseTarget(undefined)}>取消</Button><Button type="submit" tone="danger" icon={releaseMutation.isPending ? <LoaderCircle className="spin" size={14} /> : <ShieldCheck size={14} />} disabled={!connected || releaseMutation.isPending || !releaseReason.trim() || !physicalConfirmed}>确认解除整组锁</Button></footer>
            </form>
          </section>
        </div>
      ) : null}
      {settlementContext ? (
        <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSettlementContext(undefined) }}>
          <section className="task-lock-dialog" role="dialog" aria-modal="true" aria-labelledby="task-lock-settlement-title">
            <header>
              <div><span>PHYSICAL SETTLEMENT</span><h2 id="task-lock-settlement-title">转运物理结算</h2><p>Job {settlementContext.jobUuid}</p></div>
              <button type="button" aria-label="关闭物理结算对话框" onClick={() => setSettlementContext(undefined)}><X size={18} /></button>
            </header>
            <form onSubmit={(event) => { event.preventDefault(); settlementMutation.mutate() }}>
              <div className="task-lock-dialog-content">
                <div className="task-lock-dialog-warning"><AlertCircle size={16} /><span>请选择现场核验后的真实库位。系统将以该事实更新库存权威、完成物理结算，并释放该 Job 的全部执行锁；不会重新执行机器人动作。</span></div>
                {settlementMutation.isError ? <div className="task-lock-dialog-error" role="alert"><AlertCircle size={15} />{settlementMutation.error instanceof Error ? settlementMutation.error.message : '物理结算失败，请刷新后重试。'}</div> : null}
                <fieldset className="task-lock-settlement-options">
                  <legend>物料实际位置</legend>
                  {settlementOptions.map((option) => (
                    <label key={option.siteUuid}>
                      <input type="radio" name="settlement-site" value={option.siteUuid} checked={settlementSiteUuid === option.siteUuid} onChange={() => setSettlementSiteUuid(option.siteUuid)} />
                      <span>{option.phase === 'source' ? '实际仍在来源库位' : '实际已到目标库位'} <strong>{option.label}</strong></span>
                    </label>
                  ))}
                </fieldset>
                <label className="form-field"><span>物理结算原因<em>必填</em></span><textarea maxLength={500} required value={settlementReason} onChange={(event) => setSettlementReason(event.target.value)} placeholder="例如：现场核验烧杯仍位于来源仓 L1B2。" rows={4} /></label>
                <label className="task-lock-confirmation"><input type="checkbox" checked={settlementConfirmed} onChange={(event) => setSettlementConfirmed(event.target.checked)} /><span>我已确认设备停止，并现场核验了该物料的实际库位。</span></label>
              </div>
              <footer><Button type="button" onClick={() => setSettlementContext(undefined)}>取消</Button><Button type="submit" tone="danger" icon={settlementMutation.isPending ? <LoaderCircle className="spin" size={14} /> : <ShieldCheck size={14} />} disabled={!connected || settlementMutation.isPending || !settlementSiteUuid || !settlementReason.trim() || !settlementConfirmed}>确认结算并释放锁</Button></footer>
            </form>
          </section>
        </div>
      ) : null}
    </section>
  )
}

type JsonSchema = Record<string, any>

function effectiveSchema(schema: Record<string, unknown>): JsonSchema {
  const record = schema as JsonSchema
  if (Array.isArray(record.anyOf)) {
    const branch = record.anyOf.find((item: unknown) => (
      item && typeof item === 'object' && (item as JsonSchema).type !== 'null'
    ))
    return branch ? effectiveSchema(branch as Record<string, unknown>) : record
  }
  return record
}

function scalarResourceSlotSchema(schema: Record<string, unknown>): JsonSchema | undefined {
  const record = schema as JsonSchema
  if (record.$slot === 'ResourceSlot') return record
  if (!Array.isArray(record.anyOf)) return undefined
  return record.anyOf
    .filter((item: unknown) => item && typeof item === 'object')
    .map((item: unknown) => scalarResourceSlotSchema(item as Record<string, unknown>))
    .find(Boolean)
}

function initialFieldValue(field: ContractField): string {
  if (field.defaultValue === undefined || field.defaultValue === null) return ''
  if (scalarResourceSlotSchema(field.schema) && typeof field.defaultValue === 'object') {
    return String((field.defaultValue as Record<string, unknown>).uuid || '')
  }
  if (typeof field.defaultValue === 'object') return JSON.stringify(field.defaultValue, null, 2)
  return String(field.defaultValue)
}

function parseFieldValue(field: ContractField, value: string): unknown {
  const schema = effectiveSchema(field.schema)
  if (scalarResourceSlotSchema(field.schema)) return { uuid: value }
  if (schema.type === 'integer') {
    const parsed = Number(value)
    if (!Number.isInteger(parsed)) throw new Error(`参数 ${field.name} 必须是整数`)
    if (schema.minimum !== undefined && parsed < Number(schema.minimum)) throw new Error(`参数 ${field.name} 不能小于 ${schema.minimum}`)
    if (schema.maximum !== undefined && parsed > Number(schema.maximum)) throw new Error(`参数 ${field.name} 不能大于 ${schema.maximum}`)
    return parsed
  }
  if (schema.type === 'number') {
    const parsed = Number(value)
    if (!Number.isFinite(parsed)) throw new Error(`参数 ${field.name} 必须是数字`)
    if (schema.minimum !== undefined && parsed < Number(schema.minimum)) throw new Error(`参数 ${field.name} 不能小于 ${schema.minimum}`)
    if (schema.maximum !== undefined && parsed > Number(schema.maximum)) throw new Error(`参数 ${field.name} 不能大于 ${schema.maximum}`)
    return parsed
  }
  if (schema.type === 'boolean') return value === 'true'
  if (schema.type === 'array' || schema.type === 'object') {
    let parsed: unknown
    try {
      parsed = JSON.parse(value)
    } catch {
      throw new Error(`参数 ${field.name} 必须是有效 JSON`)
    }
    if (schema.type === 'array' && !Array.isArray(parsed)) throw new Error(`参数 ${field.name} 必须是 JSON 数组`)
    if (schema.type === 'object' && (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))) {
      throw new Error(`参数 ${field.name} 必须是 JSON 对象`)
    }
    return parsed
  }
  if (schema.minLength !== undefined && value.length < Number(schema.minLength)) throw new Error(`参数 ${field.name} 长度不足`)
  if (schema.maxLength !== undefined && value.length > Number(schema.maxLength)) throw new Error(`参数 ${field.name} 长度超限`)
  if (schema.pattern && !new RegExp(String(schema.pattern)).test(value)) throw new Error(`参数 ${field.name} 格式不正确`)
  return value
}

export function serialiseTaskInput(fields: ContractField[], values: Record<string, string>) {
  const entries: [string, unknown][] = []
  fields.forEach((field) => {
    const value = (values[field.name] || '').trim()
    if (!value) {
      if (field.required && field.defaultValue === undefined) {
        throw new Error(`参数 ${field.name} 为必填项`)
      }
      return
    }
    entries.push([field.name, parseFieldValue(field, value)])
  })
  return Object.fromEntries(entries)
}

function CreateTaskDialog({
  workflows,
  materials,
  connected,
  onClose,
  onNotify,
}: {
  workflows: WorkflowDefinition[]
  materials: MaterialRecord[]
  connected: boolean
  onClose: () => void
  onNotify: (message: string) => void
}) {
  const queryClient = useQueryClient()
  const dialogRef = useRef<HTMLElement>(null)
  const [workflowUuid, setWorkflowUuid] = useState(workflows[0]?.uuid || '')
  const workflow = workflows.find((item) => item.uuid === workflowUuid) || workflows[0]
  const [description, setDescription] = useState('从实验运营控制台创建')
  const [input, setInput] = useState<Record<string, string>>({})
  const needsSiteGraph = Boolean(workflow?.inputContract.some((field) => Array.isArray(field.schema.enum)))
  const sourceGraph = useQuery({
    queryKey: ['task-source-site-graph', workflow?.uuid, workflow?.revision],
    queryFn: ({ signal }) => loadWorkflowGraph(workflow!.uuid, signal),
    enabled: connected && needsSiteGraph,
  })
  const siteOptions = sourceSiteOptions(sourceGraph.data, workflow?.inputContract || [], materials)
  const selectedMaterials = [...siteOptions].flatMap(([name, options]) => options.filter((option) => option.value === input[name]).map((option) => option.materialUuid)).filter(Boolean)
  const duplicateMaterial = new Set(selectedMaterials).size !== selectedMaterials.length
  const siteInputBlocked = (needsSiteGraph && !sourceGraph.data) || duplicateMaterial || [...siteOptions].some(([name, options]) => !options.some((option) => option.value === input[name]))

  useEffect(() => {
    if (!workflow) return
    setInput(Object.fromEntries(workflow.inputContract.map((field) => [field.name, initialFieldValue(field)])))
  }, [workflow])

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const dialog = dialogRef.current
    const focusableSelector = 'button:not([disabled]), select:not([disabled]), input:not([disabled]), textarea:not([disabled])'
    dialog?.querySelector<HTMLElement>(focusableSelector)?.focus()

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
        return
      }
      if (event.key !== 'Tab' || !dialog) return
      const focusable = [...dialog.querySelectorAll<HTMLElement>(focusableSelector)]
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable.at(-1)!
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      previousFocus?.focus()
    }
  }, [onClose])

  const mutation = useMutation({
    mutationFn: () => {
      if (!connected) throw new Error('Edge 未连接，写操作已暂停')
      if (!workflow) throw new Error('请选择可运行的工作流')
      if (siteInputBlocked) throw new Error('请为每个来源选择有匹配物料的不同库位')
      return createWorkflowTask({
        workflowUuid: workflow.uuid,
        description,
        input: serialiseTaskInput(workflow.inputContract, input),
      })
    },
    onMutate: () => onClose(),
    onSuccess: (created) => {
      onNotify(`任务 ${created.uuid || ''} 已提交到 Edge`)
      void queryClient.invalidateQueries({ queryKey: ['edge-tasks'] }, { cancelRefetch: false })
    },
    onError: (error) => onNotify(`任务提交失败：${error instanceof Error ? error.message : '未知错误'}`),
  })

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section ref={dialogRef} className="task-dialog" role="dialog" aria-modal="true" aria-labelledby="create-task-title" aria-describedby="create-task-description">
        <form onSubmit={(event) => { event.preventDefault(); if (connected && workflow) mutation.mutate() }}>
          <header><div><span>WORKFLOW RUN</span><h2 id="create-task-title">创建实验任务</h2><p id="create-task-description">仅提交工作流公开输入，中间节点参数由发布修订冻结。</p></div><button type="button" onClick={onClose} aria-label="关闭"><X size={18} /></button></header>
          <div className="dialog-content">
          <label className="form-field"><span>工作流</span><select value={workflowUuid} onChange={(event) => setWorkflowUuid(event.target.value)}>{workflows.map((item) => <option key={item.uuid} value={item.uuid}>{item.name} · r{item.revision}</option>)}</select></label>
          <label className="form-field"><span>任务描述</span><input value={description} onChange={(event) => setDescription(event.target.value)} /></label>
          <div className="form-section-heading"><div><strong>运行输入</strong><small>{workflow?.inputContract.length || 0} 个公开参数</small></div><span><ShieldAlert size={14} />发布修订</span></div>
          <div className="task-input-grid">
            {workflow?.inputContract.length ? workflow.inputContract.map((field) => {
              const schema = effectiveSchema(field.schema)
              const resourceSlot = scalarResourceSlotSchema(field.schema)
              const allowedTemplates = Array.isArray(resourceSlot?.allowed_resource_template_uuids)
                ? new Set(resourceSlot.allowed_resource_template_uuids.map(String))
                : undefined
              const materialOptions = resourceSlot
                ? materials.filter((material) => !allowedTemplates || (material.resourceTemplateUuid && allowedTemplates.has(material.resourceTemplateUuid)))
                : []
              return (
              <label className={`form-field ${resourceSlot ? 'resource-slot-field' : ''}`} key={field.name}>
                <span>{field.title || field.name}<em>{field.required ? `必填 · ${field.type}` : field.type}</em></span>
                {siteOptions.has(field.name) ? (
                  <select required={field.required} value={input[field.name] ?? ''} onChange={(event) => setInput((current) => ({ ...current, [field.name]: event.target.value }))}>
                    <option value="">{siteOptions.get(field.name)?.length ? '请选择有匹配物料的库位' : '没有存放匹配物料的可选库位'}</option>
                    {siteOptions.get(field.name)?.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                ) : Array.isArray(schema.enum) ? (
                  <select required={field.required} disabled={needsSiteGraph && !sourceGraph.data} value={input[field.name] ?? ''} onChange={(event) => setInput((current) => ({ ...current, [field.name]: event.target.value }))}>
                    <option value="">请选择</option>
                    {schema.enum.map((value: unknown) => <option key={String(value)} value={String(value)}>{String(value)}</option>)}
                  </select>
                ) : resourceSlot ? (
                  <select required={field.required} value={input[field.name] ?? ''} onChange={(event) => setInput((current) => ({ ...current, [field.name]: event.target.value }))}>
                    <option value="">{materialOptions.length ? '选择 Edge 物料' : '没有符合模板约束的物料'}</option>
                    {materialOptions.map((material) => (
                      <option key={material.uuid} value={material.uuid}>{material.name} · {material.currentLocation.label}</option>
                    ))}
                  </select>
                ) : schema.type === 'boolean' ? (
                  <select required={field.required} value={input[field.name] ?? ''} onChange={(event) => setInput((current) => ({ ...current, [field.name]: event.target.value }))}>
                    <option value="">{field.required ? '请选择' : '未设置'}</option>
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </select>
                ) : schema.type === 'array' || schema.type === 'object' ? (
                  <textarea
                    required={field.required}
                    value={input[field.name] || ''}
                    onChange={(event) => setInput((current) => ({ ...current, [field.name]: event.target.value }))}
                    placeholder={schema.type === 'array' ? '[ ... ]' : '{ ... }'}
                    rows={4}
                  />
                ) : (
                  <input
                    type={schema.type === 'number' || schema.type === 'integer' ? 'number' : 'text'}
                    required={field.required}
                    min={schema.minimum}
                    max={schema.maximum}
                    step={schema.type === 'integer' ? 1 : schema.multipleOf ?? 'any'}
                    minLength={schema.minLength}
                    maxLength={schema.maxLength}
                    pattern={schema.pattern}
                    value={input[field.name] || ''}
                    onChange={(event) => setInput((current) => ({ ...current, [field.name]: event.target.value }))}
                    placeholder={field.defaultValue === undefined ? field.type : String(field.defaultValue)}
                  />
                )}
              </label>
              )
            }) : <div className="no-input-note"><Circle size={15} />该工作流没有公开输入，可直接创建任务。</div>}
          </div>
          {!connected ? <div className="dialog-warning"><AlertCircle size={16} />Edge 未连接，当前不能提交真实任务。</div> : null}
          {needsSiteGraph && sourceGraph.isPending ? <p role="status">正在读取来源库位配置…</p> : null}
          {needsSiteGraph && sourceGraph.isError ? <p role="alert">来源库位读取失败，请关闭后重试。</p> : null}
          {duplicateMaterial ? <p role="alert">不同来源不能选择同一份物料。</p> : null}
          </div>
          <footer><Button type="button" onClick={onClose}>取消</Button><Button type="submit" tone="primary" icon={mutation.isPending ? <LoaderCircle className="spin" size={16} /> : <Send size={16} />} disabled={!connected || !workflow || mutation.isPending || siteInputBlocked}>提交任务</Button></footer>
        </form>
      </section>
    </div>
  )
}

export function TasksPage({
  tasks,
  workflows,
  materials,
  connected,
  onRefresh,
  onNotify,
  onOpenWorkflow,
  capabilities,
  errorTaskIds = [],
  onHandleError,
  targetTaskUuid,
  onViewResources,
}: {
  tasks: WorkflowTask[]
  workflows: WorkflowDefinition[]
  materials: MaterialRecord[]
  connected: boolean
  onRefresh: () => void
  onNotify: (message: string) => void
  onOpenWorkflow: (target: WorkflowTarget) => void
  capabilities?: RuntimeCapabilities
  errorTaskIds?: string[]
  onHandleError?: (taskUuid: string) => void
  targetTaskUuid?: string
  onViewResources?: (taskUuid: string) => void
}) {
  capabilities = capabilities || DEFAULT_RUNTIME_CAPABILITIES
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState<TaskFilter>('all')
  const [view, setView] = useState<'matrix' | 'gantt'>(() => {
    try { return localStorage.getItem('unilabos-task-view') === 'gantt' ? 'gantt' : 'matrix' } catch { return 'matrix' }
  })
  const changeView = (next: 'matrix' | 'gantt') => {
    setView(next)
    try { localStorage.setItem('unilabos-task-view', next) } catch { /* 存储不可用时仍可切换当前视图。 */ }
  }
  const [selectedId, setSelectedId] = useState(targetTaskUuid || tasks[0]?.uuid || '')
  const [selectedNodeRef, setSelectedNodeRef] = useState<{ taskUuid: string; nodeUuid: string }>()
  const [selectedStepNodeUuid, setSelectedStepNodeUuid] = useState('')
  const [cancelIntents, setCancelIntents] = useState<Record<string, TaskCancelIntent>>(() => readTaskCancelIntents())
  const [cancelDialogOpen, setCancelDialogOpen] = useState(false)
  const interventionsQuery = useQuery({
    queryKey: ['workflow-interventions'],
    queryFn: ({ signal }) => loadWorkflowInterventions(signal),
    enabled: connected,
    refetchInterval: 1_000,
  })
  const openIntervention = interventionsQuery.data?.[0]

  const selectTask = useCallback((taskUuid: string) => {
    setSelectedId(taskUuid)
    setSelectedNodeRef(undefined)
  }, [])

  const selectNode = useCallback((taskUuid: string, nodeUuid: string) => {
    setSelectedId(taskUuid)
    setSelectedNodeRef({ taskUuid, nodeUuid })
  }, [])

  const targetTaskAvailable = tasks.some((task) => task.uuid === targetTaskUuid)
  useEffect(() => {
    if (!targetTaskUuid || !targetTaskAvailable) return
    setFilter('all')
    setSelectedId(targetTaskUuid)
    setSelectedNodeRef(undefined)
  }, [targetTaskUuid, targetTaskAvailable])

  useEffect(() => {
    if (selectedId && !tasks.some((task) => task.uuid === selectedId)) setSelectedId(tasks[0]?.uuid || '')
  }, [tasks, selectedId])

  useEffect(() => {
    if (!selectedNodeRef) return
    const task = tasks.find((item) => item.uuid === selectedNodeRef.taskUuid)
    if (!task?.nodes.some((node) => node.uuid === selectedNodeRef.nodeUuid)) {
      setSelectedNodeRef(undefined)
    }
  }, [tasks, selectedNodeRef])

  const filtered = useMemo(() => tasks.filter((task) => matchesFilter(task, filter)), [tasks, filter])
  const selectedSummary = selectedId
    ? filtered.find((task) => task.uuid === selectedId) || filtered[0]
    : undefined
  const selectedDetailQuery = useQuery({
    queryKey: ['workflow-task-detail', selectedSummary?.uuid],
    queryFn: ({ signal }) => loadWorkflowTaskDetail(selectedSummary!.uuid, materials, signal),
    enabled: connected && Boolean(selectedSummary),
    staleTime: 10_000,
  })
  const selected = selectedDetailQuery.data && selectedDetailQuery.data.uuid === selectedSummary?.uuid
    ? { ...selectedDetailQuery.data, trace: selectedSummary.trace }
    : selectedSummary
  const selectedNodeTask = selectedNodeRef
    ? selected?.uuid === selectedNodeRef.taskUuid
      ? selected
      : tasks.find((task) => task.uuid === selectedNodeRef.taskUuid)
    : undefined
  const selectedNode = selectedNodeTask?.nodes.find((node) => node.uuid === selectedNodeRef?.nodeUuid)
  const runningTasks = tasks.filter((task) => task.nodes.some((node) => node.status === 'running' || node.status === 'canceling'))
  const showStepControls = capabilities.canDebug
  const selectedIsTerminal = Boolean(selected && ['succeeded', 'failed', 'canceled', 'timeout'].includes(selected.executionStatus || selected.status))
  const selectedCancelIntent = selected ? cancelIntents[selected.uuid] : undefined
  const stepStateQuery = useQuery({
    queryKey: ['workflow-task-step-state', selected?.uuid],
    queryFn: ({ signal }) => loadWorkflowTaskStepState(selected!.uuid, signal),
    enabled: showStepControls && connected && Boolean(selected) && !selectedIsTerminal,
    refetchInterval: 2_000,
  })
  const stepState = stepStateQuery.data
  const effectiveExecutionMode = stepState?.executionMode || selected?.executionMode || 'normal'
  const ordinaryTaskPaused = effectiveExecutionMode === 'normal' && selected?.dispatchGate === 'paused'
  const readyNodeUuids = useMemo(
    () => new Set(stepState?.candidates.map((candidate) => candidate.nodeUuid) || []),
    [stepState?.candidates],
  )
  useEffect(() => {
    if (!stepState?.requiresSelection) {
      setSelectedStepNodeUuid(stepState?.candidates[0]?.nodeUuid || '')
      return
    }
    if (!stepState.candidates.some((candidate) => candidate.nodeUuid === selectedStepNodeUuid)) {
      setSelectedStepNodeUuid('')
    }
  }, [stepState, selectedStepNodeUuid])
  const updateCancelIntent = useCallback((taskUuid: string, intent?: TaskCancelIntent) => {
    setCancelIntents((previous) => {
      const next = { ...previous }
      if (intent) next[taskUuid] = intent
      else delete next[taskUuid]
      storeTaskCancelIntents(next)
      return next
    })
  }, [])

  const controlMutation = useMutation({
    mutationFn: ({ type, targetNodeUuid, idempotencyKey }: { type: 'step' | 'switch_to_step' | 'pause' | 'resume' | 'cancel'; targetNodeUuid?: string; idempotencyKey?: string }) => {
      if (!connected) throw new Error('Edge 未连接，写操作已暂停')
      return commandWorkflowTask(selected?.uuid || '', type, targetNodeUuid, idempotencyKey)
    },
    onSuccess: (command, variables) => {
      const labels = {
        step: '单步命令已提交',
        switch_to_step: '已进入单步切换',
        pause: '任务暂停命令已提交',
        resume: '任务继续命令已提交',
        cancel: '取消请求已提交，正在等待设备终态',
      }
      if (variables.type === 'cancel' && selected?.uuid) {
        updateCancelIntent(selected.uuid, {
          taskUuid: selected.uuid,
          idempotencyKey: variables.idempotencyKey || taskCommandUuid('task-cancel'),
          state: command.status === 'succeeded' ? 'accepted' : 'pending',
        })
        setCancelDialogOpen(false)
        onNotify(command.status === 'succeeded' ? labels.cancel : '取消请求已记录，等待状态确认')
      } else {
        onNotify(labels[variables.type])
      }
      void queryClient.invalidateQueries({ queryKey: ['edge-tasks'] }, { cancelRefetch: false })
      void queryClient.invalidateQueries({ queryKey: ['workflow-task-step-state', selected?.uuid] })
      if (selected?.uuid) {
        void queryClient.invalidateQueries({ queryKey: ['workflow-task-detail', selected.uuid] }, { cancelRefetch: false })
        void queryClient.invalidateQueries({ queryKey: ['workflow-task-execution-locks', selected.uuid] }, { cancelRefetch: false })
      }
    },
    onError: (error, variables) => {
      if (variables.type === 'cancel' && selected?.uuid) {
        if (error instanceof TaskCommandRequestError && error.outcomeUnknown) {
          const existing = cancelIntents[selected.uuid]
          updateCancelIntent(selected.uuid, {
            taskUuid: selected.uuid,
            idempotencyKey: variables.idempotencyKey || existing?.idempotencyKey || taskCommandUuid('task-cancel'),
            state: 'unknown',
          })
          setCancelDialogOpen(false)
          onNotify('取消请求结果未知，请确认原取消请求；当前设备仍可能运行')
          return
        }
        updateCancelIntent(selected.uuid)
        setCancelDialogOpen(false)
      }
      onNotify(`任务控制失败：${error instanceof Error ? error.message : '未知错误'}`)
    },
  })

  const submitCancel = useCallback(() => {
    if (!selected) return
    const existing = cancelIntents[selected.uuid]
    const intent = existing || {
      taskUuid: selected.uuid,
      idempotencyKey: taskCommandUuid('task-cancel'),
      state: 'pending' as const,
    }
    updateCancelIntent(selected.uuid, { ...intent, state: 'pending' })
    controlMutation.mutate({ type: 'cancel', idempotencyKey: intent.idempotencyKey })
  }, [cancelIntents, controlMutation, selected, updateCancelIntent])

  const counts: Record<TaskFilter, number> = {
    all: tasks.length,
    running: tasks.filter((task) => matchesFilter(task, 'running')).length,
    waiting: tasks.filter((task) => matchesFilter(task, 'waiting')).length,
    failed: tasks.filter((task) => matchesFilter(task, 'failed')).length,
    succeeded: tasks.filter((task) => matchesFilter(task, 'succeeded')).length,
  }
  const summaryCards: { label: string; value: number; tone: string; icon: LucideIcon }[] = [
    { label: '运行中', value: counts.running, tone: 'blue', icon: Activity },
    { label: '等待资源', value: counts.waiting, tone: 'amber', icon: Clock3 },
    { label: '今日完成', value: counts.succeeded, tone: 'green', icon: Check },
    { label: '需要处理', value: counts.failed, tone: 'red', icon: AlertCircle },
  ]
  const selectedTaskControl = selected && !selectedIsTerminal ? (
    <div className="task-step-inline" aria-label={showStepControls ? 'Task 行内单步调度控制' : 'Task 行内任务控制'}>
      {showStepControls ? (
        <header>
          <span>
            <strong>{effectiveExecutionMode === 'normal' ? (ordinaryTaskPaused ? '任务已暂停' : '自动运行') : effectiveExecutionMode === 'switching_to_step' ? '正在切换' : '单步调试'}</strong>
            <small>{effectiveExecutionMode === 'switching_to_step' ? '等待在途 Job 结束' : effectiveExecutionMode === 'normal' && ordinaryTaskPaused ? '等待继续命令' : '调度模式'}</small>
          </span>
          <code>{effectiveExecutionMode}</code>
        </header>
      ) : null}
      {showStepControls && effectiveExecutionMode === 'step' && stepState?.candidates.length ? (
        <label className="task-step-inline-candidate">
          <span>下一步节点</span>
          <select
            aria-label="下一步节点"
            value={selectedStepNodeUuid}
            disabled={!stepState.requiresSelection}
            onChange={(event) => setSelectedStepNodeUuid(event.target.value)}
          >
            {stepState.requiresSelection && !selectedStepNodeUuid ? <option value="">请选择可执行节点</option> : null}
            {stepState.candidates.map((candidate) => (
              <option key={candidate.nodeUuid} value={candidate.nodeUuid}>
                {candidate.name}{candidate.deviceId ? ` · ${candidate.deviceId}` : ''}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {showStepControls && effectiveExecutionMode === 'step' && !stepStateQuery.isFetching && !stepState?.candidates.length ? (
        <small className="task-step-inline-note">当前没有可执行节点</small>
      ) : null}
      <div className="task-step-inline-actions">
        {effectiveExecutionMode === 'normal' ? (
          <Button
            icon={ordinaryTaskPaused ? <Play size={12} /> : <Pause size={12} />}
            disabled={!connected || controlMutation.isPending}
            onClick={() => controlMutation.mutate({ type: ordinaryTaskPaused ? 'resume' : 'pause' })}
          >
            {ordinaryTaskPaused ? '继续任务' : '暂停任务'}
          </Button>
        ) : null}
        {showStepControls && effectiveExecutionMode === 'normal' && !ordinaryTaskPaused ? (
          <Button icon={<StepForward size={12} />} disabled={!connected || controlMutation.isPending} onClick={() => controlMutation.mutate({ type: 'switch_to_step' })}>切换为单步</Button>
        ) : null}
        {showStepControls && effectiveExecutionMode === 'switching_to_step' ? <Button icon={<Pause size={12} />} disabled>等待切换</Button> : null}
        {showStepControls && effectiveExecutionMode === 'step' ? (
          <>
            <Button tone="primary" icon={<StepForward size={12} />} disabled={!connected || controlMutation.isPending || !stepState?.canStep || (stepState.requiresSelection && !selectedStepNodeUuid)} onClick={() => controlMutation.mutate({ type: 'step', targetNodeUuid: selectedStepNodeUuid || stepState?.candidates[0]?.nodeUuid })}>
              {stepState?.inFlightJobCount ? '当前节点运行中' : '执行下一步'}
            </Button>
            <Button icon={<Play size={12} />} disabled={!connected || controlMutation.isPending || Boolean(stepState?.inFlightJobCount)} onClick={() => controlMutation.mutate({ type: 'resume' })}>继续自动运行</Button>
          </>
        ) : null}
        <Button
          tone="danger"
          icon={<Square size={12} />}
          disabled={!connected || controlMutation.isPending || selectedCancelIntent?.state === 'pending' || selectedCancelIntent?.state === 'accepted'}
          onClick={() => setCancelDialogOpen(true)}
        >
          {selectedCancelIntent?.state === 'unknown' ? '确认原取消请求' : selectedCancelIntent?.state === 'accepted' ? '等待取消终态' : selectedCancelIntent?.state === 'pending' ? '正在提交取消' : '取消任务'}
        </Button>
      </div>
      {ordinaryTaskPaused ? <small className="task-step-inline-note">点击一个待运行节点，在节点详情的“实际运行参数”区域修改参数。</small> : null}
    </div>
  ) : undefined

  return (
    <div className="page tasks-page">
      <HumanInteractionPanel taskUuid={selectedId} connected={connected} onNotify={onNotify} />
      <PageHeader
        eyebrow="PARALLEL TASK MONITOR"
        title={view === 'gantt' ? '多任务时间甘特图' : '并行任务运行矩阵'}
        description={view === 'gantt' ? '在同一时间轴比较多个任务的实际执行与预计安排；点击节点时间条查看详情。' : '不同工作流连续逐行展示；节点轨道同步横向滚动，运行到哪个节点，哪个节点就亮起。'}
        actions={
          <>
            <Button icon={<RefreshCw size={16} />} onClick={onRefresh}>刷新状态</Button>
            <Button tone="primary" icon={<Plus size={17} />} disabled={!workflows.length} onClick={() => workflows[0] && onOpenWorkflow({ workflowUuid: workflows[0].uuid, revision: workflows[0].revision })}>前往工作流创建</Button>
          </>
        }
      />

      <section className="task-summary-strip">
        {summaryCards.map(({ label, value, tone, icon: Icon }) => (
          <div className={`task-summary-item summary-${tone}`} key={label}><span><Icon size={17} /></span><p><small>{label}</small><strong>{value}</strong></p></div>
        ))}
      </section>

      <Panel className="parallel-board">
        <div className="task-toolbar">
          <div className="filter-tabs">
            {([
              ['all', '全部'], ['running', '运行中'], ['waiting', '等待'], ['failed', '异常'], ['succeeded', '已完成'],
            ] as [TaskFilter, string][]).map(([key, label]) => (
              <button key={key} className={filter === key ? 'active' : ''} onClick={() => setFilter(key)}>{label}<span>{counts[key]}</span></button>
            ))}
          </div>
          <div className="task-view-switch" role="group" aria-label="任务展示方式">
            <button type="button" aria-pressed={view === 'matrix'} onClick={() => changeView('matrix')}><LayoutList size={15} />节点矩阵</button>
            <button type="button" aria-pressed={view === 'gantt'} onClick={() => changeView('gantt')}><ChartGantt size={15} />时间甘特图</button>
          </div>
          {view === 'matrix' ? <div className="matrix-legend" aria-label="节点状态颜色">
            <span><i className="node-done" />运行成功</span>
            <span><i className="node-running" />正在运行</span>
            <span><i className="node-waiting" />等待运行</span>
            <span><i className="node-failed" />运行失败</span>
            <span><i className="node-pending" />未运行</span>
          </div> : null}
        </div>
        {filtered.length ? (
          view === 'gantt' ? <TaskGantt
            connected={connected}
            tasks={filtered}
            selectedId={selected?.uuid || ''}
            selectedNode={selectedNodeRef}
            onSelect={selectTask}
            onSelectNode={selectNode}
            onViewResources={onViewResources}
          /> : <TaskMatrix
            // 列表接口只返回任务摘要；选中任务的详情查询会补齐 canDelete 和
            // deleteBlockers，让矩阵中的删除按钮在资格确认后立即可用。
            tasks={filtered.map((task) => selected?.uuid === task.uuid ? selected : task)}
            selectedId={selected?.uuid || ''}
            selectedNode={selectedNodeRef}
            onSelect={selectTask}
            onSelectNode={selectNode}
            onOpenWorkflow={onOpenWorkflow}
            onNotify={onNotify}
            writable={connected}
            readyNodeUuids={selected?.uuid ? readyNodeUuids : new Set<string>()}
            selectedTaskControl={selectedTaskControl}
            onTaskDeleted={(taskUuid) => {
              if (selected?.uuid === taskUuid) {
                setSelectedId('')
                setSelectedNodeRef(undefined)
                setSelectedStepNodeUuid('')
              }
            }}
            onViewResources={onViewResources}
          />
        ) : <EmptyState title="当前筛选没有任务" description="选择其他状态，或创建一个新的工作流任务。" />}
        {view === 'gantt' && selectedTaskControl ? <div className="gantt-selected-controls"><strong>{selected?.workflowName}</strong>{selectedTaskControl}</div> : null}
      </Panel>

      {selectedNodeTask && selectedNode ? (
        <TaskNodeInspector task={selectedNodeTask} node={selectedNode} connected={connected} onNotify={onNotify} onClose={() => setSelectedNodeRef(undefined)} />
      ) : null}

      <TimeSchedulePanel connected={connected} tasks={tasks} />

      <section className="task-detail-grid">
        <Panel className="selected-task-card">
          <PanelHeader title="选中任务" description="点击任务名称或节点切换" action={selected ? <StatusBadge status={selected.status} /> : null} />
          {selected ? (
            <>
              <div className="selected-task-identity"><span><FlaskConical size={18} /></span><div><strong>{selected.uuid}</strong><small>{selected.workflowName}</small></div><ChevronRight size={17} /></div>
              {errorTaskIds.includes(selected.uuid) && onHandleError ? <Button tone="danger" icon={<AlertCircle size={16} />} onClick={() => onHandleError(selected.uuid)}>处理节点错误</Button> : null}
              <dl className="selected-task-properties">
                <div><dt>样品</dt><dd>{selected.sample}</dd></div>
                <div><dt>当前节点</dt><dd>{selected.current}</dd></div>
                <div><dt>整体进度</dt><dd>{selected.progress}%</dd></div>
                <div><dt>更新时间</dt><dd>{selected.updatedAt}</dd></div>
              </dl>
              {selected.trace ? (
                <a
                  className="selected-task-trace-link"
                  href={selected.trace.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label="在 SigNoz 中查看 Trace"
                >
                  <span><ExternalLink size={15} />{selected.trace.mode === 'trace' ? '查看完整 Trace' : '检索历史调度 Trace'}</span>
                  <code>{selected.trace.traceId || selected.uuid}</code>
                </a>
              ) : null}
              {view === 'gantt' ? (
                <TaskDeleteAction
                  task={selected}
                  connected={connected}
                  onNotify={onNotify}
                  onDeleted={() => {
                    setSelectedId('')
                    setSelectedNodeRef(undefined)
                    setSelectedStepNodeUuid('')
                  }}
                />
              ) : null}
              <TaskExecutionLocks
                key={selected.uuid}
                taskUuid={selected.uuid}
                materials={materials}
                connected={connected}
                cleanupStatus={selected.cleanupStatus}
                manualReviewAttentionResolved={selected.manualReviewAttentionResolved}
                onNotify={onNotify}
              />
            </>
          ) : <EmptyState title="没有选中任务" description="选择一个任务查看详情。" />}
        </Panel>

        <Panel className="running-jobs-card">
          <PanelHeader title="正在运行的节点" description={`${runningTasks.length} 个并行 Task`} action={<span className="live-badge"><i />SSE-ready</span>} />
          <div className="running-job-list">
            {runningTasks.length ? runningTasks.map((task) => {
              const active = task.nodes.find((node) => node.status === 'running' || node.status === 'canceling')
              return (
                <button key={task.uuid} onClick={() => selectTask(task.uuid)}><span className="active-job-pulse" /><div><strong>{active?.name || task.current}</strong><small>{task.uuid} · {task.sample}</small></div><em>{task.progress}%</em></button>
              )
            }) : <EmptyState title="没有正在运行的节点" description="Edge 当前任务均已结束或尚未开始。" />}
          </div>
        </Panel>

        <Panel className="task-attention-card">
          <PanelHeader title="阻塞与异常" description="按操作优先级展示" action={<span className="count-badge">{counts.waiting + counts.failed}</span>} />
          <div className="mini-attention-list">
            {tasks.filter((task) => matchesFilter(task, 'waiting')).slice(0, 2).map((task) => <button key={task.uuid} onClick={() => selectTask(task.uuid)}><span className="warn"><Clock3 size={15} /></span><div><strong>{task.uuid} 等待资源</strong><small>{task.current}</small></div><ChevronRight size={15} /></button>)}
            {tasks.filter((task) => matchesFilter(task, 'failed')).slice(0, 2).map((task) => <button key={task.uuid} onClick={() => selectTask(task.uuid)}><span className="danger"><AlertCircle size={15} /></span><div><strong>{task.uuid} 需要处理</strong><small>{task.current}</small></div><ChevronRight size={15} /></button>)}
            {!counts.waiting && !counts.failed ? <EmptyState title="没有阻塞或异常" description="当前任务队列运行正常。" /> : null}
          </div>
        </Panel>
      </section>

      {openIntervention ? <ErrorPolicyDialog intervention={openIntervention} connected={connected} onNotify={onNotify} /> : null}
      {cancelDialogOpen && selected ? (
        <TaskCancelDialog
          task={selected}
          existingIntent={selectedCancelIntent}
          connected={connected}
          pending={controlMutation.isPending}
          onClose={() => setCancelDialogOpen(false)}
          onConfirm={submitCancel}
        />
      ) : null}

    </div>
  )
}
