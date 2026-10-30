import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react'
import { ArrowRight, Boxes, ChevronDown, ChevronUp, Cpu, Focus, GripVertical, History, Minus, Plus, RotateCcw, X } from 'lucide-react'
import { EDGE_API_BASE, loadTaskMaterialEvents } from '../lib/edgeClient'
import type { TaskMaterialEvent } from '../lib/edgeClient'
import { CANVAS_FRAME_SIZE, CANVAS_SITE_SIZE, canvasBlockSize, deviceTaskIds, layoutMaterialCanvas, locateCanvasResource } from '../lib/materialCanvasLayout'
import type { CanvasBlock, CanvasPoint, SiteSection } from '../lib/materialCanvasLayout'
import { describeMaterialEvent, diffSiteOccupancy, eventStepLabel, findEventSite } from '../lib/materialCanvasChanges'
import type { CanvasChange } from '../lib/materialCanvasChanges'
import { taskColor, usageNeedsAttention, usageNodeLabel } from '../lib/resourceUsage'
import type { ResourceUsageModel } from '../lib/resourceUsage'
import type { MaterialUsageContext } from './MaterialUsagePanel'
import { TaskChip } from './MaterialUsagePanel'

export interface CanvasSelection { materialUuid: string; siteUuid?: string; request: number }
type View = CanvasPoint & { scale: number }
type Arrow = { x1: number; y1: number; x2: number; y2: number }
// 用户手动平移/缩放后暂停自动跟随的时长。
const FOLLOW_PAUSE_MS = 8000
const FLASH_MS = 9000
// 左上角“最近变化”小条的高度；适应画布和自动定位都在其下方的可见区域内居中。
const CHANGES_TOP = 46
// 同时容纳来源与目标时若只能缩到这个比例以下，改为放大目标、来源由箭头指入。
const FOCUS_MIN_SCALE = .85
const storageKey = `unilab:material-canvas:v1:${EDGE_API_BASE}`

function readPositions(): Record<string, CanvasPoint> {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey) || '{}')
    return Object.fromEntries(Object.entries(value).filter((entry): entry is [string, CanvasPoint] => {
      const point = entry[1] as CanvasPoint | null
      return Boolean(point && Number.isFinite(point.x) && Number.isFinite(point.y))
    }))
  } catch { return {} }
}

/** 分组内有料库位数 / 库位总数；多分组设备按当前分组统计，避免把不同料仓混在一起。 */
function sectionFill(section: SiteSection) {
  return `${section.sites.filter(({ site }) => site.materialUuid).length}/${section.sites.length}`
}

/** 设备标题与库位共用一个变换容器；拖动只保存本浏览器的展示位置。 */
export function MaterialResourceCanvas({ model, context, selection, onSelect, onClear, onDetails, visibleIds, historyTask }: {
  model: ResourceUsageModel; context: MaterialUsageContext; selection?: CanvasSelection
  onSelect: (materialUuid: string, siteUuid?: string) => void; onClear: () => void
  onDetails: (materialUuid: string, siteUuid?: string) => void; visibleIds?: Set<string>
  /** "物料变化"面板展示其历史的任务：页面筛选的任务，未筛选时为正在运行的任务。 */
  historyTask?: { uuid: string; name: string; running: boolean }
}) {
  const viewport = useRef<HTMLDivElement>(null)
  const [view, setView] = useState<View>({ x: 24, y: 24, scale: .7 })
  const [positions, setPositions] = useState(readPositions)
  const positionsRef = useRef(positions)
  const [sections, setSections] = useState<Record<string, string>>({})
  const drag = useRef<{ id?: string; client: CanvasPoint; origin: CanvasPoint; scale: number; moved: boolean } | null>(null)
  const layout = useMemo(() => layoutMaterialCanvas(model), [model])
  const blocks = useMemo(() => layout.map((block) => {
    const section = block.sections.find((entry) => entry.uuid === sections[block.uuid]) || block.sections[0]
    return { ...block, ...canvasBlockSize(section), section }
  }), [layout, sections])
  const devices = new Map(model.devices.map((device) => [device.uuid, device]))
  const shown = blocks.filter((block) => !visibleIds || visibleIds.has(block.uuid) || devices.get(block.uuid)!.sites.some((site) => visibleIds.has(site.ownerUuid) || Boolean(site.materialUuid && visibleIds.has(site.materialUuid))))
  const located = locateCanvasResource(model, selection?.materialUuid, selection?.siteUuid)
  const selectedNode = model.nodes.find((node) => node.material.uuid === selection?.materialUuid)
  const selectedName = located.site?.name || (selectedNode ? usageNodeLabel(selectedNode).text : undefined)
  const initialFit = useRef(false)
  const viewRef = useRef(view)
  viewRef.current = view
  // 最近一次手动操作画布的时间；跟随变化在此之后暂停一段时间，不和用户抢视口。
  const lastInteraction = useRef(0)
  const [follow, setFollow] = useState(true)
  const followRef = useRef(follow)
  followRef.current = follow
  const [changesOpen, setChangesOpen] = useState(false)
  // 物料变化历史来自 Edge 库存台账：刷新不丢，能回看整轮，每条附产生它的步骤。
  // 任务运行中 10 秒刷新一次，间隔长于画布快照，避免给执行进程增加负担。
  const history = useQuery({
    queryKey: ['task-material-events', historyTask?.uuid],
    queryFn: ({ signal }) => loadTaskMaterialEvents(historyTask!.uuid, signal),
    enabled: Boolean(historyTask?.uuid),
    refetchInterval: historyTask?.running ? 10_000 : false,
  })
  const events = useMemo(() => [...(history.data || [])].reverse(), [history.data])
  const [flash, setFlash] = useState<{ from?: string; to?: string; arrow?: Arrow; until: number } | null>(null)
  const previousModel = useRef<ResourceUsageModel | null>(null)
  const pendingFocus = useRef<CanvasChange | null>(null)

  function savePositions(next: Record<string, CanvasPoint>) {
    positionsRef.current = next
    setPositions(next)
    try { localStorage.setItem(storageKey, JSON.stringify(next)) } catch { /* 浏览器禁用存储时仍允许本次查看。 */ }
  }
  function touch() { lastInteraction.current = Date.now() }
  function fit() {
    touch()
    if (!shown.length || !viewport.current) return
    const points = shown.map((block) => ({ ...block, ...(positionsRef.current[block.uuid] || {}) }))
    const x = Math.min(...points.map((p) => p.x)), y = Math.min(...points.map((p) => p.y))
    const width = Math.max(...points.map((p) => p.x + p.width)) - x
    const height = Math.max(...points.map((p) => p.y + p.height)) - y
    const rect = viewport.current.getBoundingClientRect()
    const scale = Math.max(.15, Math.min(1, (rect.width - 70) / width, (rect.height - CHANGES_TOP - 85) / height))
    setView({ x: (rect.width - width * scale) / 2 - x * scale, y: CHANGES_TOP + (rect.height - CHANGES_TOP - height * scale) / 2 - y * scale, scale })
  }
  function focusBlock(block: CanvasBlock, section?: SiteSection) {
    const point = positionsRef.current[block.uuid] || block
    const size = canvasBlockSize(section)
    const rect = viewport.current?.getBoundingClientRect()
    if (!rect?.width) return
    const scale = Math.min(1, (rect.width - 60) / size.width, (rect.height - 80) / size.height)
    setView({ x: (rect.width - size.width * scale) / 2 - point.x * scale, y: (rect.height - size.height * scale) / 2 - point.y * scale, scale })
  }
  useEffect(() => {
    if (initialFit.current || !shown.length) return
    const rect = viewport.current?.getBoundingClientRect()
    if (rect?.width) {
      initialFit.current = true
      const primary = model.devices.find((device) => device.isDevice && device.sites.length)
      const block = shown.find((entry) => entry.uuid === primary?.uuid) || shown[0]
      const point = positionsRef.current[block.uuid] || block
      const scale = Math.min(.85, (rect.width - 50) / block.width, (rect.height - 80) / block.height)
      setView({ scale, x: (rect.width - block.width * scale) / 2 - point.x * scale, y: (rect.height - block.height * scale) / 2 - point.y * scale })
    }
  })
  // 选择同一个烧杯后若发生转运，焦点跟随新库位；普通占用轮询不会重置视口。
  useEffect(() => {
    if (!selection || !located.device) return
    const block = blocks.find((entry) => entry.uuid === located.device!.uuid)
    if (!block) return
    const owner = located.site?.ownerUuid || selection.materialUuid
    const section = block.sections.find((entry) => entry.uuid === owner) || block.sections[0]
    if (section) {
      setSections((current) => ({ ...current, [block.uuid]: section.uuid }))
    }
    focusBlock(block, section)
  // 只响应显式选择或所选物料的实际位置变化。
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection?.request, located.device?.uuid, located.site?.uuid])

  // 每次快照比较库位占用：物料位置变了就记一条变化，高亮来源与目标，并在跟随开启时把视口移过去。
  useEffect(() => {
    const previous = previousModel.current
    previousModel.current = model
    if (!previous) return
    const found = diffSiteOccupancy(previous, model)
    if (!found.length) return
    // 画布看到位置变了，历史面板随即补读，不等下一次定时刷新。
    if (historyTask?.uuid) void history.refetch()
    const latest = found[0]
    setFlash({ from: latest.from?.siteUuid, to: latest.to?.siteUuid, until: Date.now() + FLASH_MS })
    if (followRef.current && Date.now() - lastInteraction.current > FOLLOW_PAUSE_MS) showChange(latest)
  // 只在库存快照变化时比较；其余依赖通过 ref 读取最新值。
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model])
  useEffect(() => {
    if (!flash) return
    const timer = window.setTimeout(() => setFlash(null), Math.max(0, flash.until - Date.now()))
    return () => window.clearTimeout(timer)
  }, [flash])

  /** 点击一条历史：搬运定位到来源与目标库位；消耗和已使用定位到该物料。 */
  function showEvent(event: TaskMaterialEvent) {
    lastInteraction.current = 0
    if (event.kind === 'moved') {
      const from = findEventSite(model, event.fromSite, event.fromOwnerUuid)
      const to = findEventSite(model, event.toSite, event.toOwnerUuid)
      if (from || to) {
        setFlash({ from: from?.siteUuid, to: to?.siteUuid, until: Date.now() + FLASH_MS })
        showChange({ id: event.uuid, materialUuid: event.materialUuid, materialName: event.materialName, from, to, at: Date.parse(event.occurredAt) })
        return
      }
    }
    onSelect(event.materialUuid)
  }

  /** 切到来源与目标库位所在分组，渲染后按实际库位位置把两者一起放进视口中央。 */
  function showChange(change: CanvasChange) {
    const sectionsFor: Record<string, string> = {}
    for (const ref of [change.from, change.to]) {
      if (!ref) continue
      const site = model.sites.get(ref.siteUuid)
      const device = site && model.devices.find((entry) => entry.sites.some((item) => item.uuid === ref.siteUuid))
      if (site && device && !sectionsFor[device.uuid]) sectionsFor[device.uuid] = site.ownerUuid
    }
    if (change.to) {
      const site = model.sites.get(change.to.siteUuid)
      const device = site && model.devices.find((entry) => entry.sites.some((item) => item.uuid === change.to!.siteUuid))
      if (site && device) sectionsFor[device.uuid] = site.ownerUuid
    }
    setSections((current) => ({ ...current, ...sectionsFor }))
    pendingFocus.current = change
  }
  useEffect(() => {
    const change = pendingFocus.current
    if (!change) return
    // 等分组切换渲染完成后再量库位位置；若期间又重渲染，下一次渲染会重新排队。
    const frame = requestAnimationFrame(() => {
      if (pendingFocus.current !== change) return
      pendingFocus.current = null
      const element = viewport.current
      if (!element) return
      const rect = element.getBoundingClientRect()
      const current = viewRef.current
      const center = (uuid?: string) => {
        const tile = uuid ? element.querySelector<HTMLElement>(`[data-site-uuid="${CSS.escape(uuid)}"]`) : null
        if (!tile) return undefined
        const box = tile.getBoundingClientRect()
        return { x: (box.left + box.width / 2 - rect.left - current.x) / current.scale, y: (box.top + box.height / 2 - rect.top - current.y) / current.scale }
      }
      const from = center(change.from?.siteUuid), to = center(change.to?.siteUuid)
      const points = [from, to].filter(Boolean) as CanvasPoint[]
      if (!points.length || !rect.width) return
      const margin = 110
      const x0 = Math.min(...points.map((p) => p.x)) - margin, x1 = Math.max(...points.map((p) => p.x)) + margin
      const y0 = Math.min(...points.map((p) => p.y)) - margin, y1 = Math.max(...points.map((p) => p.y)) + margin
      const top = CHANGES_TOP, height = rect.height - top
      const both = Math.min(1.1, (rect.width - 40) / (x1 - x0), (height - 60) / (y1 - y0))
      if (both >= FOCUS_MIN_SCALE || !to) {
        const scale = Math.max(.35, both)
        setView({ scale, x: rect.width / 2 - (x0 + x1) / 2 * scale, y: top + height / 2 - (y0 + y1) / 2 * scale })
      } else {
        // 来源与目标相距太远时，放大到目标库位并把它放在中央；来源一侧由箭头从画面边缘指入。
        const scale = 1
        const offset = from ? Math.max(-1, Math.min(1, (from.x - to.x) / Math.max(1, Math.abs(from.x - to.x)))) : 0
        const centerX = rect.width / 2 - offset * rect.width * .18
        setView({ scale, x: centerX - to.x * scale, y: top + height / 2 - to.y * scale })
      }
      setFlash((state) => ({ from: change.from?.siteUuid, to: change.to?.siteUuid, until: state?.until ?? Date.now() + FLASH_MS,
        ...(from && to ? { arrow: { x1: from.x, y1: from.y, x2: to.x, y2: to.y } } : {}) }))
    })
    return () => cancelAnimationFrame(frame)
  })

  useEffect(() => {
    const element = viewport.current
    if (!element) return
    const wheel = (event: WheelEvent) => {
      if ((event.target as HTMLElement).closest('.resource-canvas-popover, select')) return
      event.preventDefault()
      lastInteraction.current = Date.now()
      const rect = element.getBoundingClientRect()
      const x = event.clientX - rect.left, y = event.clientY - rect.top
      setView((current) => {
        const scale = Math.max(.15, Math.min(2, current.scale * Math.exp(-event.deltaY * .002)))
        const factor = scale / current.scale
        return { scale, x: x - (x - current.x) * factor, y: y - (y - current.y) * factor }
      })
    }
    element.addEventListener('wheel', wheel, { passive: false })
    return () => element.removeEventListener('wheel', wheel)
  }, [])

  function startDrag(event: ReactPointerEvent, id?: string) {
    if (event.button !== 0) return
    if (!id && (event.target as HTMLElement).closest('button, select, .resource-canvas-popover')) return
    event.stopPropagation()
    touch()
    const block = blocks.find((entry) => entry.uuid === id)
    drag.current = { id, client: { x: event.clientX, y: event.clientY }, origin: id ? positionsRef.current[id] || block! : view, scale: view.scale, moved: false }
    event.currentTarget.setPointerCapture?.(event.pointerId)
  }
  function moveDrag(event: ReactPointerEvent) {
    const current = drag.current
    if (!current) return
    const dx = event.clientX - current.client.x, dy = event.clientY - current.client.y
    if (Math.abs(dx) + Math.abs(dy) < 4 && !current.moved) return
    current.moved = true
    if (current.id) {
      const next = { ...positionsRef.current, [current.id]: { x: current.origin.x + dx / current.scale, y: current.origin.y + dy / current.scale } }
      positionsRef.current = next; setPositions(next)
    } else setView((view) => ({ ...view, x: current.origin.x + dx, y: current.origin.y + dy }))
  }
  function endDrag() {
    if (drag.current?.id && drag.current.moved) savePositions(positionsRef.current)
    drag.current = null
  }
  function zoom(factor: number) {
    touch()
    const rect = viewport.current?.getBoundingClientRect()
    setView((current) => {
      const scale = Math.max(.15, Math.min(2, current.scale * factor)), ratio = scale / current.scale
      const x = (rect?.width || 0) / 2, y = (rect?.height || 0) / 2
      return { scale, x: x - (x - current.x) * ratio, y: y - (y - current.y) * ratio }
    })
  }

  return <section className="resource-canvas-shell" aria-label="设备与库位画布" style={{
    '--device-border': `${CANVAS_FRAME_SIZE.border}px`,
    '--device-head-height': `${CANVAS_FRAME_SIZE.headerHeight}px`,
    '--device-padding-x': `${CANVAS_FRAME_SIZE.paddingX}px`,
    '--device-padding-y': `${CANVAS_FRAME_SIZE.paddingY}px`,
    '--device-section-height': `${CANVAS_FRAME_SIZE.sectionHeight}px`,
    '--device-section-gap': `${CANVAS_FRAME_SIZE.sectionGap}px`,
    '--device-empty-height': `${CANVAS_SITE_SIZE.height}px`,
  } as CSSProperties}>
    <div className="resource-canvas-legend"><span><Cpu size={15} />设备：Task 使用时点亮</span><span><Boxes size={15} />库位：有物料时点亮</span><span><i className="task" />Task 色标</span><span><i className="stock" />有料 · 未绑定 Task</span><span><i />空闲 / 空库位</span></div>
    <div className="resource-canvas-viewport" ref={viewport} onPointerDown={(event) => startDrag(event)} onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={endDrag}>
      {!shown.length ? <div className="resource-canvas-empty"><Boxes size={32} /><strong>暂无匹配资源</strong><span>可清除筛选，或在左侧选择物料查看详情。</span></div> : null}
      <div className="resource-canvas-plane" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})` }}>
        {shown.map((block) => {
          const device = devices.get(block.uuid)!
          const taskIds = deviceTaskIds(device)
          const attention = device.holdings.some(usageNeedsAttention)
          const section = block.section
          const point = positions[block.uuid] || block
          return <article key={block.uuid} aria-label={`设备组 ${device.name}`} className={`resource-device-block ${located.device?.uuid === block.uuid ? 'selected' : ''}`} style={{ left: point.x, top: point.y, width: block.width, height: block.height }}>
            <header className={`resource-device-head ${taskIds.length ? 'lit' : ''}`} data-in-use={Boolean(taskIds.length)} style={{ '--usage-color': taskColor(taskIds[0] || '') } as CSSProperties}>
              <button className="resource-device-drag" aria-label={`移动设备及库位 ${device.name}`} title="拖动设备和全部库位；方向键微调" onPointerDown={(event) => startDrag(event, block.uuid)} onKeyDown={(event) => {
                const delta: Record<string, CanvasPoint> = { ArrowLeft: { x: -20, y: 0 }, ArrowRight: { x: 20, y: 0 }, ArrowUp: { x: 0, y: -20 }, ArrowDown: { x: 0, y: 20 } }
                if (!delta[event.key]) return
                event.preventDefault(); savePositions({ ...positionsRef.current, [block.uuid]: { x: point.x + delta[event.key].x, y: point.y + delta[event.key].y } })
              }}><GripVertical size={17} /></button>
              <button className="resource-device-title" data-usage-material={device.uuid} aria-label={`查看设备 ${device.name}`} onClick={() => onSelect(device.uuid)}><strong>{device.label.primary}</strong>{device.label.secondary ? <em className="resource-device-kind">{device.label.secondary}</em> : null}<small>{taskIds.length ? taskIds.map((id) => `Task ${id.slice(0, 8)}`).join(' · ') : attention ? '占用待处理' : '空闲'}</small></button>
              <span className="resource-device-state">{attention ? '⚠' : <i />}{taskIds.length ? '使用中' : attention ? '待确认' : '空闲'}</span>
            </header>
            {section ? <div className="resource-device-sites">
              <div className="resource-site-section"><select aria-label={`${device.name} 库位分组`} value={section?.uuid || ''} disabled={!section} onChange={(event) => {
                const uuid = event.target.value
                touch()
                setSections((current) => ({ ...current, [block.uuid]: uuid }))
                focusBlock(block, block.sections.find((entry) => entry.uuid === uuid))
              }}>{block.sections.map((entry) => <option key={entry.uuid} value={entry.uuid}>{block.sections.length > 1 ? `${entry.name} · ${sectionFill(entry)}` : entry.name}</option>)}{!section ? <option>未配置库位</option> : null}</select><span title="当前分组的有料库位数 / 库位总数">{section ? sectionFill(section) : '0/0'} 有料</span></div>
              {section ? <div className="resource-site-cells" style={{ gridTemplateColumns: `repeat(${section.columns}, ${CANVAS_SITE_SIZE.width}px)`, gridTemplateRows: `${section.layerLabels?.length ? `${CANVAS_FRAME_SIZE.layerLabelHeight}px ` : ''}repeat(${section.rows}, ${CANVAS_SITE_SIZE.height}px)`, gap: CANVAS_SITE_SIZE.gap }}>
                {section.layerLabels?.map((layer) => <span key={layer.column} className="resource-site-layer-label" style={{ gridColumn: layer.column + 1, gridRow: 1 }} title={layer.name}>{layer.name}</span>)}
                {section.sites.map(({ site, column, row }) => {
                const ids = [...new Set(site.bindings.map((binding) => binding.workflow_task_uuid))]
                const color = ids.length ? taskColor(ids[0]) : '#069c91'
                const transfers = site.transfers || []
                const warning = transfers.some((transfer) => transfer.uncertain) || site.holdings.some(usageNeedsAttention)
                const marker = warning ? '位置待确认' : transfers.some((transfer) => transfer.direction === 'out') ? '转移中' : transfers.some((transfer) => transfer.direction === 'in') ? '待放入' : site.holdings.length ? '已锁定' : ''
                const binding = ids.length ? `关联 Task：${ids.join('、')}` : site.materialUuid ? '未绑定 Task' : ''
                const description = [`当前物料：${site.materialName || '空库位'}`, marker, binding].filter(Boolean).join('；')
                const status = warning ? '待确认' : marker || (site.materialUuid ? '有料' : '空')
                return <button key={site.uuid} aria-label={`库位 ${site.ownerName} / ${site.name}`} aria-description={description} data-site-uuid={site.uuid} data-usage-site={site.uuid} data-occupied={Boolean(site.materialUuid)} title={`${site.ownerName} / ${site.name}：${description}`} className={`resource-site-cell ${site.materialUuid ? 'lit' : ''} ${located.site?.uuid === site.uuid ? 'selected' : ''} ${warning ? 'attention' : ''} ${flash?.to === site.uuid ? 'moved-to' : ''} ${flash?.from === site.uuid ? 'moved-from' : ''}`} style={{ gridColumn: column + 1, gridRow: row + (section.layerLabels?.length ? 2 : 1), '--usage-color': color } as CSSProperties} onClick={() => onSelect(site.ownerUuid, site.uuid)}><strong>{site.name}</strong><small>{status}</small></button>
              })}</div> : null}
            </div> : null}
          </article>
        })}
        {flash?.arrow ? <svg className="resource-canvas-arrow" aria-hidden="true"><defs><marker id="resource-move-head" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" /></marker></defs><line x1={flash.arrow.x1} y1={flash.arrow.y1} x2={flash.arrow.x2} y2={flash.arrow.y2} markerEnd="url(#resource-move-head)" /></svg> : null}
      </div>
      {selection && selectedName ? <aside className="resource-canvas-popover" aria-label="画布选中资源"><header><strong>{selectedName}</strong><button aria-label="关闭资源预览" onClick={onClear}><X size={16} /></button></header>{located.site ? <><p>当前物料：<b>{located.site.materialName || '空库位'}</b></p>{located.site.materialUuid && !located.site.bindings.length ? <p>未绑定 Task</p> : null}{located.site.bindings.map((binding) => <TaskChip key={binding.uuid} uuid={binding.workflow_task_uuid} tasks={context.tasks} onClick={() => context.onOpenTask(binding.workflow_task_uuid)} />)}{located.site.transfers?.map((transfer) => <p key={`${transfer.job.uuid}:${transfer.direction}`} className="resource-warning">{transfer.uncertain ? '转移结果待确认' : transfer.direction === 'in' ? '目标库位，等待实际放入' : '正在转移，保留最近确认位置'}</p>)}</> : <p>{located.device ? `${located.device.sites.length} 个库位 · ${located.device.sites.filter((site) => site.materialUuid).length} 个有料` : '当前未定位到画布库位'}</p>}{located.device && !located.site ? deviceTaskIds(located.device).map((uuid) => <TaskChip key={uuid} uuid={uuid} tasks={context.tasks} onClick={() => context.onOpenTask(uuid)} />) : null}<footer><button onClick={() => onDetails(located.site?.ownerUuid || selection.materialUuid, located.site?.uuid || selection.siteUuid)}>查看详情{located.site ? ' / 上下料' : ''}</button>{located.site?.materialUuid ? <button onClick={() => onDetails(located.site!.materialUuid!)}>查看物料</button> : null}</footer></aside> : null}
      <aside className={`resource-canvas-changes ${changesOpen ? 'open' : ''}`} aria-label="任务物料变化" onPointerDown={(event) => event.stopPropagation()}>
        <header>
          <button className="resource-changes-toggle" aria-expanded={changesOpen} aria-label={`物料变化 ${events.length} 条`} onClick={() => setChangesOpen((open) => !open)}>
            <History size={14} /><b>物料变化</b><em>{events.length}</em>
            {events[0] ? <span className="resource-changes-latest"><strong>{events[0].materialName}</strong>{describeMaterialEvent(events[0])}</span> : <span className="resource-changes-latest">{historyTask ? (history.isError ? '读取失败' : history.isLoading ? '读取中' : '这个任务还没有物料变化') : '暂无运行中的任务'}</span>}
            {events.length ? (changesOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />) : null}
          </button>
          <label title="物料位置变化时自动把画布移到变化处；手动拖动或缩放后暂停 8 秒"><input type="checkbox" checked={follow} onChange={(event) => { setFollow(event.target.checked); lastInteraction.current = 0 }} />跟随变化</label>
        </header>
        {changesOpen && historyTask ? <p className="resource-changes-task"><button onClick={() => context.onOpenTask(historyTask.uuid)} title="打开任务">{historyTask.name} · {historyTask.uuid.slice(0, 8)}</button>{historyTask.running ? '运行中' : ''}</p> : null}
        {changesOpen && events.length ? <ol>{events.slice(0, 60).map((event) => {
          const step = eventStepLabel(event)
          return <li key={event.uuid}><button onClick={() => showEvent(event)}>
            <time>{new Date(event.occurredAt).toLocaleTimeString('zh-CN', { hour12: false })}</time><strong>{event.materialName}</strong>
            <span>{describeMaterialEvent(event)}</span>
            <small className="resource-change-step" title={step.full}>{step.short}</small></button></li>
        })}</ol> : null}
      </aside>
      <div className="resource-canvas-help">拖动空白平移 · 滚轮缩放 · 拖动设备左侧手柄整体移动</div>
      <div className="resource-canvas-controls"><button aria-label="缩小画布" onClick={() => zoom(1 / 1.2)}><Minus size={16} /></button><span>{Math.round(view.scale * 100)}%</span><button aria-label="放大画布" onClick={() => zoom(1.2)}><Plus size={16} /></button><button onClick={fit}><Focus size={15} />适应画布</button><button title="仅重置本浏览器的展示布局" onClick={() => { savePositions({}); onClear(); requestAnimationFrame(fit) }}><RotateCcw size={15} />重置排布</button></div>
    </div>
  </section>
}
