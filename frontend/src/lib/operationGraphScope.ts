import type { WorkflowGraphNode } from '../types'

/** 保留当前操作自己拥有的节点；已发布子操作内部只能进入子操作后查看或编辑。 */
export function operationOwnedNodes(nodes: WorkflowGraphNode[]): WorkflowGraphNode[] {
  const byUuid = new Map(nodes.map((node) => [node.uuid, node]))
  return nodes.filter((node) => {
    let parent = node.parentUuid
    const seen = new Set([node.uuid])
    while (parent) {
      if (seen.has(parent)) return false
      seen.add(parent)
      const ancestor = byUuid.get(parent)
      if (!ancestor) break
      if (ancestor.meta_data?.unilab?.composite) return false
      parent = ancestor.parentUuid
    }
    return true
  }).sort((left, right) => (
    Number(left.meta_data?.unilab?.sequence_index ?? left.authoringOrder ?? left.meta_data?.unilab?.authoring_source_order ?? Number.MAX_SAFE_INTEGER)
    - Number(right.meta_data?.unilab?.sequence_index ?? right.authoringOrder ?? right.meta_data?.unilab?.authoring_source_order ?? Number.MAX_SAFE_INTEGER)
  ))
}
