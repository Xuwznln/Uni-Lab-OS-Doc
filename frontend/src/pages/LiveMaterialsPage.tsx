import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ResourceHoverCard } from '../components/ResourceHoverCard'
import type { ResourceHover } from '../components/ResourceHoverCard'
import { Boxes, RefreshCw, X } from 'lucide-react'
import { MaterialsPage } from './MaterialsPage'
import { MaterialHierarchyTree } from '../components/MaterialHierarchyTree'
import { MaterialResourceCanvas } from '../components/MaterialResourceCanvas'
import type { CanvasSelection } from '../components/MaterialResourceCanvas'
import type { MaterialUsageContext } from '../components/MaterialUsagePanel'
import { useResourceUsage } from '../hooks/useResourceUsage'
import { buildResourceUsage, materialsFromUsage, materialUsage, taskColor, usageNeedsAttention } from '../lib/resourceUsage'
import type { MaterialRecord, WorkflowTask } from '../types'
import '../components/MaterialResourceCanvas.css'

export function LiveMaterialsPage({ materials, tasks, connected, onNotify, onRefresh, onOpenTask, targetTaskUuid, hoverDelayMs = 400 }: {
  materials: MaterialRecord[]; tasks: WorkflowTask[]; connected: boolean; onNotify: (message: string) => void
  onRefresh: () => void; onOpenTask: (uuid: string) => void; targetTaskUuid?: string
  /** 速览卡出现前的停留时间（毫秒）；测试可置 0。 */
  hoverDelayMs?: number
}) {
  const query = useResourceUsage(true)
  const [taskFilter, setTaskFilter] = useState(targetTaskUuid || '')
  const [filter, setFilter] = useState('all')
  const [selection, setSelection] = useState<CanvasSelection>()
  const [detail, setDetail] = useState<{ materialUuid: string; siteUuid?: string }>()
  const [hover, setHover] = useState<ResourceHover>()
  const hoverTimer = useRef<number | undefined>(undefined)
  const drawer = useRef<HTMLDialogElement>(null)
  const canvasLayout = useRef<HTMLDivElement>(null)
  const sizeCanvas = () => {
    const element = canvasLayout.current
    if (!element) return
    // 使用未滚动的页面位置并扣除外层留白，避免悬浮重渲染触发页面高度反复变化。
    let top = element.getBoundingClientRect().top
    let bottom = 0
    let parent = element.parentElement
    while (parent) {
      top += parent.scrollTop
      if (parent.matches('.material-canvas-page, .page-content')) bottom += parseFloat(getComputedStyle(parent).paddingBottom) || 0
      parent = parent.parentElement
    }
    element.style.height = `${Math.max(420, window.innerHeight - top - bottom)}px`
  }
  useLayoutEffect(sizeCanvas)
  useEffect(() => { window.addEventListener('resize', sizeCanvas); return () => window.removeEventListener('resize', sizeCanvas) }, [])
  const model = useMemo(() => query.data ? buildResourceUsage(query.data, tasks) : undefined, [query.data, tasks])
  // "物料变化"面板看哪个任务：选了任务筛选就看它；否则看正在运行的任务，没有运行中的就看最近更新的一个。
  const historyTask = useMemo(() => {
    const byUpdate = [...tasks].sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)))
    const task = (taskFilter && tasks.find((entry) => entry.uuid === taskFilter))
      || byUpdate.find((entry) => entry.status === 'running') || byUpdate[0]
    return task ? { uuid: task.uuid, name: task.workflowName, running: task.status === 'running' } : undefined
  }, [tasks, taskFilter])
  const liveMaterials = useMemo(() => query.data ? materialsFromUsage(query.data) : materials, [query.data, materials])
  const visibleIds = useMemo(() => {
    if (!model || (!taskFilter && filter === 'all')) return undefined
    return new Set(liveMaterials.filter((material) => {
      const facts = materialUsage(model, material.uuid)
      const stored = facts.sites.some((site) => site.materialUuid) || [...model.sites.values()].some((site) => site.materialUuid === material.uuid)
      const matchesTask = !taskFilter || facts.holdings.some((holding) => holding.taskUuid === taskFilter) || facts.bindings.some((binding) => binding.workflow_task_uuid === taskFilter) || facts.activities.some(({ task }) => task.uuid === taskFilter) || model.devices.find((device) => device.uuid === material.uuid)?.executions?.some((job) => job.task_uuid === taskFilter)
      return matchesTask && (filter !== 'occupied' || stored || facts.holdings.length || facts.activities.length) && (filter !== 'attention' || facts.holdings.some(usageNeedsAttention))
    }).map((material) => material.uuid))
  }, [model, liveMaterials, taskFilter, filter])
  const refresh = () => { void query.refetch().catch(() => undefined); onRefresh() }
  const context: MaterialUsageContext = { model, tasks, stale: query.isError || !connected, loading: query.isFetching, updatedAt: query.dataUpdatedAt, error: query.isError ? query.error.message : undefined, refresh, onOpenTask }
  // 速览卡只在画布上触发（列表树行已显示两行标签），停留一段时间才出现，移开立即消失。
  function clearHover() {
    if (hoverTimer.current !== undefined) { window.clearTimeout(hoverTimer.current); hoverTimer.current = undefined }
    setHover(undefined)
  }
  function hoverResource(target: EventTarget, immediate = false) {
    const element = (target as HTMLElement).closest<HTMLElement>('[data-usage-material], [data-usage-site]')
    if (!element || element.closest('.electron-material-tree')) { clearHover(); return }
    const rect = element.getBoundingClientRect()
    const next: ResourceHover = { materialUuid: element.dataset.usageMaterial, siteUuid: element.dataset.usageSite, left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom }
    if (hoverTimer.current !== undefined) window.clearTimeout(hoverTimer.current)
    if (immediate || hoverDelayMs <= 0) { hoverTimer.current = undefined; setHover(next); return }
    setHover(undefined)
    hoverTimer.current = window.setTimeout(() => { hoverTimer.current = undefined; setHover(next) }, hoverDelayMs)
  }
  useEffect(() => () => { if (hoverTimer.current !== undefined) window.clearTimeout(hoverTimer.current) }, [])
  function select(materialUuid: string, siteUuid?: string) {
    setSelection((current) => ({ materialUuid, siteUuid, request: (current?.request || 0) + 1 }))
  }
  useEffect(() => { if (detail) drawer.current?.showModal?.() }, [detail])
  // 从任务页进入时只定位一次，避免每次补刷抢走用户正在查看的位置。
  const targetLocated = useRef(false)
  useEffect(() => {
    if (!targetTaskUuid || targetLocated.current || !visibleIds?.size || !model) return
    const device = model.devices.find((entry) => visibleIds.has(entry.uuid) || entry.sites.some((site) => visibleIds.has(site.ownerUuid) || Boolean(site.materialUuid && visibleIds.has(site.materialUuid))))
    if (device) { targetLocated.current = true; select(device.uuid) }
  }, [targetTaskUuid, visibleIds, model])

  return <div className="material-canvas-page">
    <header className="material-canvas-page-header"><div><h1>设备与库位</h1><p>查看设备使用与物料位置 · 设备和库位整体移动</p></div><div className="material-canvas-actions"><select aria-label="筛选占用任务" value={taskFilter} onChange={(event) => { setTaskFilter(event.target.value); setSelection(undefined) }}><option value="">全部 Task（含未绑定库存）</option>{tasks.map((task) => <option value={task.uuid} key={task.uuid}>{task.workflowName} · {task.uuid.slice(0, 8)}</option>)}</select><select aria-label="筛选资源状态" value={filter} onChange={(event) => { setFilter(event.target.value); setSelection(undefined) }}><option value="all">全部资源</option><option value="occupied">有料 / 使用中</option><option value="attention">遗留占用 / 待确认</option></select>{taskFilter || filter !== 'all' ? <button onClick={() => { setTaskFilter(''); setFilter('all') }}>清除筛选</button> : null}<button onClick={() => setDetail({ materialUuid: selection?.materialUuid || liveMaterials[0]?.uuid || '' })}><Boxes size={15} />物料管理</button><button disabled={query.isFetching} onClick={refresh}><RefreshCw size={15} />刷新</button></div></header>
    <div className={`material-canvas-freshness ${context.stale ? 'stale' : ''}`} role={context.stale ? 'status' : undefined}><i />{context.stale ? '只读快照 · 当前占用可能已变化' : model ? '实时更新 · 任务事件触发，每 5 秒补刷' : '正在读取设备与库位'}{query.dataUpdatedAt ? <span>最后成功更新 {new Date(query.dataUpdatedAt).toLocaleTimeString('zh-CN', { hour12: false })}</span> : null}{context.error ? <span>{context.error}，已保留上次成功数据</span> : null}</div>
    <div className="material-canvas-layout" ref={canvasLayout} onMouseOver={(event) => hoverResource(event.target)} onMouseLeave={clearHover} onFocus={(event) => hoverResource(event.target, true)} onBlur={clearHover} onPointerDownCapture={clearHover}>
      <MaterialHierarchyTree materials={liveMaterials} selectedId={selection?.materialUuid} onSelect={(uuid) => select(uuid)} onSelectSite={select} visibleMaterialIds={visibleIds}
        renderBadge={model ? (uuid) => { const site = model.sites.get(uuid); const facts = site || materialUsage(model, uuid); const ids = [...new Set([...facts.holdings.map((holding) => holding.taskUuid), ...facts.bindings.map((binding) => binding.workflow_task_uuid)])]; return <span className="resource-tree-colors">{ids.filter(Boolean).slice(0, 3).map((id) => <i key={id} title={`Task ${id}`} style={{ backgroundColor: taskColor(id) }} />)}</span> } : undefined} />
      {model ? <MaterialResourceCanvas model={model} context={context} selection={selection} onSelect={select} onClear={() => setSelection(undefined)} onDetails={(materialUuid, siteUuid) => setDetail({ materialUuid, siteUuid })} visibleIds={visibleIds} historyTask={historyTask} /> : <div className="resource-canvas-loading"><Boxes size={32} /><strong>{query.isError ? '暂时无法读取画布' : '正在加载设备与库位…'}</strong><p>{query.isError ? query.error.message : '占用数据读取完成后展示，当前不推断资源为空。'}</p>{liveMaterials.length ? <button onClick={() => setDetail({ materialUuid: selection?.materialUuid || liveMaterials[0].uuid })}>查看物料详情</button> : null}</div>}
    </div>
    {model?.unmatched.length ? <section className="resource-unmatched" aria-label="未匹配资源占用"><strong>还有 {model.unmatched.length} 项占用未匹配到物料树</strong>{model.unmatched.map((holding) => <p key={holding.id}><code>{holding.lockKey}</code> · <button onClick={() => onOpenTask(holding.taskUuid)}>查看来源 Task {holding.taskUuid.slice(0, 8)}</button></p>)}</section> : null}
    {!detail ? <ResourceHoverCard hover={hover} context={context} /> : null}
    {detail ? <dialog ref={drawer} className="material-canvas-drawer" aria-label="物料与库位详情" onCancel={() => setDetail(undefined)} onClick={(event) => { if (event.target === event.currentTarget) setDetail(undefined) }}><div className="material-drawer-content"><header><div><strong>物料与库位详情</strong><small>实际上下料会更新占用，画布拖动只调整展示位置</small></div><button aria-label="关闭物料详情" onClick={() => setDetail(undefined)}><X size={20} /></button></header><MaterialsPage key={`${detail.materialUuid}:${detail.siteUuid || ''}`} materials={liveMaterials} total={liveMaterials.length} connected={connected && !query.isError} onNotify={onNotify} onRefresh={refresh} targetMaterialUuid={detail.materialUuid} targetSiteUuid={detail.siteUuid} detailOnly usage={context} /></div></dialog> : null}
  </div>
}
