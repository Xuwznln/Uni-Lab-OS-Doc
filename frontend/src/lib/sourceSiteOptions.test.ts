import { describe, expect, it } from 'vitest'
import { sourceSiteOptions } from './sourceSiteOptions'
import type { MaterialRecord, WorkflowGraph } from '../types'

const owner = { uuid: 'owner', name: '工作位', sites: [{ uuid: 'site-a', name: 'A' }] } as MaterialRecord

function graph(supports: boolean, branch = false): WorkflowGraph {
  return { nodes: [
    { uuid: 'condition', kind: 'condition', type: 'condition', name: '判断', disabled: false },
    { uuid: 'source', kind: 'material_source', type: 'material_source', name: '物料', disabled: false,
      parentUuid: branch ? 'condition' : undefined,
      param: { mount: { uuid: 'owner' }, resource_template_uuid: 'bottle', supports_replenishment: supports },
      meta_data: { unilab: { material_source_site_binding: { parameter: 'work_site' } } } },
  ] } as WorkflowGraph
}

describe('运行时来源库位输入', () => {
  it('支持补料的来源可选择空库位', () => {
    expect(sourceSiteOptions(graph(true), [], [owner]).get('work_site')).toEqual([
      { value: 'A', label: '工作位 / A · 空库位，执行时检查', materialUuid: '' },
    ])
  })
  it('备用分支为空不在提交前阻塞任务', () => {
    expect(sourceSiteOptions(graph(false, true), [], [owner]).get('work_site')).toHaveLength(1)
  })
  it('旧来源保留有料库位限制', () => {
    expect(sourceSiteOptions(graph(false), [], [owner]).get('work_site')).toEqual([])
  })
})
