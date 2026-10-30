import type { WorkflowGraphEdge, WorkflowGraphNode } from '../types'

export function childWorkflowUuid(node: WorkflowGraphNode): string | undefined {
  const value = node.meta_data?.unilab?.composite?.child_workflow_uuid
  return typeof value === 'string' && value ? value : undefined
}

/** 仅用于画布：实验操作显示为一次调用，内部节点仍保留在原始执行图中。 */
export function projectCompositeCalls(nodes: WorkflowGraphNode[]) {
  const byId = new Map(nodes.map((node) => [node.uuid, node]))
  const owner = (uuid: string): string => {
    let current = byId.get(uuid)
    let result = uuid
    const visited = new Set<string>()
    while (current?.parentUuid && !visited.has(current.parentUuid)) {
      visited.add(current.parentUuid)
      current = byId.get(current.parentUuid)
      if (current && childWorkflowUuid(current)) result = current.uuid
    }
    return result
  }
  const visibleNodes = nodes.filter((node) => owner(node.uuid) === node.uuid)
  const projectEdges = <T extends WorkflowGraphEdge>(edges: T[]): T[] => {
    const seen = new Set<string>()
    return edges.flatMap((edge) => {
      const sourceNodeUuid = owner(edge.sourceNodeUuid)
      const targetNodeUuid = owner(edge.targetNodeUuid)
      if (sourceNodeUuid === targetNodeUuid) return []
      // 同一对端点的不同物料/控制关系仍需保留。
      const key = JSON.stringify([sourceNodeUuid, targetNodeUuid, edge.sourceHandleUuid, edge.targetHandleUuid, (edge as T & { displayKind?: string }).displayKind])
      if (seen.has(key)) return []
      seen.add(key)
      return [{ ...edge, sourceNodeUuid, targetNodeUuid }]
    })
  }
  return { nodes: visibleNodes, owner, projectEdges }
}
