import { describe, expect, it } from 'vitest'
import type { WorkflowTask } from '../types'
import type { TimeScheduleSnapshot } from './timeScheduleClient'
import { buildTaskGantt } from './taskGantt'

const task = {
  uuid: 'task-1', workflowUuid: 'workflow-1', workflowName: '测试流程', status: 'pending',
  priority: 'normal', sample: 'sample-1', description: '', current: '', progress: 0,
  updatedAt: '2026-01-01T00:00:00Z', nodes: [
    { uuid: 'node-later', name: '后续动作', kind: 'device_action', index: 1, status: 'pending' },
    { uuid: 'node-first', name: '先行动作', kind: 'device_action', index: 0, status: 'pending' },
  ], materialUuids: [], runMode: 'normal', executionMode: 'normal', dispatchGate: 'active',
  controlStatus: 'active', matrixGroupKey: 'group-1',
} as WorkflowTask

const candidate = {
  revision: 'revision-1', time_origin: 1000, status: 'OPTIMAL', validated: true, error: '', nodes: [
    { key: ['task-1', 'node-later'], device_id: 'device-1', start_ms: 10_000, end_ms: 12_000, fixed: false },
    { key: ['task-1', 'node-first'], device_id: 'device-1', start_ms: 0, end_ms: 10_000, fixed: false },
  ], completion_targets: [],
} satisfies NonNullable<TimeScheduleSnapshot['latest']>

describe('任务甘特图排程展示', () => {
  it('按候选开始时间展示后续待运行节点，而不是按任务节点插入顺序', () => {
    const [row] = buildTaskGantt([task], candidate, [], 999_000)

    expect(row.bars.map((bar) => [bar.nodeUuid, bar.kind])).toEqual([
      ['node-first', 'planned'],
      ['node-later', 'planned'],
    ])
    expect(row.bars[1].startMs).toBeGreaterThan(row.bars[0].startMs)
  })

  it('候选包含尚未同步到任务详情的后续节点时仍按时间展示', () => {
    const candidateWithUnsyncedNode = {
      ...candidate,
      nodes: [
        ...candidate.nodes,
        { key: ['task-1', 'node-last'], device_id: 'device-2', start_ms: 20_000, end_ms: 25_000, fixed: false },
      ],
    } satisfies NonNullable<TimeScheduleSnapshot['latest']>

    const [row] = buildTaskGantt([task], candidateWithUnsyncedNode, [], 999_000)

    expect(row.bars.map((bar) => [bar.nodeUuid, bar.kind])).toEqual([
      ['node-first', 'planned'],
      ['node-later', 'planned'],
      ['node-last', 'planned'],
    ])
    expect(row.bars.at(-1)?.name).toBe('node-last')
    expect(row.bars.at(-1)?.deviceId).toBe('device-2')
  })

  it('完整实际时间带有不确定原因时仍保留待核对提示', () => {
    const uncertainTask = {
      ...task,
      nodes: [{
        ...task.nodes[0], status: 'attention' as const,
        job: {
          uuid: 'job-1', executionStatus: 'failed', uncertaintyReason: 'result_not_confirmed',
          param: {}, feedbackData: {}, returnInfo: {}, errorInfo: [],
          startedAt: '2026-01-01T00:00:01Z', finishedAt: '2026-01-01T00:00:03Z',
        },
      }],
    } as WorkflowTask

    const [row] = buildTaskGantt([uncertainTask], null, [], Date.parse('2026-01-01T00:00:10Z'))

    expect(row.bars).toHaveLength(1)
    expect(row.bars[0].kind).toBe('actual')
    expect(row.pending).toEqual([{ nodeUuid: 'node-later', name: '后续动作', reason: '实际执行状态需要核对，结束时间待定' }])
  })

  it('完整实际时间带有人工确认请求时保留待确认提示', () => {
    const confirmationTask = {
      ...task,
      nodes: [{
        ...task.nodes[0],
        job: {
          uuid: 'job-2', executionStatus: 'succeeded',
          param: {}, feedbackData: {}, returnInfo: {}, errorInfo: [],
          startedAt: '2026-01-01T00:00:01Z', finishedAt: '2026-01-01T00:00:03Z',
          manualConfirmation: { status: 'pending' as const, deadlineAt: '', actions: ['approve' as const] },
        },
      }],
    } as WorkflowTask

    const [row] = buildTaskGantt([confirmationTask], null, [], Date.parse('2026-01-01T00:00:10Z'))

    expect(row.pending[0]?.reason).toBe('等待人工确认，后续时间待定')
  })
})
