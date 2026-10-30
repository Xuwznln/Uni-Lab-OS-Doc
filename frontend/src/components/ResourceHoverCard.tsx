import { useLayoutEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import type { MaterialUsageContext } from './MaterialUsagePanel'
import { deviceTaskIds } from '../lib/materialCanvasLayout'
import { taskColor, usageNodeLabel } from '../lib/resourceUsage'

/** 触发元素的视口矩形；卡片优先放在元素右侧，放不下再放左侧，贴近底部时向上翻。 */
export interface ResourceHover { materialUuid?: string; siteUuid?: string; left: number; top: number; right?: number; bottom?: number }

const CARD_WIDTH = 240
const GAP = 8
const MARGIN = 12

/**
 * 库位与物料的速览卡。没有 Task 关联、占用或转运时只给一行迷你提示，
 * 有内容才展开完整卡片；悬浮信息始终取当前快照，不缓存转运前的位置。
 */
export function ResourceHoverCard({ hover, context }: { hover?: ResourceHover; context: MaterialUsageContext }) {
  const model = context.model
  const card = useRef<HTMLDivElement>(null)
  const [placed, setPlaced] = useState<{ left: number; top: number }>()
  useLayoutEffect(() => {
    setPlaced(undefined)
    if (!hover || !card.current) return
    const { width, height } = card.current.getBoundingClientRect()
    const right = hover.right ?? hover.left
    const bottom = hover.bottom ?? hover.top
    let left = right + GAP
    if (left + width > window.innerWidth - MARGIN) left = hover.left - width - GAP
    if (left < MARGIN) left = Math.max(MARGIN, Math.min(hover.left, window.innerWidth - width - MARGIN))
    let top = hover.top
    if (top + height > window.innerHeight - MARGIN) top = Math.max(MARGIN, bottom - height)
    setPlaced({ left, top })
  }, [hover])
  if (!hover || !model) return null
  const site = hover.siteUuid ? model.sites.get(hover.siteUuid) : undefined
  const uuid = hover.materialUuid || site?.materialUuid
  const materialNode = model.nodes.find((node) => node.material.uuid === uuid)
  const label = materialNode ? usageNodeLabel(materialNode) : undefined
  const currentSite = [...model.sites.values()].find((entry) => entry.materialUuid === uuid)
  const device = model.devices.find((entry) => entry.uuid === uuid)
  const bindings = model.bindings.filter((binding) => binding.material_uuid === uuid)
  const materialTasks = [...new Set([...bindings.map((binding) => binding.workflow_task_uuid),
    ...model.holdings.filter((holding) => holding.resourceUuid === uuid).map((holding) => holding.taskUuid),
    ...model.devices.flatMap((entry) => entry.executions || []).filter((job) => job.expected_change_set?.material_uuid === uuid).map((job) => job.task_uuid),
  ].filter(Boolean))]
  const siteTasks = [...new Set([...(site?.holdings || []).map((holding) => holding.taskUuid), ...(site?.transfers || []).map((transfer) => transfer.job.task_uuid)].filter(Boolean))]
  const deviceTasks = device ? deviceTaskIds(device) : []
  const planned = site?.planned || []
  const quiet = !materialTasks.length && !siteTasks.length && !deviceTasks.length && !planned.length && !context.stale
  // 迷你提示按内容自适应宽度（CSS 限最大 320），完整卡固定 240。
  const style: CSSProperties = { left: placed?.left ?? hover.left, top: placed?.top ?? hover.top, visibility: placed ? 'visible' : 'hidden', ...(quiet ? {} : { width: CARD_WIDTH }) }
  const renderTasks = (ids: string[]) => ids.map((id) => {
    const task = context.tasks.find((entry) => entry.uuid === id)
    const jobs = model.devices.flatMap((entry) => entry.executions || []).filter((job) => job.task_uuid === id)
    return <div className="resource-hover-task" key={id} style={{ '--usage-color': taskColor(id) } as CSSProperties}><div>{task?.workflowName || jobs[0]?.task_name || 'Task'}</div><code>Task {id}</code>{jobs.length ? <small>节点：{[...new Set(jobs.map((job) => job.node_name))].join('、')}</small> : null}</div>
  })
  // 迷你提示：主体 · 状态，一行说完。
  if (quiet) {
    const text = site
      ? `库位 ${site.name} · ${site.materialName ? `当前物料 ${site.materialName}` : '空库位'}`
      : `${device ? '设备' : '物料'} ${label?.text || uuid} · 空闲`
    return createPortal(<div ref={card} className="resource-hover-card compact" role="tooltip" style={style}>{text}</div>, document.body)
  }
  return createPortal(<div ref={card} className="resource-hover-card" role="tooltip" style={style}><strong>{site ? `库位 · ${site.name}` : `${device ? '设备' : '物料'} · ${label?.text || uuid}`}</strong>
    {context.stale ? <p>最后一次快照，当前状态可能已变化</p> : null}
    {site ? <>
      <small>所属容器：{site.ownerName}</small>
      <p>当前物料：{site.materialName || '空库位'}</p>
      {planned.map((entry) => <p key={entry.holding.id}>计划放入：{entry.materialName}</p>)}
      {site.materialUuid && materialTasks.length ? <><small>物料关联 Task</small>{renderTasks(materialTasks)}</> : null}
      {siteTasks.length ? <><small>库位使用 Task</small>{renderTasks(siteTasks)}</> : null}
    </> : <>
      <p>当前位置：{currentSite ? `${currentSite.ownerName} / ${currentSite.name}` : device ? '画布设备' : '未分配库位'}</p>
      {device ? deviceTasks.length ? <><small>设备使用 Task</small>{renderTasks(deviceTasks)}</> : null
        : materialTasks.length ? <><small>物料关联 Task</small>{renderTasks(materialTasks)}</> : null}
    </>}
  </div>, document.body)
}
