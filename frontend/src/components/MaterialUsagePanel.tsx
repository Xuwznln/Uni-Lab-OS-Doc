import { ArrowUpRight, LockKeyhole, RefreshCw } from 'lucide-react'
import { Button } from './ui'
import { holdingLabel, materialUsage, taskColor, usageNeedsAttention, usageNodeLabel } from '../lib/resourceUsage'
import type { ResourceUsageModel, UsageHolding, UsageSite } from '../lib/resourceUsage'
import type { WorkflowTask } from '../types'
import './MaterialUsagePanel.css'

export interface MaterialUsageContext {
  model?: ResourceUsageModel; tasks: WorkflowTask[]; stale: boolean; loading: boolean; updatedAt: number; error?: string
  refresh: () => void; onOpenTask: (uuid: string) => void; visibleMaterialIds?: Set<string>
}

export function TaskChip({ uuid, tasks, onClick }: { uuid: string; tasks: WorkflowTask[]; onClick?: () => void }) {
  const name = tasks.find((task) => task.uuid === uuid)?.workflowName || 'Task'
  return <button type="button" className="resource-task-chip" style={{ borderColor: taskColor(uuid), color: taskColor(uuid) }} title={`${name} · ${uuid}`} onClick={onClick}><i style={{ backgroundColor: taskColor(uuid) }} />{name} · {uuid.slice(0, 8)}</button>
}

function Holding({ holding, context }: { holding: UsageHolding; context: MaterialUsageContext }) {
  const sourceNode = context.model?.nodes.find((node) => node.material.uuid === holding.resourceUuid)
  const source = sourceNode ? usageNodeLabel(sourceNode).primary : holding.lockKey
  return <article className={`resource-holding ${usageNeedsAttention(holding) ? 'attention' : ''}`} style={{ borderLeftColor: taskColor(holding.taskUuid) }}>
    <div><strong>{source}</strong><span>{holdingLabel(holding)}</span></div>
    <TaskChip uuid={holding.taskUuid} tasks={context.tasks} onClick={() => context.onOpenTask(holding.taskUuid)} />
    <small>节点：{holding.nodeName}</small>
    {holding.since ? <small>占用开始：{new Date(holding.since).toLocaleString('zh-CN', { hour12: false })}</small> : null}
    <div><Button icon={<ArrowUpRight size={13} />} onClick={() => context.onOpenTask(holding.taskUuid)}>查看任务与执行锁</Button>{holding.task?.trace ? <a href={holding.task.trace.url} target="_blank" rel="noreferrer">Trace ↗</a> : null}</div>
  </article>
}

export function SiteUsageSummary({ site, context }: { site?: UsageSite; context: MaterialUsageContext }) {
  if (!site) return <small className="resource-unbound">调度占用尚未读取</small>
  const taskIds = [...new Set([...site.bindings.map((binding) => binding.workflow_task_uuid), ...site.holdings.map((holding) => holding.taskUuid)])]
  return <span className="site-usage-summary">
    {taskIds.map((uuid) => <span key={uuid} className="site-task-label" title={uuid} style={{ color: taskColor(uuid) }}><i style={{ backgroundColor: taskColor(uuid) }} />Task {uuid.slice(0, 8)}</span>)}
    {!taskIds.length ? <small className="resource-unbound">未绑定 Task</small> : null}
    {site.holdings.length ? <small className={site.holdings.some(usageNeedsAttention) ? 'resource-warning' : ''}><LockKeyhole size={12} />{[...new Set(site.holdings.map(holdingLabel))].join(' · ')}</small> : null}
    {site.planned.map((planned) => <small className="resource-planned" key={planned.holding.id}>计划放入：{planned.materialName}</small>)}
  </span>
}

export function MaterialUsagePanel({ materialUuid, context, onSelectSite }: { materialUuid: string; context: MaterialUsageContext; onSelectSite: (owner: string, site: string) => void }) {
  const facts = context.model ? materialUsage(context.model, materialUuid) : undefined
  const directBindings = facts?.bindings.filter((binding) => binding.material_uuid === materialUuid) || []
  const node = context.model?.nodes.find((node) => node.material.uuid === materialUuid)
  const isDevice = node?.material.type === 'device' || node?.resource_template?.resource_type === 'device'
  const activities = context.model?.devices.find((device) => device.uuid === materialUuid)?.activities || []
  return <section className="material-live-usage" aria-label="当前资源使用情况">
    <header><div><strong>当前使用情况</strong><small>{context.stale ? '最后一次快照，当前状态可能已变化' : context.model ? '任务变化实时刷新 · 每 5 秒补刷' : '正在读取调度占用'}{context.updatedAt ? ` · ${new Date(context.updatedAt).toLocaleTimeString('zh-CN', { hour12: false })}` : ''}</small></div><Button icon={<RefreshCw size={13} />} disabled={context.loading} onClick={context.refresh}>刷新占用</Button></header>
    {context.error ? <p role="alert" className="resource-warning">{context.error}。{context.model ? '已保留上次成功数据。' : '尚不能判断是否存在占用。'}</p> : null}
    {!isDevice && context.model ? <div className="resource-binding-line"><strong>物料任务绑定</strong>{directBindings.length ? directBindings.map((binding) => <TaskChip key={binding.uuid} uuid={binding.workflow_task_uuid} tasks={context.tasks} onClick={() => context.onOpenTask(binding.workflow_task_uuid)} />) : <span>未绑定 Task，可作为初始库存存放。</span>}</div> : null}
    {activities.map(({ task, node: action }) => <div className="resource-binding-line" key={`${task.uuid}:${action.uuid}`}><strong>{action.name} · {action.status === 'canceling' ? '取消中' : '执行中'}</strong><TaskChip uuid={task.uuid} tasks={context.tasks} onClick={() => context.onOpenTask(task.uuid)} /></div>)}
    {facts?.holdings.length ? <div className="resource-holding-grid">{facts.holdings.map((holding) => <Holding key={holding.id} holding={holding} context={context} />)}</div> : context.model ? <p className="resource-unbound">当前没有调度占用。物料存放状态与执行锁分别记录。</p> : null}
    {isDevice && facts?.sites.length ? <div className="resource-descendant-sites"><h4>设备下属库位（{facts.sites.length}）</h4><div>{facts.sites.map((site) => <button key={site.uuid} type="button" className={`resource-site-card ${site.holdings.some(usageNeedsAttention) ? 'attention' : ''} ${site.planned.length ? 'reserved' : ''}`} style={{ borderLeftColor: taskColor(site.holdings[0]?.taskUuid || site.bindings[0]?.workflow_task_uuid || '') }} onClick={() => onSelectSite(site.ownerUuid, site.uuid)}><strong>{site.ownerName} / {site.name}</strong><span>{site.materialName || '空库位'}</span><SiteUsageSummary site={site} context={context} /></button>)}</div></div> : null}
  </section>
}

export function SelectedSiteUsage({ site, context }: { site?: UsageSite; context: MaterialUsageContext }) {
  if (!site) return null
  return <section className="selected-site-usage" aria-label="库位任务关联"><h4>{site.name} · 当前存放：{site.materialName || '空库位'}</h4>
    <div className="resource-binding-line"><strong>当前物料所属任务</strong>{site.bindings.length ? site.bindings.map((binding) => <TaskChip key={binding.uuid} uuid={binding.workflow_task_uuid} tasks={context.tasks} onClick={() => context.onOpenTask(binding.workflow_task_uuid)} />) : <span>{site.materialUuid ? '未绑定 Task' : '库位为空'}</span>}</div>
    <div className="resource-holding-grid">{site.holdings.map((holding) => <Holding key={holding.id} holding={holding} context={context} />)}</div>
    {site.planned.map((planned) => <div className="resource-binding-line resource-planned" key={planned.holding.id}>计划放入：{planned.materialName}<TaskChip uuid={planned.holding.taskUuid} tasks={context.tasks} onClick={() => context.onOpenTask(planned.holding.taskUuid)} /></div>)}
    {!site.holdings.length ? <p className="resource-unbound">该库位没有调度占用。</p> : null}
  </section>
}
