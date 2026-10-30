import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type PointerEvent } from 'react'
import {
  Braces,
  CircleDot,
  GitBranch,
  Layers3,
  ListTree,
  Network,
  PackageOpen,
  Repeat2,
  RotateCcw,
  TriangleAlert,
  X,
} from 'lucide-react'
import {
  applyWorkflowNodeOffsets,
  layoutWorkflowGraph,
  type PositionedWorkflowNode,
  type WorkflowNodeOffset,
} from '../lib/workflowGraphLayout'
import {
  projectWorkflowControlFlow,
  workflowControlKind,
  type WorkflowControlRelationKind,
  type WorkflowDisplayEdge,
} from '../lib/workflowControlFlow'
import type { WorkflowGraphEdge, WorkflowGraphNode } from '../types'
import { isRuntimeMaterialSource } from '../lib/sourceSiteOptions'
import { childWorkflowUuid, projectCompositeCalls } from '../lib/workflowCompositeView'

type WorkflowEdgeKind =
  | 'dependency'
  | 'material'
  | 'parallel_entry'
  | 'parallel_join'
  | WorkflowControlRelationKind
type EdgeEndpoint = 'source' | 'target'

const edgeKindMetadata = {
  dependency: { label: '流程依赖', description: '前置节点完成后，后续节点才能继续' },
  material: { label: '物料输入', description: '物料来源与消费节点的输入关系' },
  parallel_entry: { label: '并行入口', description: '前置条件满足后，该节点进入并行分支；不表示物料被复制' },
  parallel_join: { label: '并行汇合', description: '并行分支完成后，后续节点才能继续' },
  condition_true: { label: 'True 分支', description: '严格布尔条件为 true 时，调度器只激活该分支' },
  condition_false: { label: 'False 分支', description: '严格布尔条件为 false 时，调度器只激活该分支' },
  condition_branch: { label: '条件分支', description: '条件命中时，调度器激活该分支' },
  repeat_entry: { label: '进入循环', description: '前置条件满足后，调度器进入循环第一轮' },
  repeat_continue: { label: '继续下一轮', description: '退出条件为 false 时，调度器携带 next 值开始下一轮' },
  repeat_exit: { label: '退出循环', description: '退出条件为 true 时，调度器结束循环并继续后继节点' },
} satisfies Record<WorkflowEdgeKind, { label: string; description: string }>

/** 将节点类型翻译为实验人员能直接理解的名称。 */
function nodeTypeLabel(node: WorkflowGraphNode) {
  if (childWorkflowUuid(node)) return '实验操作'
  if (node.kind === 'material_source') return '物料源'
  if (node.kind === 'group') return '分组'
  if (node.type.toLowerCase() === 'condition') return '条件节点'
  if (node.type.toLowerCase() === 'repeat_until') return '循环节点'
  return node.deviceId || node.type || '工作流节点'
}

function nodeParallelScope(node: WorkflowGraphNode | undefined, nodesByUuid: Map<string, WorkflowGraphNode>) {
  if (!node) return undefined
  if (node.parallelScope) return node.parallelScope
  return node.parentUuid ? nodesByUuid.get(node.parentUuid)?.parallelScope : undefined
}

function classifyEdge(
  edge: WorkflowDisplayEdge,
  nodesByUuid: Map<string, WorkflowGraphNode>,
): WorkflowEdgeKind {
  if (edge.displayKind) return edge.displayKind
  const source = nodesByUuid.get(edge.sourceNodeUuid)
  const target = nodesByUuid.get(edge.targetNodeUuid)
  if (source?.kind === 'material_source') return 'material'
  const sourceScope = nodeParallelScope(source, nodesByUuid)
  const targetScope = nodeParallelScope(target, nodesByUuid)
  if (targetScope && sourceScope !== targetScope) return 'parallel_entry'
  if (sourceScope && sourceScope !== targetScope) return 'parallel_join'
  return 'dependency'
}

function repeatContinuePath(source: PositionedWorkflowNode, target: PositionedWorkflowNode) {
  const sourceX = source.x + source.width / 2
  const sourceY = source.y + source.height
  const targetX = target.x + target.width / 2
  const targetY = target.y + target.height
  const loopY = Math.max(sourceY, targetY) + 28
  return `M ${sourceX} ${sourceY} C ${sourceX} ${loopY}, ${targetX} ${loopY}, ${targetX} ${targetY}`
}

function WorkflowNodeCard({
  positioned,
  materialInputs = [],
  edgeEndpoint,
  dimmed,
  dragging,
  onPointerDown,
  onPointerMove,
  onPointerUp,
  previewingMaterials,
  controlBadges = [],
  onMaterialPreviewStart,
  onMaterialPreviewEnd,
}: {
  positioned: PositionedWorkflowNode
  materialInputs?: WorkflowGraphNode[]
  edgeEndpoint?: EdgeEndpoint
  dimmed?: boolean
  dragging?: boolean
  onPointerDown: (event: PointerEvent<HTMLElement>) => void
  onPointerMove: (event: PointerEvent<HTMLElement>) => void
  onPointerUp: (event: PointerEvent<HTMLElement>) => void
  previewingMaterials?: boolean
  controlBadges?: string[]
  onMaterialPreviewStart: (materialUuids: string[]) => void
  onMaterialPreviewEnd: () => void
}) {
  const { node } = positioned
  const isMaterial = node.kind === 'material_source'
  const typeLabel = nodeTypeLabel(node)
  const alias = node.authoringResultName && node.authoringResultName !== node.name
    ? node.authoringResultName
    : typeLabel
  const order = node.authoringOrder === undefined ? '' : `#${String(node.authoringOrder + 1).padStart(2, '0')} · `
  const accessibleDetails = [...new Set([alias, typeLabel, ...controlBadges])]
  const accessibleMaterialInputs = materialInputs.length
    ? `物料输入：${materialInputs.map((material) => material.name).join('、')}`
    : undefined
  const endpointLabel = edgeEndpoint === 'source'
    ? '已选连线起点'
    : edgeEndpoint === 'target'
      ? '已选连线终点'
      : undefined
  return (
    <article
      className={[
        'workflow-dag-node',
        isMaterial ? 'material-source' : '',
        node.disabled ? 'disabled' : '',
        edgeEndpoint ? `edge-${edgeEndpoint}` : '',
        dimmed ? 'edge-dimmed' : '',
        dragging ? 'dragging' : '',
      ].filter(Boolean).join(' ')}
      aria-label={[node.name, ...accessibleDetails, accessibleMaterialInputs, endpointLabel].filter(Boolean).join('，')}
      aria-grabbed={dragging}
      data-node-uuid={node.uuid}
      data-rank={positioned.rank}
      data-band={positioned.band}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      style={{ left: positioned.x, top: positioned.y, width: positioned.width, height: positioned.height }}
      title={`${node.name}\n${alias}\n${typeLabel}\n拖动可调整位置\n${node.uuid}`}
    >
      <span className="workflow-dag-node-icon">
        {isMaterial ? <CircleDot size={17} /> : <Braces size={17} />}
      </span>
      <span className="workflow-dag-node-copy">
        <strong>{node.name}</strong>
        <small>{order}{alias}</small>
        {childWorkflowUuid(node) ? <a
          href={`?page=workflows&workflow=${encodeURIComponent(childWorkflowUuid(node)!)}`}
          aria-label={`查看实验操作：${node.name}`}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
        >查看实验操作</a> : null}
      </span>
      {materialInputs.length ? (
        <button
          type="button"
          className="workflow-dag-node-materials"
          aria-label={`按住查看 ${materialInputs.length} 个物料输入：${materialInputs.map((material) => material.name).join('、')}`}
          aria-pressed={previewingMaterials}
          title={materialInputs.map((material) => material.name).join('、')}
          onPointerDown={(event) => {
            if (event.button !== 0) return
            event.stopPropagation()
            event.currentTarget.setPointerCapture?.(event.pointerId)
            onMaterialPreviewStart(materialInputs.map((material) => material.uuid))
          }}
          onPointerMove={(event) => {
            event.stopPropagation()
            const bounds = event.currentTarget.getBoundingClientRect()
            const outside = event.clientX < bounds.left
              || event.clientX > bounds.right
              || event.clientY < bounds.top
              || event.clientY > bounds.bottom
            if (!outside) return
            if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
              event.currentTarget.releasePointerCapture(event.pointerId)
            }
            onMaterialPreviewEnd()
          }}
          onPointerUp={(event) => {
            event.stopPropagation()
            if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
              event.currentTarget.releasePointerCapture(event.pointerId)
            }
            onMaterialPreviewEnd()
          }}
          onPointerCancel={(event) => {
            event.stopPropagation()
            onMaterialPreviewEnd()
          }}
          onPointerLeave={onMaterialPreviewEnd}
          onLostPointerCapture={onMaterialPreviewEnd}
          onClick={(event) => event.stopPropagation()}
        >
          <PackageOpen size={10} />{materialInputs.length}
        </button>
      ) : null}
      {controlBadges.length ? (
        <span className="workflow-dag-node-control-badges">
          {controlBadges.map((label) => <small key={label}>{label}</small>)}
        </span>
      ) : null}
      {node.disabled ? <em>已禁用</em> : null}
    </article>
  )
}

interface DragSession {
  pointerId: number
  startClientX: number
  startClientY: number
  nodeUuids: string[]
  initialOffsets: Record<string, WorkflowNodeOffset>
  bounds: { minX: number; minY: number; maxX: number; maxY: number }
  canvas: { width: number; height: number }
}

type DragBounds = DragSession['bounds']

export function WorkflowDag({
  nodes,
  edges,
  loading,
  error,
  onRetry,
}: {
  nodes: WorkflowGraphNode[]
  edges: WorkflowGraphEdge[]
  loading: boolean
  error: boolean
  onRetry?: () => void
}) {
  const regionRef = useRef<HTMLElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const dragSessionRef = useRef<DragSession | null>(null)
  const [ranksPerBand, setRanksPerBand] = useState(4)
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 })
  const [viewMode, setViewMode] = useState<'focus' | 'complete'>('complete')
  const [selectedEdgeUuid, setSelectedEdgeUuid] = useState<string>()
  const [nodeOffsets, setNodeOffsets] = useState<Record<string, WorkflowNodeOffset>>({})
  const [draggingNodeUuids, setDraggingNodeUuids] = useState<Set<string>>(new Set())
  const [previewMaterialUuids, setPreviewMaterialUuids] = useState<Set<string>>(new Set())
  const graphProjection = useMemo(() => {
    const calls = projectCompositeCalls(nodes)
    const displayNodes = calls.nodes
    const nodeByUuid = new Map(displayNodes.map((node) => [node.uuid, node]))
    const rawControl = projectWorkflowControlFlow(nodes)
    const control = {
      ...rawControl,
      controlRegions: rawControl.controlRegions.filter((region) => nodeByUuid.has(region.node.uuid)),
      controlEdges: calls.projectEdges(rawControl.controlEdges.filter((edge) => !edge.controlRegionUuid || nodeByUuid.has(edge.controlRegionUuid))),
    }
    const authoritativeEdges: WorkflowDisplayEdge[] = calls.projectEdges(edges).map((edge) => ({
      ...edge,
      authoritative: true,
      affectsLayout: true,
    }))
    const materialNodes = displayNodes
      .filter((node) => node.kind === 'material_source' && !isRuntimeMaterialSource(node, nodes))
      .sort((left, right) => (
        (left.authoringOrder ?? Number.MAX_SAFE_INTEGER) - (right.authoringOrder ?? Number.MAX_SAFE_INTEGER)
          || left.uuid.localeCompare(right.uuid)
      ))
    const materialNodeUuids = new Set(materialNodes.map((node) => node.uuid))
    const materialEdges = authoritativeEdges.filter((edge) => (
      materialNodeUuids.has(edge.sourceNodeUuid) || materialNodeUuids.has(edge.targetNodeUuid)
    ))
    const materialEdgeUuids = new Set(materialEdges.map((edge) => edge.uuid))
    const workflowEdges = authoritativeEdges.filter((edge) => !materialEdgeUuids.has(edge.uuid))
    const materialInputsByTarget = new Map<string, WorkflowGraphNode[]>()
    materialEdges.forEach((edge) => {
      const material = nodeByUuid.get(edge.sourceNodeUuid)
      if (!material || material.kind !== 'material_source') return
      const inputs = materialInputsByTarget.get(edge.targetNodeUuid) || []
      inputs.push(material)
      materialInputsByTarget.set(edge.targetNodeUuid, inputs)
    })
    const controlEdges = control.controlEdges.filter((edge) => (
      !materialNodeUuids.has(edge.sourceNodeUuid) && !materialNodeUuids.has(edge.targetNodeUuid)
    ))
    const displayEdges = [...authoritativeEdges, ...controlEdges]
    const edgeKindByUuid = new Map(displayEdges.map((edge) => [edge.uuid, classifyEdge(edge, nodeByUuid)]))
    const edgeLabelByUuid = new Map(controlEdges.map((edge) => [edge.uuid, edge.displayLabel]))
    const parallelControlCount = workflowEdges.filter((edge) => {
      const kind = edgeKindByUuid.get(edge.uuid)
      return kind === 'parallel_entry' || kind === 'parallel_join'
    }).length
    return {
      nodeByUuid,
      displayNodes,
      materialNodes,
      materialEdges,
      materialInputsByTarget,
      workflowNodes: displayNodes.filter((node) => !materialNodeUuids.has(node.uuid)),
      workflowEdges,
      layoutWorkflowEdges: [...workflowEdges, ...controlEdges.filter((edge) => edge.affectsLayout)],
      displayEdges,
      controlEdges,
      controlRegions: control.controlRegions,
      nodeBadges: control.nodeBadges,
      edgeKindByUuid,
      edgeLabelByUuid,
      parallelControlCount,
      dependencyCount: workflowEdges.length - parallelControlCount,
    }
  }, [edges, nodes])
  const visibleNodes = viewMode === 'focus' ? graphProjection.workflowNodes : graphProjection.displayNodes
  const visibleEdges = viewMode === 'focus'
    ? graphProjection.layoutWorkflowEdges
    : graphProjection.displayEdges.filter((edge) => edge.affectsLayout)
  const automaticLayout = useMemo(
    () => layoutWorkflowGraph(visibleNodes, visibleEdges, { ranksPerBand }),
    [ranksPerBand, visibleEdges, visibleNodes],
  )
  const layout = useMemo(
    () => applyWorkflowNodeOffsets(automaticLayout, nodeOffsets),
    [automaticLayout, nodeOffsets],
  )
  const positionedEdges = useMemo(() => {
    const positionedByUuid = new Map(layout.nodes.map((positioned) => [positioned.node.uuid, positioned]))
    const overlays = graphProjection.controlEdges.filter((edge) => !edge.affectsLayout).flatMap((edge) => {
      const source = positionedByUuid.get(edge.sourceNodeUuid)
      const target = positionedByUuid.get(edge.targetNodeUuid)
      if (!source || !target) return []
      return [{ edge, source, target, path: repeatContinuePath(source, target) }]
    })
    return [...layout.edges, ...overlays]
  }, [graphProjection.controlEdges, layout])
  const selectedEdge = selectedEdgeUuid
    ? graphProjection.displayEdges.find((edge) => edge.uuid === selectedEdgeUuid)
    : undefined
  const selectedSource = selectedEdge ? graphProjection.nodeByUuid.get(selectedEdge.sourceNodeUuid) : undefined
  const selectedTarget = selectedEdge ? graphProjection.nodeByUuid.get(selectedEdge.targetNodeUuid) : undefined
  const selectedKind = selectedEdge ? graphProjection.edgeKindByUuid.get(selectedEdge.uuid) : undefined
  const hasCustomLayout = Object.keys(nodeOffsets).length > 0

  useEffect(() => {
    const region = regionRef.current
    const scroll = scrollRef.current
    if (!region) return
    const updateLayoutMetrics = () => {
      if (!region.clientWidth) return
      setRanksPerBand(Math.max(2, Math.min(6, Math.floor((region.clientWidth + 76) / (188 + 76)))))
      if (scroll) {
        setViewportSize((current) => (
          current.width === scroll.clientWidth && current.height === scroll.clientHeight
            ? current
            : { width: scroll.clientWidth, height: scroll.clientHeight }
        ))
      }
    }
    updateLayoutMetrics()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(updateLayoutMetrics)
    observer.observe(region)
    if (scroll) observer.observe(scroll)
    return () => observer.disconnect()
  }, [])

  const selectEdge = (edgeUuid: string) => {
    setSelectedEdgeUuid((current) => current === edgeUuid ? undefined : edgeUuid)
  }

  const selectEdgeWithKeyboard = (event: KeyboardEvent<SVGGElement>, edgeUuid: string) => {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    selectEdge(edgeUuid)
  }

  const startDrag = (
    event: PointerEvent<HTMLElement>,
    requestedNodeUuids: string[],
    requestedBounds?: DragBounds,
  ) => {
    if (event.button !== 0 || !requestedNodeUuids.length) return
    event.preventDefault()
    const draggedNodes = layout.nodes.filter((node) => requestedNodeUuids.includes(node.node.uuid))
    if (!draggedNodes.length && !requestedBounds) return
    const bounds = requestedBounds || {
      minX: Math.min(...draggedNodes.map((node) => node.x)),
      minY: Math.min(...draggedNodes.map((node) => node.y)),
      maxX: Math.max(...draggedNodes.map((node) => node.x + node.width)),
      maxY: Math.max(...draggedNodes.map((node) => node.y + node.height)),
    }
    const visibleWidth = scrollRef.current?.clientWidth || 0
    const visibleHeight = scrollRef.current?.clientHeight || 0
    const initialOffsets = Object.fromEntries(requestedNodeUuids.map((uuid) => [
      uuid,
      nodeOffsets[uuid] || { x: 0, y: 0 },
    ]))
    dragSessionRef.current = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      nodeUuids: requestedNodeUuids,
      initialOffsets,
      bounds,
      canvas: {
        width: Math.max(layout.width, viewportSize.width, visibleWidth),
        height: Math.max(layout.height, viewportSize.height, visibleHeight),
      },
    }
    setDraggingNodeUuids(new Set(requestedNodeUuids))
    event.currentTarget.setPointerCapture?.(event.pointerId)
  }

  const startNodeDrag = (event: PointerEvent<HTMLElement>, positioned: PositionedWorkflowNode) => {
    const group = positioned.groupUuid
      ? automaticLayout.groups.find((candidate) => candidate.node.uuid === positioned.groupUuid)
      : undefined
    startDrag(event, group?.children.map((child) => child.node.uuid) || [positioned.node.uuid])
  }

  const startGroupDrag = (event: PointerEvent<HTMLElement>, groupUuid: string) => {
    const group = layout.groups.find((candidate) => candidate.node.uuid === groupUuid)
    if (!group) return
    const childUuids = group.children.map((child) => child.node.uuid)
    const frameBounds = group.frames.length
      ? {
          minX: Math.min(...group.frames.map((frame) => frame.x)),
          minY: Math.min(...group.frames.map((frame) => frame.y)),
          maxX: Math.max(...group.frames.map((frame) => frame.x + frame.width)),
          maxY: Math.max(...group.frames.map((frame) => frame.y + frame.height)),
        }
      : undefined
    startDrag(event, childUuids.length ? childUuids : [groupUuid], childUuids.length ? undefined : frameBounds)
  }

  const moveDrag = (event: PointerEvent<HTMLElement>) => {
    const session = dragSessionRef.current
    if (!session || session.pointerId !== event.pointerId) return
    const requestedX = event.clientX - session.startClientX
    const requestedY = event.clientY - session.startClientY
    const deltaX = Math.max(-session.bounds.minX, Math.min(session.canvas.width - session.bounds.maxX, requestedX))
    const deltaY = Math.max(-session.bounds.minY, Math.min(session.canvas.height - session.bounds.maxY, requestedY))
    setNodeOffsets((current) => {
      const next = { ...current }
      session.nodeUuids.forEach((uuid) => {
        const initial = session.initialOffsets[uuid]
        next[uuid] = { x: initial.x + deltaX, y: initial.y + deltaY }
      })
      return next
    })
  }

  const endDrag = (event: PointerEvent<HTMLElement>) => {
    const session = dragSessionRef.current
    if (!session || session.pointerId !== event.pointerId) return
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    dragSessionRef.current = null
    setDraggingNodeUuids(new Set())
    setPreviewMaterialUuids(new Set())
  }

  const changeViewMode = () => {
    setViewMode((current) => current === 'focus' ? 'complete' : 'focus')
    setNodeOffsets({})
    setDraggingNodeUuids(new Set())
    dragSessionRef.current = null
  }

  const endpointFor = (nodeUuid: string): EdgeEndpoint | undefined => {
    if (selectedEdge?.sourceNodeUuid === nodeUuid) return 'source'
    if (selectedEdge?.targetNodeUuid === nodeUuid) return 'target'
    return undefined
  }

  const clearEdgeSelectionFromBlankSpace = (event: MouseEvent<HTMLElement>) => {
    const target = event.target
    if (!(target instanceof Element)) return
    if (target.closest('.workflow-dag-edge-control, .workflow-dag-node, .workflow-dag-group-frame')) return
    setSelectedEdgeUuid(undefined)
  }

  return (
    <section ref={regionRef} className="workflow-dag-region" role="region" aria-label="发布修订拓扑" tabIndex={0}>
      <div className="workflow-dag-summary">
        <span><strong>{nodes.length}</strong> 个节点</span>
        {graphProjection.displayNodes.length < nodes.length ? <span>当前显示 {graphProjection.displayNodes.length} 个节点，实验操作内部已收起</span> : null}
        <span><strong>{edges.length}</strong> 条权威连线</span>
        <span><strong>{layout.groups.filter((group) => !workflowControlKind(group.node)).length}</strong> 个分组</span>
        <span><strong>{graphProjection.controlRegions.length}</strong> 个控制域</span>
        <span><strong>{graphProjection.controlEdges.length}</strong> 条调度关系</span>
        {viewMode === 'focus' ? (
          <span>{graphProjection.dependencyCount} 条流程依赖 · {graphProjection.parallelControlCount} 条并行控制 · {graphProjection.materialEdges.length} 条物料输入</span>
        ) : null}
        <em>蛇形布局 · 每行最多 {layout.ranksPerBand} 个拓扑层</em>
        <button
          type="button"
          className="workflow-dag-view-toggle"
          aria-label="恢复自动布局"
          disabled={!hasCustomLayout}
          onClick={() => setNodeOffsets({})}
        >
          <RotateCcw size={13} />恢复布局
        </button>
        <button
          type="button"
          className="workflow-dag-view-toggle"
          aria-label={viewMode === 'focus' ? '查看完整 DAG' : '查看主流程'}
          onClick={changeViewMode}
        >
          {viewMode === 'focus' ? <Network size={13} /> : <ListTree size={13} />}
          {viewMode === 'focus' ? '完整 DAG' : '主流程'}
        </button>
      </div>
      <div className="workflow-dag-legend" aria-label="连线图例">
        <span className="dependency">流程依赖</span>
        <span className="material">物料输入</span>
        <span className="parallel">并行入口 / 汇合</span>
        <span className="control">条件 / 循环控制</span>
        <small>实线为权威 DAG；控制虚线由调度参数投影，不写回动作 Edge</small>
      </div>
      {selectedEdge && selectedSource && selectedTarget && selectedKind ? (
        <div className={`workflow-dag-relation ${selectedKind}`} role="status">
          <strong>{edgeKindMetadata[selectedKind].label}</strong>
          <span>{selectedSource.name} → {selectedTarget.name}</span>
          <small>{edgeKindMetadata[selectedKind].description}</small>
          <button type="button" aria-label="清除连线选择" onClick={() => setSelectedEdgeUuid(undefined)}><X size={13} /></button>
        </div>
      ) : null}
      {loading && !nodes.length ? <div className="workflow-dag-state">正在计算拓扑布局…</div> : null}
      {error ? (
        <div className="workflow-dag-state error">
          <TriangleAlert size={16} />
          <span>工作流图读取失败，未生成推测拓扑。</span>
          {onRetry ? (
            <button type="button" disabled={loading} onClick={onRetry} aria-label="重新读取工作流图">
              <RotateCcw size={13} />重新读取
            </button>
          ) : null}
        </div>
      ) : null}
      {!loading && !error && !nodes.length ? <div className="workflow-dag-state">该修订没有工作流节点。</div> : null}
      {nodes.length ? (
        <>
          {viewMode === 'focus' && graphProjection.materialNodes.length ? (
            <section className="workflow-dag-materials" role="region" aria-label="物料输入">
              <div className="workflow-dag-materials-heading">
                <span><PackageOpen size={14} /><strong>物料输入</strong></span>
                <small>{graphProjection.materialNodes.length} 个来源 · 在消费节点上就近标记，不绘制跨画布长线</small>
              </div>
              <div className="workflow-dag-material-list">
                {graphProjection.materialNodes.map((material) => {
                  const outgoingEdges = graphProjection.materialEdges.filter((edge) => edge.sourceNodeUuid === material.uuid)
                  const endpoint = endpointFor(material.uuid)
                  return (
                    <article
                      className={`workflow-dag-material ${endpoint ? `edge-${endpoint}` : ''} ${previewMaterialUuids.has(material.uuid) ? 'material-preview' : ''}`}
                      aria-label={[material.name, '物料源', endpoint === 'source' ? '已选连线起点' : endpoint === 'target' ? '已选连线终点' : undefined].filter(Boolean).join('，')}
                      data-node-uuid={material.uuid}
                      key={material.uuid}
                    >
                      <CircleDot size={13} />
                      <div><strong>{material.name}</strong><small>物料源</small></div>
                      <div className="workflow-dag-material-targets">
                        {outgoingEdges.map((edge) => {
                          const target = graphProjection.nodeByUuid.get(edge.targetNodeUuid)
                          const targetName = target?.authoringResultName || target?.name || edge.targetNodeUuid
                          const label = `${edgeKindMetadata.material.label}：${material.name} → ${target?.name || targetName}`
                          return (
                            <button
                              className={selectedEdgeUuid === edge.uuid ? 'selected' : ''}
                              data-edge-uuid={edge.uuid}
                              key={edge.uuid}
                              type="button"
                              aria-label={label}
                              aria-pressed={selectedEdgeUuid === edge.uuid}
                              title={`输入到 ${targetName}`}
                              onClick={() => selectEdge(edge.uuid)}
                            >→ {targetName}</button>
                          )
                        })}
                        {!outgoingEdges.length ? <span>未连接</span> : null}
                      </div>
                    </article>
                  )
                })}
              </div>
            </section>
          ) : null}
          <div ref={scrollRef} className="workflow-dag-scroll" onClick={clearEdgeSelectionFromBlankSpace}>
            <div
              className="workflow-dag-stage"
              style={{
                width: Math.max(layout.width, viewportSize.width),
                height: Math.max(layout.height, viewportSize.height),
              }}
            >
              <svg
                className="workflow-dag-edges"
                width={Math.max(layout.width, viewportSize.width)}
                height={Math.max(layout.height, viewportSize.height)}
              >
                <defs>
                  {(Object.keys(edgeKindMetadata) as WorkflowEdgeKind[]).map((kind) => (
                    <marker
                      className={`workflow-dag-arrow ${kind}`}
                      id={`workflow-dag-arrow-${kind}`}
                      key={kind}
                      viewBox="0 0 8 8"
                      refX="7"
                      refY="4"
                      markerWidth="7"
                      markerHeight="7"
                      orient="auto-start-reverse"
                    >
                      <path d="M 0 0 L 8 4 L 0 8 z" />
                    </marker>
                  ))}
                </defs>
                {positionedEdges.map(({ edge, source, target, path }) => {
                  const displayEdge = edge as WorkflowDisplayEdge
                  const kind = graphProjection.edgeKindByUuid.get(edge.uuid) || 'dependency'
                  const selected = edge.uuid === selectedEdgeUuid
                  const relationLabel = graphProjection.edgeLabelByUuid.get(edge.uuid) || edgeKindMetadata[kind].label
                  const label = `${relationLabel}：${source.node.name} → ${target.node.name}`
                  const labelX = (source.x + source.width / 2 + target.x + target.width / 2) / 2
                  const labelY = kind === 'repeat_continue'
                    ? Math.max(source.y + source.height, target.y + target.height) + 24
                    : (source.y + source.height / 2 + target.y + target.height / 2) / 2 - 8
                  return (
                    <g
                      className={`workflow-dag-edge-control ${kind} ${selected ? 'selected' : ''}`}
                      data-edge-uuid={edge.uuid}
                      key={edge.uuid}
                      role="button"
                      tabIndex={0}
                      aria-label={label}
                      aria-pressed={selected}
                      onClick={() => selectEdge(edge.uuid)}
                      onKeyDown={(event) => selectEdgeWithKeyboard(event, edge.uuid)}
                    >
                      <path className="workflow-dag-edge-hit" d={path} aria-hidden="true" />
                      <path
                        className="workflow-dag-edge"
                        d={path}
                        markerEnd={`url(#workflow-dag-arrow-${kind})`}
                        aria-hidden="true"
                      />
                      {displayEdge.displayKind ? (
                        <text className="workflow-dag-edge-label" x={labelX} y={labelY} textAnchor="middle" aria-hidden="true">
                          {relationLabel}
                        </text>
                      ) : null}
                    </g>
                  )
                })}
              </svg>
              {layout.groups.map((group) => (
                <div
                  className={`workflow-dag-group ${workflowControlKind(group.node) ? `control-${workflowControlKind(group.node)}` : ''} ${draggingNodeUuids.has(group.node.uuid) || group.children.some((child) => draggingNodeUuids.has(child.node.uuid)) ? 'dragging' : ''}`}
                  data-node-uuid={group.node.uuid}
                  data-rank={group.rank}
                  data-band={group.band}
                  key={group.node.uuid}
                  role="group"
                  aria-label={workflowControlKind(group.node) === 'condition'
                    ? `条件控制域：${group.node.name}`
                    : workflowControlKind(group.node) === 'repeat_until'
                      ? `循环控制域：${group.node.name}`
                      : `分组：${group.node.name}`}
                >
                  {group.frames.map((frame, index) => {
                    const controlRegion = graphProjection.controlRegions.find((region) => region.node.uuid === group.node.uuid)
                    return (
                    <div
                      className={`workflow-dag-group-frame ${controlRegion ? 'control-region' : ''}`}
                      key={`${group.node.uuid}-${index}`}
                      onPointerDown={(event) => startGroupDrag(event, group.node.uuid)}
                      onPointerMove={moveDrag}
                      onPointerUp={endDrag}
                      onPointerCancel={endDrag}
                      style={{ left: frame.x, top: frame.y, width: frame.width, height: frame.height }}
                    >
                      <div className="workflow-dag-group-title">
                        {controlRegion?.kind === 'condition'
                          ? <GitBranch size={14} />
                          : controlRegion?.kind === 'repeat_until'
                            ? <Repeat2 size={14} />
                            : <Layers3 size={14} />}
                        <strong>{group.node.name}</strong>
                        {controlRegion ? <small>{controlRegion.subtitle}</small> : null}
                      </div>
                    </div>
                    )
                  })}
                  <span className="sr-only">{group.children.map((child) => child.node.name).join('、')}</span>
                </div>
              ))}
              {layout.nodes.map((positioned) => {
                const endpoint = endpointFor(positioned.node.uuid)
                const materialInputs = viewMode === 'focus'
                  ? graphProjection.materialInputsByTarget.get(positioned.node.uuid)
                  : undefined
                return (
                  <WorkflowNodeCard
                    key={positioned.node.uuid}
                    positioned={positioned}
                    materialInputs={materialInputs}
                    controlBadges={graphProjection.nodeBadges.get(positioned.node.uuid)}
                    edgeEndpoint={endpoint}
                    dimmed={Boolean(selectedEdge) && !endpoint}
                    dragging={draggingNodeUuids.has(positioned.node.uuid)}
                    onPointerDown={(event) => startNodeDrag(event, positioned)}
                    onPointerMove={moveDrag}
                    onPointerUp={endDrag}
                    previewingMaterials={Boolean(materialInputs?.some((material) => previewMaterialUuids.has(material.uuid)))}
                    onMaterialPreviewStart={(materialUuids) => setPreviewMaterialUuids(new Set(materialUuids))}
                    onMaterialPreviewEnd={() => setPreviewMaterialUuids(new Set())}
                  />
                )
              })}
            </div>
          </div>
        </>
      ) : null}
      {layout.warnings.length ? (
        <div className="workflow-dag-warning"><TriangleAlert size={14} />{layout.warnings.join(' ')}</div>
      ) : null}
    </section>
  )
}
