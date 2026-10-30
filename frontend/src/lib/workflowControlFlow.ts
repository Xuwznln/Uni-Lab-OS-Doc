import type { WorkflowGraphEdge, WorkflowGraphNode } from '../types'

export type WorkflowControlKind = 'condition' | 'repeat_until'

export type WorkflowControlRelationKind =
  | 'condition_true'
  | 'condition_false'
  | 'condition_branch'
  | 'repeat_entry'
  | 'repeat_continue'
  | 'repeat_exit'

export interface WorkflowDisplayEdge extends WorkflowGraphEdge {
  authoritative: boolean
  affectsLayout: boolean
  displayKind?: WorkflowControlRelationKind
  displayLabel?: string
  controlRegionUuid?: string
}

export interface WorkflowControlRegion {
  node: WorkflowGraphNode
  kind: WorkflowControlKind
  subtitle: string
}

export interface WorkflowControlFlowProjection {
  controlEdges: WorkflowDisplayEdge[]
  controlRegions: WorkflowControlRegion[]
  nodeBadges: Map<string, string[]>
}

type UnknownRecord = Record<string, unknown>

function record(value: unknown): UnknownRecord | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as UnknownRecord
    : undefined
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string' && Boolean(item))
    : []
}

function unique(values: string[]) {
  return [...new Set(values)]
}

export function workflowControlKind(node: WorkflowGraphNode | undefined): WorkflowControlKind | undefined {
  if (!node) return undefined
  const values = [node.kind, node.type].map((value) => String(value).toLowerCase())
  if (values.includes('condition')) return 'condition'
  if (values.includes('repeat_until')) return 'repeat_until'
  return undefined
}

export function isWorkflowRegionNode(node: WorkflowGraphNode) {
  return node.kind === 'group' || Boolean(workflowControlKind(node))
}

function bindingNodeUuids(bindings: unknown) {
  const sourceUuids: string[] = []
  Object.values(record(bindings) || {}).forEach((value) => {
    const binding = record(value)
    if (binding?.kind === 'node_result' && typeof binding.node_uuid === 'string') {
      sourceUuids.push(binding.node_uuid)
    }
  })
  return sourceUuids
}

function conditionBranchLabel(branch: UnknownRecord, index: number) {
  const raw = String(branch.label || '').trim().toLowerCase()
  if (raw === 'if' || (index === 0 && branch.condition != null)) return 'True 分支'
  if (raw === 'else' || branch.condition == null) return 'False 分支'
  return String(branch.label || `分支 ${index + 1}`)
}

function conditionRelationKind(label: string): WorkflowControlRelationKind {
  if (label === 'True 分支') return 'condition_true'
  if (label === 'False 分支') return 'condition_false'
  return 'condition_branch'
}

function addBadge(badges: Map<string, string[]>, nodeUuid: string, label: string) {
  const values = badges.get(nodeUuid) || []
  if (!values.includes(label)) values.push(label)
  badges.set(nodeUuid, values)
}

export function projectWorkflowControlFlow(
  nodes: WorkflowGraphNode[],
): WorkflowControlFlowProjection {
  const nodesByUuid = new Map(nodes.map((node) => [node.uuid, node]))
  const controlRegions = nodes.flatMap((node): WorkflowControlRegion[] => {
    const kind = workflowControlKind(node)
    if (!kind) return []
    const maximum = Number(node.param?.max_iterations)
    return [{
      node,
      kind,
      subtitle: kind === 'condition'
        ? '严格布尔 · 仅执行命中分支'
        : Number.isInteger(maximum) && maximum > 0
          ? `最多 ${maximum} 轮 · 条件满足后退出`
          : '条件满足后退出',
    }]
  })
  const nodeBadges = new Map<string, string[]>()
  const controlEdges: WorkflowDisplayEdge[] = []
  const seenEdges = new Set<string>()

  const controlBoundary = (
    nodeUuid: string,
    boundary: 'entry' | 'exit',
    seen = new Set<string>(),
  ): string[] => {
    if (seen.has(nodeUuid)) return []
    seen.add(nodeUuid)
    const node = nodesByUuid.get(nodeUuid)
    const kind = workflowControlKind(node)
    if (!kind || !node) return nodesByUuid.has(nodeUuid) ? [nodeUuid] : []
    const params = record(node.param) || {}
    const boundaryUuids = kind === 'condition'
      ? (Array.isArray(params.branches) ? params.branches : []).flatMap((value) => (
          strings(record(value)?.[`${boundary}_node_uuids`])
        ))
      : strings(params[`${boundary}_node_uuids`])
    return unique(boundaryUuids.flatMap((uuid) => controlBoundary(uuid, boundary, new Set(seen))))
  }

  const controlEntries = (nodeUuid: string) => controlBoundary(nodeUuid, 'entry')
  const controlExits = (nodeUuid: string) => controlBoundary(nodeUuid, 'exit')

  const addControlEdge = ({
    regionUuid,
    sourceUuid,
    targetUuid,
    displayKind,
    displayLabel,
    affectsLayout,
  }: {
    regionUuid: string
    sourceUuid: string
    targetUuid: string
    displayKind: WorkflowControlRelationKind
    displayLabel: string
    affectsLayout: boolean
  }) => {
    if (sourceUuid === targetUuid || !nodesByUuid.has(sourceUuid) || !nodesByUuid.has(targetUuid)) return
    const identity = `${regionUuid}:${displayKind}:${sourceUuid}:${targetUuid}`
    if (seenEdges.has(identity)) return
    seenEdges.add(identity)
    controlEdges.push({
      uuid: `display-control:${identity}`,
      sourceNodeUuid: sourceUuid,
      targetNodeUuid: targetUuid,
      authoritative: false,
      affectsLayout,
      displayKind,
      displayLabel,
      controlRegionUuid: regionUuid,
    })
  }

  controlRegions.forEach(({ node, kind }) => {
    const params = record(node.param) || {}
    if (kind === 'condition') {
      const sources = unique([
        ...strings(params.predecessor_node_uuids),
        ...bindingNodeUuids(params.bindings),
      ]).flatMap((uuid) => controlExits(uuid))
      const branches = Array.isArray(params.branches) ? params.branches : []
      branches.forEach((value, index) => {
        const branch = record(value)
        if (!branch) return
        const label = conditionBranchLabel(branch, index)
        strings(branch.node_uuids).forEach((uuid) => addBadge(nodeBadges, uuid, label))
        const targets = strings(branch.entry_node_uuids).flatMap((uuid) => controlEntries(uuid))
        sources.forEach((sourceUuid) => targets.forEach((targetUuid) => addControlEdge({
          regionUuid: node.uuid,
          sourceUuid,
          targetUuid,
          displayKind: conditionRelationKind(label),
          displayLabel: label,
          affectsLayout: true,
        })))
      })
      return
    }

    const entries = strings(params.entry_node_uuids).flatMap((uuid) => controlEntries(uuid))
    const exits = strings(params.exit_node_uuids).flatMap((uuid) => controlExits(uuid))
    const sources = unique([
      ...strings(params.predecessor_node_uuids),
      ...bindingNodeUuids(params.initial_carry),
    ]).flatMap((uuid) => controlExits(uuid))
    const successors = strings(params.successor_node_uuids).flatMap((uuid) => controlEntries(uuid))
    entries.forEach((uuid) => addBadge(nodeBadges, uuid, '循环入口'))
    exits.forEach((uuid) => addBadge(nodeBadges, uuid, '退出判断'))
    sources.forEach((sourceUuid) => entries.forEach((targetUuid) => addControlEdge({
      regionUuid: node.uuid,
      sourceUuid,
      targetUuid,
      displayKind: 'repeat_entry',
      displayLabel: '进入循环',
      affectsLayout: true,
    })))
    exits.forEach((sourceUuid) => successors.forEach((targetUuid) => addControlEdge({
      regionUuid: node.uuid,
      sourceUuid,
      targetUuid,
      displayKind: 'repeat_exit',
      displayLabel: '满足条件 · 退出循环',
      affectsLayout: true,
    })))
    exits.forEach((sourceUuid) => entries.forEach((targetUuid) => addControlEdge({
      regionUuid: node.uuid,
      sourceUuid,
      targetUuid,
      displayKind: 'repeat_continue',
      displayLabel: '继续下一轮',
      affectsLayout: false,
    })))
  })

  return { controlEdges, controlRegions, nodeBadges }
}
