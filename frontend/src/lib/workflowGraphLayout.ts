import type { WorkflowGraphEdge, WorkflowGraphNode } from '../types'
import { isWorkflowRegionNode, workflowControlKind } from './workflowControlFlow'

const NODE_WIDTH = 164
const NODE_HEIGHT = 82
const GROUP_WIDTH = 188
const GROUP_FRAME_SIDE = 12
const GROUP_FRAME_TOP = 30
const GROUP_FRAME_BOTTOM = 12
const COLUMN_GAP = 76
const ROW_GAP = 46
const BAND_GAP = 88
const STAGE_PADDING = 36

interface LayoutOptions {
  ranksPerBand?: number
}

type WorkflowGroupFrame = { x: number; y: number; width: number; height: number }

export interface PositionedWorkflowNode {
  node: WorkflowGraphNode
  x: number
  y: number
  width: number
  height: number
  rank: number
  band: number
  groupUuid?: string
  groupUuids: string[]
}

export interface PositionedWorkflowGroup {
  node: WorkflowGraphNode
  rank: number
  band: number
  children: PositionedWorkflowNode[]
  frames: WorkflowGroupFrame[]
}

export interface PositionedWorkflowEdge {
  edge: WorkflowGraphEdge
  source: PositionedWorkflowNode
  target: PositionedWorkflowNode
  path: string
}

export interface WorkflowGraphLayout {
  width: number
  height: number
  nodes: PositionedWorkflowNode[]
  groups: PositionedWorkflowGroup[]
  edges: PositionedWorkflowEdge[]
  warnings: string[]
  ranksPerBand: number
}

interface LayoutEntity {
  id: string
  node: WorkflowGraphNode
  rank: number
  band: number
  x: number
  y: number
  width: number
  height: number
}

function isGroup(node: WorkflowGraphNode) {
  return isWorkflowRegionNode(node)
}

function frameForNodes(
  groupNode: WorkflowGraphNode,
  nodes: PositionedWorkflowNode[],
): WorkflowGroupFrame {
  const minX = Math.min(...nodes.map((node) => node.x))
  const minY = Math.min(...nodes.map((node) => node.y))
  const maxX = Math.max(...nodes.map((node) => node.x + node.width))
  const maxY = Math.max(...nodes.map((node) => node.y + node.height))
  const repeatBottom = workflowControlKind(groupNode) === 'repeat_until' ? 38 : 0
  return {
    x: minX - GROUP_FRAME_SIDE,
    y: minY - GROUP_FRAME_TOP,
    width: maxX - minX + GROUP_FRAME_SIDE * 2,
    height: maxY - minY + GROUP_FRAME_TOP + GROUP_FRAME_BOTTOM + repeatBottom,
  }
}

function frameContainsNode(frame: WorkflowGroupFrame, node: PositionedWorkflowNode) {
  const centerX = node.x + node.width / 2
  const centerY = node.y + node.height / 2
  return centerX >= frame.x
    && centerX <= frame.x + frame.width
    && centerY >= frame.y
    && centerY <= frame.y + frame.height
}

function groupFrames(
  groupNode: WorkflowGraphNode,
  children: PositionedWorkflowNode[],
  positionedNodes: PositionedWorkflowNode[],
): WorkflowGroupFrame[] {
  const childrenByBand = new Map<number, PositionedWorkflowNode[]>()
  children.forEach((child) => {
    const values = childrenByBand.get(child.band) || []
    values.push(child)
    childrenByBand.set(child.band, values)
  })
  const bands = [...childrenByBand.entries()].sort(([left], [right]) => left - right)
  if (workflowControlKind(groupNode)) {
    return bands.map(([, values]) => frameForNodes(groupNode, values))
  }

  const memberUuids = new Set(children.map((child) => child.node.uuid))
  return bands.flatMap(([, values]) => {
    const sorted = [...values].sort((left, right) => (
      left.rank - right.rank
        || left.y - right.y
        || left.x - right.x
        || compareNodes(left.node, right.node)
    ))
    const runs: PositionedWorkflowNode[][] = []
    sorted.forEach((child) => {
      const current = runs.at(-1)
      if (!current) {
        runs.push([child])
        return
      }
      const maxRank = Math.max(...current.map((node) => node.rank))
      const candidate = [...current, child]
      const candidateFrame = frameForNodes(groupNode, candidate)
      const containsUnrelatedNode = positionedNodes.some((node) => (
        node.band === child.band
          && !memberUuids.has(node.node.uuid)
          && frameContainsNode(candidateFrame, node)
      ))
      if (child.rank <= maxRank + 1 && !containsUnrelatedNode) {
        current.push(child)
      } else {
        runs.push([child])
      }
    })
    return runs.map((run) => frameForNodes(groupNode, run))
  })
}

function nodeOrder(node: WorkflowGraphNode) {
  return Number.isFinite(node.authoringOrder) ? Number(node.authoringOrder) : Number.MAX_SAFE_INTEGER
}

function compareNodes(left: WorkflowGraphNode, right: WorkflowGraphNode) {
  return nodeOrder(left) - nodeOrder(right) || left.uuid.localeCompare(right.uuid)
}

function compareEntities(left: LayoutEntity, right: LayoutEntity) {
  return compareNodes(left.node, right.node)
}

export function workflowEdgePath(source: PositionedWorkflowNode, target: PositionedWorkflowNode) {
  if (target.band > source.band) {
    const sourceX = source.x + source.width / 2
    const sourceY = source.y + source.height
    const targetX = target.x + target.width / 2
    const targetY = target.y
    const middleY = sourceY + Math.max(42, (targetY - sourceY) / 2)
    return `M ${sourceX} ${sourceY} C ${sourceX} ${middleY}, ${targetX} ${middleY}, ${targetX} ${targetY}`
  }

  const travelsRight = target.x >= source.x
  const sourceX = travelsRight ? source.x + source.width : source.x
  const targetX = travelsRight ? target.x : target.x + target.width
  const sourceY = source.y + source.height / 2
  const targetY = target.y + target.height / 2
  const bend = Math.max(42, Math.abs(targetX - sourceX) * 0.42)
  const sourceControlX = sourceX + (travelsRight ? bend : -bend)
  const targetControlX = targetX + (travelsRight ? -bend : bend)
  return `M ${sourceX} ${sourceY} C ${sourceControlX} ${sourceY}, ${targetControlX} ${targetY}, ${targetX} ${targetY}`
}

export function layoutWorkflowGraph(
  nodes: WorkflowGraphNode[],
  edges: WorkflowGraphEdge[],
  options: LayoutOptions = {},
): WorkflowGraphLayout {
  const ranksPerBand = Math.max(2, Math.floor(options.ranksPerBand || 4))
  const warnings: string[] = []
  const nodesByUuid = new Map(nodes.map((node) => [node.uuid, node]))
  const groupNodes = nodes.filter(isGroup).sort(compareNodes)
  const groupIds = new Set(groupNodes.map((node) => node.uuid))
  const entities: LayoutEntity[] = nodes.filter((node) => !isGroup(node)).map((node) => ({
    id: node.uuid,
    node,
    rank: 0,
    band: 0,
    x: 0,
    y: 0,
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
  }))
  entities.sort(compareEntities)

  const entitiesById = new Map(entities.map((entity) => [entity.id, entity]))

  const adjacency = new Map<string, Set<string>>()
  const predecessors = new Map<string, Set<string>>()
  const indegree = new Map(entities.map((entity) => [entity.id, 0]))
  edges.forEach((edge) => {
    const source = entitiesById.get(edge.sourceNodeUuid)
    const target = entitiesById.get(edge.targetNodeUuid)
    if (!source || !target) {
      const endpointsExist = nodesByUuid.has(edge.sourceNodeUuid) && nodesByUuid.has(edge.targetNodeUuid)
      warnings.push(endpointsExist
        ? `连线 ${edge.uuid} 指向展示分组，未参与拓扑布局。`
        : `连线 ${edge.uuid} 的端点不存在，已跳过布局。`)
      return
    }
    const targets = adjacency.get(source.id) || new Set<string>()
    if (targets.has(target.id)) return
    targets.add(target.id)
    adjacency.set(source.id, targets)
    const sources = predecessors.get(target.id) || new Set<string>()
    sources.add(source.id)
    predecessors.set(target.id, sources)
    indegree.set(target.id, (indegree.get(target.id) || 0) + 1)
  })

  const ready = entities.filter((entity) => indegree.get(entity.id) === 0).sort(compareEntities)
  const visited = new Set<string>()
  while (ready.length) {
    const entity = ready.shift()!
    visited.add(entity.id)
    const targets = [...(adjacency.get(entity.id) || [])]
      .map((uuid) => entitiesById.get(uuid)!)
      .sort(compareEntities)
    targets.forEach((target) => {
      target.rank = Math.max(target.rank, entity.rank + 1)
      const nextIndegree = (indegree.get(target.id) || 0) - 1
      indegree.set(target.id, nextIndegree)
      if (nextIndegree === 0) {
        ready.push(target)
        ready.sort(compareEntities)
      }
    })
  }

  if (visited.size !== entities.length) {
    const lastRank = Math.max(0, ...entities.filter((entity) => visited.has(entity.id)).map((entity) => entity.rank))
    entities.filter((entity) => !visited.has(entity.id)).sort(compareEntities).forEach((entity) => {
      entity.rank = lastRank + 1
    })
    warnings.push('检测到循环依赖；相关节点已放到最后一个拓扑层。')
  }

  const maxRank = Math.max(0, ...entities.map((entity) => entity.rank))
  const ranks = Array.from({ length: maxRank + 1 }, (_, rank) => (
    entities.filter((entity) => entity.rank === rank).sort(compareEntities)
  ))

  const orderByEntity = new Map<string, number>()
  ranks.forEach((rankEntities, rank) => {
    if (rank > 0) {
      rankEntities.sort((left, right) => {
        const barycenter = (entity: LayoutEntity) => {
          const prior = [...(predecessors.get(entity.id) || [])]
            .map((uuid) => orderByEntity.get(uuid))
            .filter((value): value is number => value !== undefined)
          return prior.length ? prior.reduce((sum, value) => sum + value, 0) / prior.length : Number.MAX_SAFE_INTEGER
        }
        return barycenter(left) - barycenter(right) || compareEntities(left, right)
      })
    }
    rankEntities.forEach((entity, index) => orderByEntity.set(entity.id, index))
  })

  const bandCount = Math.floor(maxRank / ranksPerBand) + 1
  const bandHeights = Array.from({ length: bandCount }, (_, band) => {
    const firstRank = band * ranksPerBand
    const lastRank = Math.min(maxRank, firstRank + ranksPerBand - 1)
    return Math.max(
      NODE_HEIGHT,
      ...ranks.slice(firstRank, lastRank + 1).map((rankEntities) => (
        rankEntities.reduce((sum, entity) => sum + entity.height, 0)
          + Math.max(0, rankEntities.length - 1) * ROW_GAP
      )),
    )
  })
  const bandTops: number[] = []
  let nextBandTop = STAGE_PADDING
  bandHeights.forEach((height, band) => {
    bandTops[band] = nextBandTop
    nextBandTop += height + (band < bandHeights.length - 1 ? BAND_GAP : 0)
  })

  const usedColumns = Math.min(maxRank + 1, ranksPerBand)
  ranks.forEach((rankEntities, rank) => {
    const band = Math.floor(rank / ranksPerBand)
    const slot = rank % ranksPerBand
    const visualSlot = band % 2 === 0 ? slot : ranksPerBand - slot - 1
    let y = bandTops[band]
    rankEntities.forEach((entity) => {
      entity.band = band
      entity.x = STAGE_PADDING + visualSlot * (GROUP_WIDTH + COLUMN_GAP) + (GROUP_WIDTH - entity.width) / 2
      entity.y = y
      y += entity.height + ROW_GAP
    })
  })

  const positionedNodes: PositionedWorkflowNode[] = entities.map((entity) => {
    const groupUuids: string[] = []
    const seen = new Set<string>()
    let parentUuid = entity.node.parentUuid
    while (parentUuid && groupIds.has(parentUuid) && !seen.has(parentUuid)) {
      seen.add(parentUuid)
      groupUuids.push(parentUuid)
      parentUuid = nodesByUuid.get(parentUuid)?.parentUuid
    }
    return {
      node: entity.node,
      x: entity.x,
      y: entity.y,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      rank: entity.rank,
      band: entity.band,
      groupUuid: groupUuids[0],
      groupUuids,
    }
  })

  let emptyGroupIndex = 0
  const positionedGroups: PositionedWorkflowGroup[] = groupNodes.map((groupNode) => {
    const children = positionedNodes.filter((node) => node.groupUuids.includes(groupNode.uuid))
    if (!children.length) {
      const visualSlot = emptyGroupIndex % ranksPerBand
      const emptyRow = Math.floor(emptyGroupIndex / ranksPerBand)
      emptyGroupIndex += 1
      return {
        node: groupNode,
        rank: 0,
        band: bandCount,
        children,
        frames: [{
          x: STAGE_PADDING + visualSlot * (GROUP_WIDTH + COLUMN_GAP),
          y: nextBandTop + emptyRow * (NODE_HEIGHT + ROW_GAP),
          width: GROUP_WIDTH,
          height: NODE_HEIGHT,
        }],
      }
    }
    return {
      node: groupNode,
      rank: Math.min(...children.map((node) => node.rank)),
      band: Math.min(...children.map((node) => node.band)),
      children,
      frames: groupFrames(groupNode, children, positionedNodes),
    }
  })

  const positionedByUuid = new Map(positionedNodes.map((node) => [node.node.uuid, node]))
  const positionedEdges = edges.flatMap((edge) => {
    const source = positionedByUuid.get(edge.sourceNodeUuid)
    const target = positionedByUuid.get(edge.targetNodeUuid)
    if (!source || !target) {
      if (nodesByUuid.has(edge.sourceNodeUuid) && nodesByUuid.has(edge.targetNodeUuid)) {
        warnings.push(`连线 ${edge.uuid} 指向展示分组，未绘制。`)
      }
      return []
    }
    return [{ edge, source, target, path: workflowEdgePath(source, target) }]
  })

  const stageWidth = Math.max(
    STAGE_PADDING * 2 + usedColumns * GROUP_WIDTH + Math.max(0, usedColumns - 1) * COLUMN_GAP,
    ...positionedGroups.flatMap((group) => group.frames.map((frame) => frame.x + frame.width + STAGE_PADDING)),
  )
  const stageHeight = Math.max(
    nextBandTop + STAGE_PADDING,
    ...positionedGroups.flatMap((group) => group.frames.map((frame) => frame.y + frame.height + STAGE_PADDING)),
  )

  return {
    width: stageWidth,
    height: stageHeight,
    nodes: positionedNodes,
    groups: positionedGroups,
    edges: positionedEdges,
    warnings: [...new Set(warnings)],
    ranksPerBand,
  }
}

export interface WorkflowNodeOffset {
  x: number
  y: number
}

export function applyWorkflowNodeOffsets(
  layout: WorkflowGraphLayout,
  offsets: Record<string, WorkflowNodeOffset>,
): WorkflowGraphLayout {
  const nodes = layout.nodes.map((positioned) => {
    const offset = offsets[positioned.node.uuid]
    return offset
      ? { ...positioned, x: positioned.x + offset.x, y: positioned.y + offset.y }
      : positioned
  })
  const nodesByUuid = new Map(nodes.map((node) => [node.node.uuid, node]))
  const groups = layout.groups.map((group) => {
    const children = group.children.map((child) => nodesByUuid.get(child.node.uuid) || child)
    const emptyGroupOffset = !children.length ? offsets[group.node.uuid] : undefined
    return {
      ...group,
      children,
      frames: children.length
        ? groupFrames(group.node, children, nodes)
        : group.frames.map((frame) => emptyGroupOffset
            ? { ...frame, x: frame.x + emptyGroupOffset.x, y: frame.y + emptyGroupOffset.y }
            : frame),
    }
  })
  const edges = layout.edges.map((positioned) => {
    const source = nodesByUuid.get(positioned.source.node.uuid) || positioned.source
    const target = nodesByUuid.get(positioned.target.node.uuid) || positioned.target
    return { ...positioned, source, target, path: workflowEdgePath(source, target) }
  })
  return { ...layout, nodes, groups, edges }
}
