import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, Check, ChevronDown, ChevronLeft, ChevronRight, Clock3, Layers3, Minus, Plus, RefreshCw } from 'lucide-react'
import type { WorkflowTask } from '../types'
import { currentTimeCandidate, loadTimeSchedule, mergePartialTimeCandidate } from '../lib/timeScheduleClient'
import { buildTaskGantt, formatGanttDuration, formatGanttTime } from '../lib/taskGantt'
import type { GanttBar, GanttRow } from '../lib/taskGantt'
import { taskColor } from '../lib/resourceUsage'
import { Button, EmptyState, StatusBadge } from './ui'
import './TaskGantt.css'

type WindowRange = { start: number; end: number }
const MIN_WINDOW = 30_000
const RECENT_BEFORE = 60_000
const RECENT_AFTER = 120_000
const terminalStatuses = new Set(['succeeded', 'failed', 'canceled', 'timeout'])
const kindLabels: Record<GanttBar['kind'], string> = {
  actual: '实际执行', running: '已运行，结束未确认', planned: '预计执行', remaining: '预计剩余',
}
const actionLabels: Record<string, string> = {
  transfer_material_atomic: '物料搬运', add_liquid_with_mounted_tips: '试剂移液',
  measure_density_with_mounted_tips: '测量密度', stir_beaker: '烧杯搅拌',
  dose_powder_with_two_materials: '粉末加料', add_solvent_with_materials: '溶剂加液',
  inspect_beaker: '拍照检测', pick_pour_place_atomic: '取料、倒液与放料',
  process_liquid_reagent_100ml_cap_with_material: '试剂瓶开关盖',
  process_sample_vial_250ml_cap_with_material: '样品瓶开关盖',
}
const actionColorPalette = [
  '#2f80c0', '#7b61c8', '#b35d94', '#b8782f', '#2f937d',
  '#6b8e3d', '#c65b5b', '#3f6cc5', '#8b6f47', '#3d8b9e',
]
const knownActionColors: Record<string, string> = {
  transfer_material_atomic: '#7b61c8',
  add_liquid_with_mounted_tips: '#2f80c0',
  measure_density_with_mounted_tips: '#b35d94',
  stir_beaker: '#b8782f',
  dose_powder_with_two_materials: '#2f937d',
  add_solvent_with_materials: '#6b8e3d',
  inspect_beaker: '#c65b5b',
  pick_pour_place_atomic: '#3f6cc5',
  process_liquid_reagent_100ml_cap_with_material: '#8b6f47',
  process_sample_vial_250ml_cap_with_material: '#3d8b9e',
}
const nodeLabel = (name: string) => actionLabels[name] || name

function actionColor(name: string): string {
  if (knownActionColors[name]) return knownActionColors[name]
  let hash = 0
  for (let index = 0; index < name.length; index += 1) hash = (hash * 31 + name.charCodeAt(index)) >>> 0
  return actionColorPalette[hash % actionColorPalette.length]
}

/** 无时间区间的记录仍可查看，但不挤占时间轴，也不将已结束的节点误称为待执行。 */
function PendingNodes({ row, onSelectNode }: { row: GanttRow; onSelectNode: (taskUuid: string, nodeUuid: string) => void }) {
  const nodes = new Map(row.task.nodes.map((node) => [node.uuid, node]))
  const groups = [
    { key: 'waiting', label: '时间待定', items: [] as GanttRow['pending'] },
    { key: 'attention', label: '需要关注', items: [] as GanttRow['pending'] },
    { key: 'history', label: '时间记录', items: [] as GanttRow['pending'] },
  ]
  for (const pending of row.pending) {
    const node = nodes.get(pending.nodeUuid)
    const status = node?.status
    const start = node?.job?.startedAt ? Date.parse(node.job.startedAt) : null
    const end = node?.job?.finishedAt ? Date.parse(node.job.finishedAt) : null
    const invalidTime = (start !== null && !Number.isFinite(start)) || (end !== null && !Number.isFinite(end))
      || (start !== null && end !== null && end < start)
    const missingActionTime = node?.kind === 'device_action' && status === 'succeeded' && (start === null || end === null)
    const needsAttention = status === 'attention' || status === 'failed' || Boolean(node?.job?.uncertaintyReason)
      || invalidTime || missingActionTime || pending.reason.includes('已超过预计')
    const ended = ['succeeded', 'skipped', 'canceled'].includes(status || '')
      || terminalStatuses.has(row.task.executionStatus || row.task.status)
    groups[needsAttention ? 1 : ended ? 2 : 0].items.push(pending)
  }
  return <details className="gantt-pending">
    <summary aria-label={`查看 ${row.task.workflowName} ${row.task.uuid.slice(0, 8)} 的节点时间说明：${groups.filter((group) => group.items.length).map((group) => `${group.label} ${group.items.length}`).join('，')}`}>
      <Layers3 size={13} /><span className="gantt-pending-counts">{groups.filter((group) => group.items.length).map((group) => <span className={group.key} key={group.key}>{group.label}<b>{group.items.length}</b></span>)}</span>
      <span className="gantt-expand-label">查看节点</span><ChevronDown size={14} className="gantt-disclosure-chevron" />
    </summary>
    <div className="gantt-pending-list">{groups.filter((group) => group.items.length).map((group) => <div className="gantt-pending-group" key={group.key}>
      <h4>{group.label}<span>{group.items.length}</span>{group.key === 'history' ? <small>已结束或跳过，未绘制完整时间条</small> : null}</h4>
      {group.items.map((node) => <button type="button" key={node.nodeUuid} title={`${node.name}\n${node.reason}`} disabled={!nodes.has(node.nodeUuid)} onClick={() => onSelectNode(row.task.uuid, node.nodeUuid)}>
        <span className="gantt-pending-node-name">{nodeLabel(node.name)}</span><span className="gantt-pending-reason">{node.reason}</span><ChevronRight size={13} />
      </button>)}
    </div>)}</div>
  </details>
}

function extent(bars: GanttBar[], fallback: number): WindowRange {
  const start = bars.length ? Math.min(...bars.map((bar) => bar.startMs)) : fallback - 60_000
  const end = bars.length ? Math.max(...bars.map((bar) => bar.endMs)) : fallback + 60_000
  const width = Math.max(MIN_WINDOW, end - start)
  return { start: start - width * 0.04, end: Math.max(end, start + MIN_WINDOW) + width * 0.08 }
}

/** 首屏聚焦当前附近的时间；暂停产生的长空档不应挤扁当前动作。 */
function recentRange(rows: GanttRow[], now: number): WindowRange {
  const active = rows.filter(({ task }) => !terminalStatuses.has(task.executionStatus || task.status)).flatMap((row) => row.bars)
  if (active.length) {
    // 保持当前附近的历史紧凑，但不能把 CP-SAT 已算出的后续作业裁掉。
    // 只把预计/剩余条的最晚结束时间纳入窗口；历史条不会再次拉伸“当前/最近”。
    const futureEnd = Math.max(
      now + RECENT_AFTER,
      ...active
        .filter((bar) => (bar.kind === 'planned' || bar.kind === 'remaining') && bar.endMs > now)
        .map((bar) => bar.endMs),
    )
    return { start: now - RECENT_BEFORE, end: futureEnd }
  }
  const bars = rows.flatMap((row) => row.bars)
  const latest = bars.length ? Math.max(...bars.map((bar) => bar.endMs)) : now
  return { start: latest - RECENT_BEFORE, end: latest + RECENT_AFTER }
}

function barSummary(bar: GanttBar): string {
  return `${bar.name} · ${kindLabels[bar.kind]}\n${formatGanttTime(bar.startMs, true)} → ${formatGanttTime(bar.endMs, true)}${bar.endIsOpen ? '（观测时刻，非完成时间）' : ''}\n${formatGanttDuration(bar.endMs - bar.startMs)} · ${bar.deviceId || '设备未指定'}`
}

export function TaskGantt({ connected, tasks, selectedId, selectedNode, onSelect, onSelectNode, onViewResources }: {
  connected: boolean
  tasks: WorkflowTask[]
  selectedId: string
  selectedNode?: { taskUuid: string; nodeUuid: string }
  onSelect: (taskUuid: string) => void
  onSelectNode: (taskUuid: string, nodeUuid: string) => void
  onViewResources?: (taskUuid: string) => void
}) {
  const query = useQuery({
    queryKey: ['scheduler-time-plan'], queryFn: ({ signal }) => loadTimeSchedule(signal),
    enabled: connected, refetchInterval: connected ? 2_000 : false, retry: false,
  })
  const [now, setNow] = useState(Date.now)
  const [rangeMode, setRangeMode] = useState<'recent' | 'all'>('recent')
  const [manualRange, setManualRange] = useState<WindowRange | null>(null)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const dragRef = useRef<{ pointerId: number; startX: number; range: WindowRange; width: number } | null>(null)
  const liveCandidate = currentTimeCandidate(query.data, connected, query.isError)
  const partialTaskIds = query.data?.input.partial_tasks || []
  const [lastCandidate, setLastCandidate] = useState<typeof liveCandidate>(null)
  useEffect(() => {
    if (!connected) {
      setLastCandidate(null)
    } else if (liveCandidate) {
      setLastCandidate((previous) => mergePartialTimeCandidate(liveCandidate, previous, partialTaskIds))
    }
  }, [connected, liveCandidate, partialTaskIds])
  useEffect(() => {
    if (!connected) return
    const timer = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [connected])
  // 旧候选只用于保持时间轴连续；服务端下发仍严格使用同 revision 的候选。
  // 求解线程在相邻轮次之间短暂返回 UNKNOWN 时，不能让预计条整体消失而导致坐标轴跳变。
  const mergedLiveCandidate = mergePartialTimeCandidate(liveCandidate, lastCandidate, partialTaskIds)
  const candidate = mergedLiveCandidate || (connected && !query.isError && !query.data?.input_error
    ? lastCandidate : null)
  const candidateStale = Boolean(candidate && candidate !== liveCandidate)
  const blocked = connected && !query.isError && !query.data?.input_error ? query.data?.input.blocked || [] : []
  const rows = useMemo(() => buildTaskGantt(tasks, candidate, blocked, now), [tasks, candidate, blocked, now])
  const bars = rows.flatMap((row) => row.bars)
  const range = manualRange || (rangeMode === 'all' ? extent(bars, now) : recentRange(rows, now))
  const span = range.end - range.start
  const inRange = (bar: GanttBar) => bar.endMs >= range.start && bar.startMs <= range.end
  const hovered = bars.find((bar) => bar.id === hoveredId && inRange(bar))
  const visibleRows = !manualRange && rangeMode === 'all' ? rows : rows.filter((row) => row.bars.some(inRange)
    || (!terminalStatuses.has(row.task.executionStatus || row.task.status) && (row.pending.length || !row.bars.length)))
  const actionLegend = [...new Set(visibleRows.flatMap((row) => row.bars.filter(inRange)
    .map((bar) => bar.actionKey).filter((action): action is string => Boolean(action))))].sort()
  const ticks = Array.from({ length: 9 }, (_, index) => range.start + span * index / 8)
  const nowPercent = (now - range.start) / span * 100
  const planState = !connected ? '连接已断开 · 仅保留最近观测'
    : query.isError ? '计划读取失败 · 已隐藏预计时间'
      : !query.data ? '正在读取时间计划'
        : !query.data.enabled ? '时间排程未启用 · 仅显示实际执行'
          : query.data.input_error ? '规划输入待处理 · 已隐藏预计时间'
              : candidate ? (candidateStale ? '正在重排 · 保留上次预测' : '时间计划已同步')
              : query.data.latest?.status === 'INFEASIBLE' ? '当前无可行计划 · 等待重新排程'
                : '正在重排 · 暂不展示旧预测'
  const zoom = (factor: number) => {
    const nextSpan = Math.min(90 * 24 * 60 * 60_000, Math.max(MIN_WINDOW, span * factor))
    const center = (range.start + range.end) / 2
    setManualRange({ start: center - nextSpan / 2, end: center + nextSpan / 2 })
  }
  const shift = (direction: number) => setManualRange({ start: range.start + span * direction / 2, end: range.end + span * direction / 2 })
  const isInteractiveTarget = (target: EventTarget | null) => target instanceof Element && Boolean(target.closest('button, a, input, textarea, select'))
  const beginPan = (event: ReactPointerEvent<HTMLElement>) => {
    if (event.button !== 0 || isInteractiveTarget(event.target)) return
    const width = event.currentTarget.getBoundingClientRect().width
    if (!Number.isFinite(width) || width <= 0) return
    if (typeof event.currentTarget.setPointerCapture === 'function') event.currentTarget.setPointerCapture(event.pointerId)
    dragRef.current = { pointerId: event.pointerId, startX: event.clientX, range, width }
    setManualRange(range)
    setDragging(true)
  }
  const movePan = (event: ReactPointerEvent<HTMLElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    const deltaMs = -(event.clientX - drag.startX) / drag.width * (drag.range.end - drag.range.start)
    setManualRange({ start: drag.range.start + deltaMs, end: drag.range.end + deltaMs })
  }
  const endPan = (event: ReactPointerEvent<HTMLElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    if (typeof event.currentTarget.hasPointerCapture === 'function'
      && event.currentTarget.hasPointerCapture(event.pointerId)
      && typeof event.currentTarget.releasePointerCapture === 'function') event.currentTarget.releasePointerCapture(event.pointerId)
    dragRef.current = null
    setDragging(false)
  }
  const handleAxisKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
    event.preventDefault()
    shift(event.key === 'ArrowLeft' ? -1 : 1)
  }

  return <section className="task-gantt" aria-label="多任务时间甘特图">
    <div className="gantt-tools">
      <div className="gantt-heading"><span className="gantt-heading-icon"><Activity size={18} /></span><div><strong>任务时间轴</strong><div className="gantt-plan-state"><i className={candidate ? 'synced' : ''} /><span>{planState}</span><small>每 2 秒同步</small></div></div></div>
      <div className="gantt-range-tools">
        <div className="gantt-range-switch" role="group" aria-label="时间范围">
          <button type="button" aria-pressed={!manualRange && rangeMode === 'recent'} onClick={() => { setRangeMode('recent'); setManualRange(null) }}>当前 / 最近</button>
          <button type="button" aria-pressed={!manualRange && rangeMode === 'all'} onClick={() => { setRangeMode('all'); setManualRange(null) }}>全部时间</button>
        </div>
        <button type="button" className="gantt-icon-button" aria-label="向前查看时间" onClick={() => shift(-1)}><ChevronLeft size={15} /></button>
        <button type="button" className="gantt-icon-button" aria-label="缩小甘特图" onClick={() => zoom(2)}><Minus size={15} /></button>
        <button type="button" className="gantt-icon-button" aria-label="放大甘特图" disabled={span <= MIN_WINDOW} onClick={() => zoom(0.5)}><Plus size={15} /></button>
        <button type="button" className="gantt-icon-button" aria-label="向后查看时间" onClick={() => shift(1)}><ChevronRight size={15} /></button>
        <button type="button" className="gantt-now-button" onClick={() => { setRangeMode('recent'); setManualRange(null) }}><Clock3 size={13} />现在</button>
        <Button icon={<RefreshCw size={13} />} disabled={!connected || query.isFetching} onClick={() => { void query.refetch() }}>刷新排程</Button>
      </div>
    </div>
    <div className="gantt-caption">
      <span className="gantt-window-caption"><strong>{visibleRows.length} / {tasks.length} 个任务</strong><span>{formatGanttTime(range.start, true)} — {formatGanttTime(range.end, true)}</span></span>
      <div className="gantt-legend" aria-label="甘特图图例"><span><i className="actual" />实际执行</span><span><i className="running" />正在运行</span><span><i className="planned" />预计执行 / 剩余</span><span><i className="unknown" />时间待定</span>
        {actionLegend.length ? <><b className="gantt-action-legend-title">动作颜色</b>{actionLegend.map((action) => <span key={action} title={action}><i className="gantt-action-swatch" style={{ background: actionColor(action) }} />{nodeLabel(action)}</span>)}</> : null}
      </div>
    </div>
    <div className="gantt-scroll" tabIndex={0} role="region" aria-label="可横向滚动的任务时间轴">
      <div className="gantt-canvas">
        <div className="gantt-axis-row">
          <div className="gantt-identity gantt-axis-label">任务 / 节点 <span>本地时间</span></div>
          <div className={`gantt-axis ${dragging ? 'is-dragging' : ''}`} role="group" tabIndex={0} aria-label="时间刻度，左右拖动查看时间"
            onKeyDown={handleAxisKeyDown} onPointerDown={beginPan} onPointerMove={movePan} onPointerUp={endPan} onPointerCancel={endPan}>
            {ticks.map((tick, index) => <span key={index} style={{ left: `${index * 12.5}%` }}>
            {new Date(range.start).toDateString() !== new Date(range.end).toDateString() ? <small>{new Date(tick).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })}</small> : null}
            {formatGanttTime(tick)}
          </span>)}{nowPercent >= 0 && nowPercent <= 100 ? <i className="gantt-axis-now" style={{ left: `${nowPercent}%` }}>现在</i> : null}</div>
        </div>
        {visibleRows.map((row) => {
          const running = row.task.nodes.filter((node) => ['running', 'canceling'].includes(node.status))
          const completed = row.task.nodes.filter((node) => node.status === 'succeeded').length
          const skipped = row.task.nodes.filter((node) => node.status === 'skipped').length
          const progress = Math.max(0, Math.min(100, row.task.progress || 0))
          return <div className={`gantt-row ${row.task.uuid === selectedId ? 'selected' : ''}`} key={row.task.uuid} style={{ '--task-color': taskColor(row.task.uuid) } as CSSProperties}>
          <div className="gantt-identity">
            <div className="gantt-task-meta"><span title={row.task.sample}>{row.task.sample}</span><StatusBadge status={row.task.status} /></div>
            <button type="button" className="gantt-task-name" title={row.task.workflowName} aria-label={`选择任务 ${row.task.workflowName} ${row.task.uuid}`} onClick={() => onSelect(row.task.uuid)}><i /><strong>{row.task.workflowName}</strong></button>
            <div className="gantt-task-progress"><span>{completed} / {row.task.nodes.length} 完成{skipped ? ` · ${skipped} 跳过` : ''}</span><strong>{progress}%</strong></div>
            <div className="gantt-progress-track" role="progressbar" aria-label={`${row.task.sample} 任务进度`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><i style={{ width: `${progress}%` }} /></div>
            <div className="gantt-task-footer"><code title={row.task.uuid}>{row.task.uuid.slice(0, 8)}</code>{onViewResources ? <button type="button" className="gantt-resource-link" onClick={() => onViewResources(row.task.uuid)}>占用资源<ChevronRight size={11} /></button> : null}</div>
          </div>
          <div className="gantt-row-content">
            <div className={`gantt-activity ${running.length ? 'is-running' : ''}`}>
              {running.length ? <><Activity size={13} /><span>正在执行</span><strong title={running.map((node) => node.name).join('、')}>{running.map((node) => nodeLabel(node.name)).join('、')}</strong></>
                : (row.task.executionStatus || row.task.status) === 'succeeded' ? <><Check size={13} /><strong>任务已完成</strong></>
                  : <><Clock3 size={13} /><span>执行记录与预计安排</span></>}
            </div>
            <div className={`gantt-tracks ${dragging ? 'is-dragging' : ''}`} style={{ height: Math.max(58, row.laneCount * 38 + 20) }}
              onPointerDown={beginPan} onPointerMove={movePan} onPointerUp={endPan} onPointerCancel={endPan}>
              {ticks.map((_, index) => <i className="gantt-grid-line" key={index} style={{ left: `${index * 12.5}%` }} />)}
              {nowPercent >= 0 && nowPercent <= 100 ? <><div className="gantt-future" style={{ left: `${nowPercent}%` }} /><i className="gantt-now-line" style={{ left: `${nowPercent}%` }} /></> : null}
              {row.bars.filter(inRange).map((bar) => {
                const left = Math.max(0, (bar.startMs - range.start) / span * 100)
                const right = Math.min(100, (bar.endMs - range.start) / span * 100)
                const selected = selectedNode?.taskUuid === bar.taskUuid && selectedNode.nodeUuid === bar.nodeUuid
                const detailAvailable = row.task.nodes.some((node) => node.uuid === bar.nodeUuid)
                return <button type="button" key={bar.id}
                  className={`gantt-bar gantt-bar-${bar.kind} gantt-status-${bar.status} ${selected ? 'selected' : ''} ${bar.endIsOpen ? 'open-ended' : ''}`}
                  style={{ '--gantt-action-color': bar.actionKey ? actionColor(bar.actionKey) : undefined, left: `${left}%`, width: `${Math.max(0, right - left)}%`, top: 10 + bar.lane * 38 } as CSSProperties}
                  aria-label={`${row.task.workflowName} · ${barSummary(bar)}`} aria-pressed={selected} title={`${barSummary(bar)}${detailAvailable ? '' : '\n节点详情正在同步'}`} disabled={!detailAvailable}
                  onClick={() => onSelectNode(bar.taskUuid, bar.nodeUuid)}
                  onMouseEnter={() => setHoveredId(bar.id)} onMouseLeave={() => setHoveredId(null)}
                  onFocus={() => setHoveredId(bar.id)} onBlur={() => setHoveredId(null)}>
                  <span className="gantt-bar-label"><strong>{nodeLabel(bar.name)}</strong><small>{formatGanttDuration(bar.endMs - bar.startMs)}</small></span>
                </button>
              })}
              {!row.bars.some(inRange) ? <span className="gantt-row-empty">{row.bars.length ? '执行时间不在当前窗口' : '暂无确定的执行时间'}</span> : null}
            </div>
            {row.pending.length ? <PendingNodes row={row} onSelectNode={onSelectNode} /> : null}
          </div>
        </div>})}
      </div>
    </div>
    {!visibleRows.length ? <EmptyState title="当前时间窗口没有任务" description="点击“全部时间”查看历史，或切回“当前 / 最近”查看最近执行。" /> : null}
    <div className="gantt-detail-strip">{hovered ? <><strong>{nodeLabel(hovered.name)}</strong><span>{kindLabels[hovered.kind]} · {formatGanttTime(hovered.startMs, true)} → {formatGanttTime(hovered.endMs, true)}{hovered.endIsOpen ? '（结束未确认）' : ''}</span><code>{hovered.name}</code><span>点击查看节点详情</span></> : <><span className="gantt-hint-dot" /><span>悬停查看时间，点击查看节点详情；展开“查看节点”了解等待原因。</span></>}</div>
    <p className="gantt-footnote">虚线为预计时间，会随排程更新，不代表资源已释放。时间未知的节点不绘制时长，仅展示已加载任务。</p>
  </section>
}
