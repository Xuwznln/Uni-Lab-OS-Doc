import { adaptMaterial, EDGE_API_BASE, unwrapEnvelope } from './edgeClient'
import type { ResourceOccupancy } from './stationErrorClient'
import type { MaterialRecord, TaskNode, WorkflowTask } from '../types'
import { materialLabel } from './materialLabel'
import type { MaterialLabel } from './materialLabel'

export interface ResourcePose {
  position_x?: number; position_y?: number; position_z?: number
  width?: number; length?: number; depth?: number
  rotation_x?: number; rotation_y?: number; rotation_z?: number
  scale_x?: number; scale_y?: number; scale_z?: number
}
export interface UsageGraphNode {
  material: {
    uuid: string; name: string; type?: string; parent_uuid?: string | null; barcode?: string | null
    resource_template_uuid?: string | null
    meta_data?: { source_node_id?: string; source_runtime_uuid?: string }
  }
  resource_template?: { resource_type?: string; display_name?: string; name?: string; uuid?: string }
  relative_position?: ResourcePose | null
  current_site_uuid?: string | null
  sites: Array<ResourcePose & { uuid: string; name: string; sort_order?: number; occupied_material_uuid?: string | null; allowed_resource_template_uuids?: string[] }>
}
export interface UsageJob {
  uuid: string; task_uuid: string; task_name: string; task_status: string; node_uuid: string; node_name: string
  status: string; device_id?: string; material_uuid?: string
  expected_change_set: { kind?: string; material_uuid?: string; source_site_uuid?: string; target_site_uuid?: string }
}
export interface UsageBinding { uuid: string; material_uuid: string; workflow_task_uuid: string; workflow_node_job_uuid: string; status: string }
export interface ResourceUsageSnapshot { nodes: UsageGraphNode[]; occupancies: ResourceOccupancy[]; material_bindings?: UsageBinding[]; jobs?: UsageJob[] }
export interface UsageHolding {
  id: string; taskUuid: string; task?: WorkflowTask; nodeName: string; jobUuid?: string
  state: string; kind: string; since?: string; retained: boolean; lockKey: string
  resourceUuid?: string; source?: UsageJob
}
export interface UsageSite {
  uuid: string; name: string; ownerUuid: string; ownerName: string
  materialUuid?: string; materialName?: string; holdings: UsageHolding[]
  bindings: UsageBinding[]; planned: Array<{ materialUuid: string; materialName: string; holding: UsageHolding }>
  pose?: ResourcePose; sortOrder?: number
  transfers?: Array<{ direction: 'in' | 'out'; job: UsageJob; uncertain: boolean }>
}
export interface UsageDevice {
  uuid: string; name: string; label: MaterialLabel; sourceId?: string; isDevice: boolean
  sites: UsageSite[]; holdings: UsageHolding[]; activities: Array<{ task: WorkflowTask; node: TaskNode }>
  executions?: UsageJob[]
}
export interface ResourceUsageModel { devices: UsageDevice[]; unmatched: UsageHolding[]; holdingCount: number; holdings: UsageHolding[]; bindings: UsageBinding[]; sites: Map<string, UsageSite>; nodes: UsageGraphNode[] }

/** 独立刷新库存图和持久锁；任一来源失败时保留上一份完整快照。 */
export async function loadResourceUsage(signal?: AbortSignal): Promise<ResourceUsageSnapshot> {
  const controller = new AbortController()
  const abort = () => controller.abort()
  if (signal?.aborted) abort()
  signal?.addEventListener('abort', abort, { once: true })
  // 全流程运行时占用接口可能要 7～10 秒；超时过短会让画布频繁退回只读快照。
  const timer = window.setTimeout(abort, 20_000)
  try {
    const [graph, occupancies] = await Promise.all([
      fetch(`${EDGE_API_BASE}/materials/graph`, { signal: controller.signal, headers: { Accept: 'application/json' } }).then(async (response) => {
        if (!response.ok) throw new Error(`设备与库位读取失败（${response.status}）`)
        return unwrapEnvelope<{ nodes: UsageGraphNode[] }>(await response.json())
      }),
      fetch(`${EDGE_API_BASE}/stations/local/resource-usage`, { signal: controller.signal, headers: { Accept: 'application/json' } }).then(async (response) => {
        if (!response.ok) throw new Error(`任务占用读取失败（${response.status}）`)
        return unwrapEnvelope<Omit<ResourceUsageSnapshot, 'nodes'>>(await response.json())
      }),
    ])
    if (!Array.isArray(graph.nodes) || !Array.isArray(occupancies.occupancies) || !Array.isArray(occupancies.material_bindings) || !Array.isArray(occupancies.jobs)) throw new Error('设备与库位数据格式不完整')
    // 转运可能恰好发生在读取过程中；拒绝相互矛盾的位置，防止同一个烧杯同时亮在两个库位。
    const sites = new Map(graph.nodes.flatMap((node) => node.sites).map((site) => [site.uuid, site]))
    const placements = new Map<string, string>()
    for (const site of sites.values()) {
      if (!site.occupied_material_uuid) continue
      if (placements.has(site.occupied_material_uuid)) throw new Error('物料位置正在更新，等待一致的库位快照')
      placements.set(site.occupied_material_uuid, site.uuid)
    }
    for (const node of graph.nodes) {
      if (node.current_site_uuid === undefined) continue
      const declared = placements.get(node.material.uuid)
      if (declared && declared !== node.current_site_uuid || node.current_site_uuid && sites.get(node.current_site_uuid)?.occupied_material_uuid !== node.material.uuid) throw new Error('物料位置正在更新，等待一致的库位快照')
    }
    return { nodes: graph.nodes, ...occupancies }
  } catch (error) {
    if (controller.signal.aborted && !signal?.aborted) throw new Error('资源读取超时，请稍后刷新')
    throw error
  } finally {
    window.clearTimeout(timer)
    signal?.removeEventListener('abort', abort)
    controller.abort()
  }
}

const terminal = new Set(['succeeded', 'failed', 'canceled', 'timeout'])
const isDevice = (node: UsageGraphNode) => node.material.type === 'device' || node.resource_template?.resource_type === 'device'

/** 展示模型只匹配持久资源身份，不根据任务状态推测锁是否已经释放。 */
export function buildResourceUsage(snapshot: ResourceUsageSnapshot, tasks: WorkflowTask[]): ResourceUsageModel {
  const nodes = new Map(snapshot.nodes.map((node) => [node.material.uuid, node]))
  // 作为上下文出现的物料（库位归属、占用物、计划转运）只用主行指认；两行文本留给它自己是主体的地方。
  const labelText = (uuid: string | undefined, fallback?: string) => {
    const node = uuid ? nodes.get(uuid) : undefined
    return node ? usageNodeLabel(node).primary : fallback
  }
  const locationParents = new Map(snapshot.nodes.flatMap((node) => node.sites.filter((site) => site.occupied_material_uuid).map((site) => [site.occupied_material_uuid!, node.material.uuid] as const)))
  const identities = new Map<string, string>()
  for (const { material } of snapshot.nodes) {
    for (const identity of [material.uuid, material.meta_data?.source_node_id, material.meta_data?.source_runtime_uuid]) {
      if (identity) identities.set(identity, material.uuid)
    }
  }
  const groupFor = (uuid: string): UsageGraphNode | undefined => {
    let node = nodes.get(uuid)
    let fallback = node
    let siteOwner: UsageGraphNode | undefined
    const visited = new Set<string>()
    while (node && !visited.has(node.material.uuid)) {
      visited.add(node.material.uuid)
      if (isDevice(node)) return node
      if (node.sites.length) siteOwner = node
      fallback = node
      node = nodes.get(locationParents.get(node.material.uuid) || node.material.parent_uuid || '')
    }
    return siteOwner || fallback
  }
  const groups = new Map<string, UsageDevice>()
  const ensureGroup = (node: UsageGraphNode) => {
    const uuid = node.material.uuid
    let group = groups.get(uuid)
    if (!group) {
      const label = usageNodeLabel(node)
      group = { uuid, name: label.primary, label, sourceId: node.material.meta_data?.source_node_id,
        isDevice: isDevice(node), sites: [], holdings: [], activities: [], executions: [] }
      groups.set(uuid, group)
    }
    return group
  }
  const occupants = new Map(snapshot.nodes.filter((node) => node.current_site_uuid).map((node) => [node.current_site_uuid!, node.material]))
  const bindings = (snapshot.material_bindings || []).filter((binding) => binding.status === 'active')
  const sites = new Map<string, UsageSite>()
  for (const node of snapshot.nodes) {
    if (isDevice(node)) ensureGroup(node)
    if (!node.sites.length) continue
    const group = ensureGroup(groupFor(node.material.uuid) || node)
    for (const site of [...node.sites].sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0))) {
      const occupant = occupants.get(site.uuid)
      const materialUuid = site.occupied_material_uuid || occupant?.uuid || undefined
      const entry: UsageSite = { uuid: site.uuid, name: site.name, ownerUuid: node.material.uuid, ownerName: usageNodeLabel(node).primary,
        materialUuid, materialName: materialUuid ? labelText(materialUuid, occupant?.name || materialUuid) : undefined, holdings: [], bindings: bindings.filter((binding) => binding.material_uuid === materialUuid), planned: [], pose: site, sortOrder: site.sort_order, transfers: [] }
      sites.set(site.uuid, entry)
      group.sites.push(entry)
    }
  }
  const taskMap = new Map(tasks.map((task) => [task.uuid, task]))
  const unmatched: UsageHolding[] = []
  const holdings: UsageHolding[] = []
  const jobs = new Map(snapshot.jobs?.map((job) => [job.uuid, job]))
  const active = snapshot.occupancies.filter((item) => item.state !== 'released')
  for (const item of active) {
    const taskUuid = item.workflow_task_uuid || item.task_uuid || ''
    const task = taskMap.get(taskUuid)
    const jobUuid = item.workflow_node_job_uuid
    const source = jobs.get(jobUuid || '')
    const node = task?.nodes.find((candidate) => candidate.job?.uuid === jobUuid)
    const holding: UsageHolding = { id: item.uuid, taskUuid, task, nodeName: source?.node_name || node?.name || jobUuid || '来源节点待解析', jobUuid, source,
      state: item.state, kind: item.occupancy_kind, since: item.create_time, retained: terminal.has(source?.task_status || task?.executionStatus || task?.status || ''), lockKey: item.lock_key }
    holdings.push(holding)
    // 设备锁、整物料锁、单库位锁的范围不同；设备锁不伪造成全部库位被锁。
    const device = /^\/devices\/([^/\s]+)$/.exec(item.lock_key)
    const material = /^material\/([^/\s]+)\/exclusive$/.exec(item.lock_key)
    const site = /^material\/([^/\s]+)\/site\/([^/\s]+)\/exclusive$/.exec(item.lock_key)
    const identity = device?.[1] || material?.[1] || site?.[1]
    const uuid = identity ? identities.get(identity) : undefined
    holding.resourceUuid = uuid
    const owner = uuid ? groupFor(uuid) : undefined
    if (!owner || (site && sites.get(site[2])?.ownerUuid !== uuid)) { unmatched.push(holding); continue }
    const group = ensureGroup(owner)
    group.holdings.push(holding)
    if (site) {
      const entry = sites.get(site[2])!
      entry.holdings.push(holding)
      const change = source?.expected_change_set
      if (change?.kind === 'material_transfer' && change.target_site_uuid === entry.uuid && change.material_uuid && change.material_uuid !== entry.materialUuid) {
        entry.planned.push({ materialUuid: change.material_uuid, materialName: labelText(change.material_uuid, change.material_uuid)!, holding })
      }
    }
    if (material) {
      // 与调度器一致：整物料独占覆盖其直接库位，不跨层推断其它资源锁。
      for (const entry of group.sites) if (entry.ownerUuid === uuid) entry.holdings.push(holding)
    }
  }
  for (const job of snapshot.jobs || []) {
    const executing = ['dispatched', 'running', 'cancel_requested'].includes(job.status)
    const uncertain = holdings.some((holding) => holding.jobUuid === job.uuid && usageNeedsAttention(holding))
    if (executing) {
      const identity = identities.get(job.device_id || '') || identities.get(job.material_uuid || '')
      const owner = identity ? groupFor(identity) : undefined
      if (owner) ensureGroup(owner).executions!.push(job)
    }
    if ((!executing && !uncertain) || job.expected_change_set?.kind !== 'material_transfer') continue
    const change = job.expected_change_set
    if (change.source_site_uuid) sites.get(change.source_site_uuid)?.transfers!.push({ direction: 'out', job, uncertain })
    if (change.target_site_uuid) sites.get(change.target_site_uuid)?.transfers!.push({ direction: 'in', job, uncertain })
  }
  for (const task of tasks) {
    for (const node of task.nodes) {
      if (!['running', 'canceling'].includes(node.status)) continue
      if (snapshot.jobs && !snapshot.jobs.some((job) => job.uuid === node.job?.uuid && ['dispatched', 'running', 'cancel_requested'].includes(job.status))) continue
      const identity = identities.get(node.device || '') || identities.get(node.materialUuid || '')
      const owner = identity ? groupFor(identity) : undefined
      if (owner) ensureGroup(owner).activities.push({ task, node })
    }
  }
  return { devices: [...groups.values()].sort((a, b) => Number(b.isDevice) - Number(a.isDevice) || a.name.localeCompare(b.name, 'zh-CN', { numeric: true })), unmatched, holdingCount: active.length, holdings, bindings, sites, nodes: snapshot.nodes }
}

/** 任务颜色由稳定身份决定；刷新、排序与页面切换都不会改变。 */
export function taskColor(uuid: string): string {
  if (!uuid) return '#94a0b2'
  let hash = 0
  for (const char of uuid) hash = (Math.imul(hash, 31) + char.charCodeAt(0)) >>> 0
  return `hsl(${hash % 360} 58% 42%)`
}

export function materialUsage(model: ResourceUsageModel, uuid: string) {
  const descendants = new Set([uuid])
  const locationParents = new Map([...model.sites.values()].filter((site) => site.materialUuid).map((site) => [site.materialUuid!, site.ownerUuid]))
  let changed = true
  while (changed) {
    changed = false
    for (const node of model.nodes) {
      const parent = locationParents.get(node.material.uuid) || node.material.parent_uuid
      if (parent && descendants.has(parent) && !descendants.has(node.material.uuid)) { descendants.add(node.material.uuid); changed = true }
    }
  }
  return { holdings: model.holdings.filter((holding) => holding.resourceUuid && descendants.has(holding.resourceUuid)),
    bindings: model.bindings.filter((binding) => descendants.has(binding.material_uuid)),
    activities: model.devices.filter((device) => descendants.has(device.uuid)).flatMap((device) => device.activities),
    sites: [...model.sites.values()].filter((site) => descendants.has(site.ownerUuid)) }
}

/** 快照节点的两行展示标签：主行条码（name 可读时用 name）、副行模板显示名。 */
export function usageNodeLabel(node: UsageGraphNode): MaterialLabel {
  return materialLabel(node.material, node.resource_template?.display_name)
}

/** 刷新物料树的当前位置；不把计划转运或历史任务输入当作实际位置。 */
export function materialsFromUsage(snapshot: ResourceUsageSnapshot): MaterialRecord[] {
  const model = buildResourceUsage(snapshot, [])
  return snapshot.nodes.map((node) => {
    const site = [...model.sites.values()].find((entry) => entry.materialUuid === node.material.uuid)
    const location: MaterialRecord['currentLocation'] = site
      ? { kind: 'site', label: `${site.ownerName} / ${site.name}`, siteUuid: site.uuid, ownerMaterialUuid: site.ownerUuid }
      : node.current_site_uuid ? { kind: 'unresolved', label: '所在库位待解析', siteUuid: node.current_site_uuid }
      : node.sites.length ? { kind: 'structural', label: '提供库位', siteCount: node.sites.length }
      : { kind: 'unassigned', label: '未分配权威库位' }
    return { ...adaptMaterial({ ...node.material, parent_uuid: site?.ownerUuid || node.material.parent_uuid, relative_position: node.relative_position, resource_template: node.resource_template }, location), isStructural: node.sites.length > 0, siteCount: node.sites.length,
      sites: node.sites.map((raw) => { const entry = model.sites.get(raw.uuid)!; return { uuid: entry.uuid, name: entry.name, occupiedMaterialUuid: entry.materialUuid, occupiedMaterialName: entry.materialName, allowedResourceTemplateUuids: raw.allowed_resource_template_uuids } }) }
  })
}

export function holdingLabel(holding: UsageHolding): string {
  if (holding.retained) return '遗留占用'
  if (holding.state === 'uncertain') return '结果待确认'
  if (holding.state === 'reserved') return '已预留'
  if (holding.kind === 'device_tenancy') return '持续占用'
  return '执行占用'
}

export function usageNeedsAttention(holding: UsageHolding): boolean { return holding.retained || holding.state === 'uncertain' }
