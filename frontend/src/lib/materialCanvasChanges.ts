import type { ResourceUsageModel } from './resourceUsage'
import { usageNodeLabel } from './resourceUsage'
import type { TaskMaterialEvent } from './edgeClient'

export interface CanvasSiteRef { siteUuid: string; siteName: string; ownerName: string }
/** 两次快照之间一件物料的库位变化；只有来源或只有目标时表示移出画布或新放入。 */
export interface CanvasChange {
  id: string; materialUuid: string; materialName: string
  from?: CanvasSiteRef; to?: CanvasSiteRef; at: number
}

function siteRef(model: ResourceUsageModel, siteUuid: string): CanvasSiteRef {
  const site = model.sites.get(siteUuid)!
  return { siteUuid, siteName: site.name, ownerName: site.ownerName }
}

/**
 * 比较前后两次库存快照的库位占用，按物料归并为"从哪到哪"。
 *
 * 参数：``previous``、``next`` 是相邻两次构建的画布模型，``at`` 是本次检测时间。
 * 返回：按目标库位排序的变化列表；占用未变时为空数组。只读比较，不推断未确认的转运。
 */
export function diffSiteOccupancy(previous: ResourceUsageModel, next: ResourceUsageModel, at = Date.now()): CanvasChange[] {
  const left = new Map<string, string>(), arrived = new Map<string, string>()
  for (const [uuid, site] of previous.sites) {
    const now = next.sites.get(uuid)?.materialUuid
    if (site.materialUuid && site.materialUuid !== now) left.set(site.materialUuid, uuid)
  }
  for (const [uuid, site] of next.sites) {
    const before = previous.sites.get(uuid)?.materialUuid
    if (site.materialUuid && site.materialUuid !== before) arrived.set(site.materialUuid, uuid)
  }
  const name = (materialUuid: string) => {
    const node = next.nodes.find((entry) => entry.material.uuid === materialUuid)
      || previous.nodes.find((entry) => entry.material.uuid === materialUuid)
    return node ? usageNodeLabel(node).primary : materialUuid.slice(0, 8)
  }
  const materials = [...new Set([...arrived.keys(), ...left.keys()])]
  return materials.map((materialUuid) => {
    const from = left.get(materialUuid), to = arrived.get(materialUuid)
    return {
      id: `${at}:${materialUuid}`, materialUuid, materialName: name(materialUuid), at,
      ...(from && previous.sites.has(from) ? { from: siteRef(previous, from) } : {}),
      ...(to ? { to: siteRef(next, to) } : {}),
    }
  }).sort((a, b) => Number(Boolean(b.to)) - Number(Boolean(a.to)) || a.materialName.localeCompare(b.materialName, 'zh-CN', { numeric: true }))
}

export function describeSite(ref?: CanvasSiteRef): string {
  return ref ? `${ref.ownerName} / ${ref.siteName}` : '画布外'
}


function formatQuantity(value?: number, unit?: string): string {
  if (value == null || !Number.isFinite(value)) return ''
  const rounded = Math.round(value * 1000) / 1000
  return `${rounded}${unit ? ` ${unit}` : ''}`
}

/** 一条物料变化的一句话描述：搬运写"从哪到哪"，消耗写用量与余量，已使用写原因。 */
export function describeMaterialEvent(event: TaskMaterialEvent): string {
  if (event.kind === 'moved') return `${event.fromSite || '画布外'} → ${event.toSite || '画布外'}`
  if (event.kind === 'consumed') {
    const used = formatQuantity(event.quantity, event.quantityUnit)
    const left = formatQuantity(event.balanceAfter, event.quantityUnit)
    return [used ? `扣 ${used}` : '扣减', left ? `余 ${left}` : ''].filter(Boolean).join('，')
  }
  return event.reason ? `标为已使用（${event.reason}）` : '标为已使用'
}

/** 产生这条变化的步骤：取最近两级上级步骤加节点名，完整路径放在提示里。 */
export function eventStepLabel(event: TaskMaterialEvent): { short: string; full: string } {
  const parts = [...event.stepPath, ...(event.nodeName ? [event.nodeName] : [])]
  return { short: parts.slice(-3).join(' · ') || '未关联步骤', full: parts.join(' / ') || '未关联步骤' }
}

/** 按搬运前后的所属物料身份与库位名在当前画布找到库位；缺身份时退回按"所属 / 库位"名称匹配。 */
export function findEventSite(model: ResourceUsageModel, label?: string, ownerUuid?: string): CanvasSiteRef | undefined {
  if (!label) return undefined
  const cut = label.lastIndexOf(' / ')
  const owner = cut < 0 ? '' : label.slice(0, cut), name = cut < 0 ? label : label.slice(cut + 3)
  for (const [uuid, site] of model.sites) {
    if (site.name !== name) continue
    if (ownerUuid ? site.ownerUuid === ownerUuid : site.ownerName === owner) {
      return { siteUuid: uuid, siteName: site.name, ownerName: site.ownerName }
    }
  }
  return undefined
}
