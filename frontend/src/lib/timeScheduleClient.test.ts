import { afterEach, describe, expect, it, vi } from 'vitest'
import { currentTimeCandidate, loadTimeSchedule, mergePartialTimeCandidate } from './timeScheduleClient'
import type { TimeScheduleCandidate, TimeScheduleSnapshot } from './timeScheduleClient'

afterEach(() => vi.unstubAllGlobals())

function response(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response
}

function snapshotWith(latest: Partial<TimeScheduleCandidate> | null, extra: Partial<TimeScheduleSnapshot> = {}): TimeScheduleSnapshot {
  return {
    enabled: true,
    algorithm: 'cp-sat',
    active_solves: 0,
    input_error: '',
    input: { revision: 'rev-1', partial_tasks: [], blocked: [], completion_targets: [] },
    latest: latest === null ? null : {
      revision: 'rev-1', time_origin: 0, status: 'OPTIMAL', validated: true, error: '',
      nodes: [{ key: ['task-1', 'measure'], device_id: 'sensor', start_ms: 0, end_ms: 100, fixed: false }],
      completion_targets: [],
      ...latest,
    },
    ...extra,
  }
}

describe('currentTimeCandidate', () => {
  it('同版本且已验证的可行候选才可展示', () => {
    expect(currentTimeCandidate(snapshotWith({}), true, false)).not.toBeNull()
  })

  it('候选未通过服务端校验时不展示', () => {
    // 服务端仍在投影里保留 status 与 error 供诊断；消费端必须自己按 validated 把门，
    // 不能把调度器自己拒绝执行的安排画到时间轴上。
    expect(currentTimeCandidate(snapshotWith({ validated: false }), true, false)).toBeNull()
  })

  it.each(['INFEASIBLE', 'UNKNOWN', 'ERROR', 'MODEL_INVALID'])('求解状态 %s 时不展示', (status) => {
    expect(currentTimeCandidate(snapshotWith({ status, nodes: [] }), true, false)).toBeNull()
  })

  it('候选版本与当前输入不一致时不沿用旧预测', () => {
    expect(currentTimeCandidate(snapshotWith({ revision: 'rev-0' }), true, false)).toBeNull()
  })

  it('输入自身报错、未启用、连接中断或读取失败时都不展示', () => {
    expect(currentTimeCandidate(snapshotWith({}, { input_error: '快照构建失败' }), true, false)).toBeNull()
    expect(currentTimeCandidate(snapshotWith({}, { enabled: false }), true, false)).toBeNull()
    expect(currentTimeCandidate(snapshotWith({}), false, false)).toBeNull()
    expect(currentTimeCandidate(snapshotWith({}), true, true)).toBeNull()
    expect(currentTimeCandidate(undefined, true, false)).toBeNull()
    expect(currentTimeCandidate(snapshotWith(null), true, false)).toBeNull()
  })
})

describe('loadTimeSchedule', () => {
  it('保留输入和候选中的完成语义目标', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response({
      enabled: true,
      algorithm: 'cp-sat',
      active_solves: 0,
      input_error: '',
      input: {
        revision: 'rev-1', partial_tasks: ['task-1'], blocked: [],
        completion_targets: [{
          task_id: 'task-1', node_keys: [['task-1', 'measure']], kind: 'information', prefix_id: 'if-1',
        }],
      },
      latest: {
        revision: 'rev-1', time_origin: 1000, status: 'OPTIMAL', validated: true, error: '',
        nodes: [{ key: ['task-1', 'measure'], device_id: 'sensor', start_ms: 0, end_ms: 100, fixed: false, parameters_final: false }],
        completion_targets: [{
          task_id: 'task-1', node_keys: [['task-1', 'measure']], kind: 'information', prefix_id: 'if-1',
        }],
      },
    })))

    const snapshot = await loadTimeSchedule()

    expect(snapshot.input.completion_targets).toEqual([{
      task_id: 'task-1', node_keys: [['task-1', 'measure']], kind: 'information', prefix_id: 'if-1',
    }])
    expect(snapshot.latest?.completion_targets[0]).toMatchObject({ task_id: 'task-1', kind: 'information' })
    expect(snapshot.latest?.nodes[0].parameters_final).toBe(false)
  })

  it('在部分候选缺少后缀时保留上一轮节点并重基时间原点', () => {
    const previous: TimeScheduleCandidate = {
      revision: 'rev-0', time_origin: 1000, status: 'OPTIMAL', validated: true, error: '',
      completion_targets: [], nodes: [
        { key: ['task-1', 'future'], device_id: 'robot', start_ms: 12_000, end_ms: 13_000, fixed: false },
        { key: ['task-2', 'future'], device_id: 'robot', start_ms: 12_000, end_ms: 13_000, fixed: false },
      ],
    }
    const current: TimeScheduleCandidate = {
      revision: 'rev-1', time_origin: 1010, status: 'OPTIMAL', validated: true, error: '',
      completion_targets: [], nodes: [
        { key: ['task-1', 'prefix'], device_id: 'robot', start_ms: 500, end_ms: 700, fixed: true },
      ],
    }

    const merged = mergePartialTimeCandidate(current, previous, ['task-1'])

    expect(merged?.nodes).toEqual([
      current.nodes[0],
      { ...previous.nodes[0], start_ms: 2_000, end_ms: 3_000 },
    ])
  })

  it('不把非部分任务的旧节点带入新候选', () => {
    const candidate = (revision: string, node: TimeScheduleCandidate['nodes'][number]): TimeScheduleCandidate => ({
      revision, time_origin: 1000, status: 'OPTIMAL', validated: true, error: '', completion_targets: [], nodes: [node],
    })
    const previous = candidate('rev-0', { key: ['task-2', 'old'], device_id: 'robot', start_ms: 100, end_ms: 200, fixed: false })
    const current = candidate('rev-1', { key: ['task-1', 'new'], device_id: 'robot', start_ms: 100, end_ms: 200, fixed: false })

    expect(mergePartialTimeCandidate(current, previous, ['task-1'])?.nodes).toEqual(current.nodes)
  })

  it('兼容旧接口缺少完成语义字段的响应', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response({
      enabled: false, algorithm: 'stable-ready-order', active_solves: 0, input_error: '',
      input: { revision: '', partial_tasks: [], blocked: [] }, latest: null,
    })))

    const snapshot = await loadTimeSchedule()

    expect(snapshot.input.completion_targets).toEqual([])
  })

  it('拒绝未定义的完成语义类型，避免把未知目标当作任务完成', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response({
      enabled: true, algorithm: 'cp-sat', active_solves: 0, input_error: '',
      input: {
        revision: 'rev-1', partial_tasks: [], blocked: [],
        completion_targets: [{ task_id: 'task-1', node_keys: [], kind: 'unknown', prefix_id: '' }],
      }, latest: null,
    })))

    await expect(loadTimeSchedule()).rejects.toThrow('时间计划完成目标类型无效')
  })
})
