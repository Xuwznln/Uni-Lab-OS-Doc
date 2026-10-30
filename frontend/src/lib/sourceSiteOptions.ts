import type { ContractField, MaterialRecord, WorkflowGraph, WorkflowGraphNode } from '../types'

export function isRuntimeMaterialSource(node: WorkflowGraphNode, nodes: WorkflowGraphNode[]): boolean {
  if (node.kind !== 'material_source') return false
  if (node.param?.supports_replenishment === true) return true
  let parent = nodes.find((item) => item.uuid === node.parentUuid)
  const seen = new Set<string>()
  while (parent && !seen.has(parent.uuid)) {
    seen.add(parent.uuid)
    if (parent.kind === 'condition' || parent.kind === 'repeat_until') return true
    parent = nodes.find((item) => item.uuid === parent?.parentUuid)
  }
  return false
}

/** 读取发布图的来源绑定，并从权威库位占用中投影启动选项。 */
export function sourceSiteOptions(graph: WorkflowGraph | undefined, fields: ContractField[], materials: MaterialRecord[]) {
  const byId = new Map(materials.map((material) => [material.uuid, material]))
  const result = new Map<string, Array<{ value: string; label: string; materialUuid: string }>>()
  for (const node of graph?.nodes || []) {
    const parameter = node.meta_data?.unilab?.material_source_site_binding?.parameter
    if (node.kind !== 'material_source' || typeof parameter !== 'string') continue
    const field = fields.find((item) => item.name === parameter)
    const owner = byId.get(node.param?.mount?.uuid)
    const allowed = field?.schema.enum
    const range = node.param?.slot_range
    const runtime = isRuntimeMaterialSource(node, graph?.nodes || [])
    result.set(parameter, (owner?.sites || []).flatMap((site) => {
      const material = site.occupiedMaterialUuid ? byId.get(site.occupiedMaterialUuid) : undefined
      if ((Array.isArray(allowed) && !allowed.includes(site.name)) ||
          (Array.isArray(range) && !range.includes(site.uuid))) return []
      if (runtime) return [{ value: site.name, label: `${owner?.name} / ${site.name} · ${material?.name || '空库位，执行时检查'}`, materialUuid: material?.uuid || '' }]
      if (!material || material.resourceTemplateUuid !== node.param?.resource_template_uuid ||
          material.currentLocation.kind !== 'site' || material.currentLocation.siteUuid !== site.uuid ||
          material.currentLocation.ownerMaterialUuid !== owner?.uuid ||
          (Array.isArray(allowed) && !allowed.includes(site.name)) ||
          (Array.isArray(range) && !range.includes(site.uuid))) return []
      return [{ value: site.name, label: `${owner?.name} / ${site.name} · ${material.name}`, materialUuid: material.uuid }]
    }))
  }
  return result
}
