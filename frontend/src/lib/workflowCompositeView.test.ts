import { describe, expect, it } from 'vitest'
import type { WorkflowGraphNode } from '../types'
import { projectCompositeCalls } from './workflowCompositeView'
function node(uuid: string, parentUuid?: string, composite = false): WorkflowGraphNode {
  return { uuid, name: uuid, type: 'ILab', kind: 'action', disabled: false, parentUuid,
    meta_data: composite ? { unilab: { composite: { child_workflow_uuid: `${uuid}-workflow` } } } : {} }
}
describe('组合调用的画布投影', () => {
  it('折叠嵌套动作，保留跨边界连线且不伪造父子顺序', () => {
    const nodes = [node('before'), node('call', undefined, true), node('nested', 'call', true), node('action', 'nested'), node('after')]
    const original = JSON.stringify(nodes)
    const projection = projectCompositeCalls(nodes)
    expect(projection.nodes.map(n => n.uuid)).toEqual(['before', 'call', 'after'])
    const edges = projection.projectEdges([
      { uuid: 'input', sourceNodeUuid: 'before', targetNodeUuid: 'action' },
      { uuid: 'internal', sourceNodeUuid: 'nested', targetNodeUuid: 'action' },
      { uuid: 'output', sourceNodeUuid: 'action', targetNodeUuid: 'after' },
    ])
    expect(edges.map(e => [e.sourceNodeUuid, e.targetNodeUuid])).toEqual([['before', 'call'], ['call', 'after']])
    expect(JSON.stringify(nodes)).toBe(original)
  })
  it('普通分组和条件下的动作不被收起', () => {
    const group = { ...node('condition'), type: 'condition' }
    expect(projectCompositeCalls([group, node('action', 'condition')]).nodes).toHaveLength(2)
  })
})
