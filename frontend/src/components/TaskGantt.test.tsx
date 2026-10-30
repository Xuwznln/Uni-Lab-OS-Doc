import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowTask } from '../types'
import type { TimeScheduleSnapshot } from '../lib/timeScheduleClient'
import { TaskGantt } from './TaskGantt'

let scheduleData: TimeScheduleSnapshot | undefined
vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: scheduleData, isError: false, isFetching: false, refetch: vi.fn() }),
}))

const NOW = new Date('2026-09-22T14:00:00').getTime()
const iso = (offset: number) => new Date(NOW + offset).toISOString()

function runningTask(): WorkflowTask {
  return {
    uuid: 'task-paused-and-resumed', workflowUuid: 'workflow-1', workflowName: '暂停后继续的流程',
    status: 'running', executionStatus: 'running', priority: 'normal', sample: 'sample-001',
    description: '', current: '当前动作', progress: 50, updatedAt: iso(0), materialUuids: [],
    runMode: 'normal', executionMode: 'normal', dispatchGate: 'active', controlStatus: 'active',
    matrixGroupKey: 'group-1', nodes: [
      {
        uuid: 'node-before-pause', name: '暂停前动作', kind: 'device_action', index: 0, status: 'succeeded',
        job: { uuid: 'job-old', param: {}, feedbackData: {}, returnInfo: {}, errorInfo: [],
          startedAt: iso(-600_000), finishedAt: iso(-590_000), executionStatus: 'succeeded' },
      },
      {
        uuid: 'node-current', name: '当前动作', kind: 'device_action', index: 1, status: 'running',
        job: { uuid: 'job-current', param: {}, feedbackData: {}, returnInfo: {}, errorInfo: [],
          startedAt: iso(-10_000), executionStatus: 'running' },
      },
    ],
  }
}

function historicalTask(): WorkflowTask {
  return {
    ...runningTask(), uuid: 'task-history', status: 'succeeded', executionStatus: 'succeeded',
    progress: 100, nodes: [
      {
        uuid: 'node-history', name: '历史动作', kind: 'device_action', index: 0, status: 'succeeded',
        job: { uuid: 'job-history', param: {}, feedbackData: {}, returnInfo: {}, errorInfo: [],
          startedAt: iso(-600_000), finishedAt: iso(-590_000), executionStatus: 'succeeded' },
      },
    ],
  }
}

function colorTask(): WorkflowTask {
  return {
    ...runningTask(), uuid: 'task-colors', workflowName: '动作配色流程',
    nodes: [
      {
        uuid: 'node-actual', name: 'same_action', kind: 'device_action', index: 0, status: 'succeeded',
        job: { uuid: 'job-actual', param: {}, feedbackData: {}, returnInfo: {}, errorInfo: [],
          startedAt: iso(-30_000), finishedAt: iso(-20_000), executionStatus: 'succeeded' },
      },
      {
        uuid: 'node-running', name: 'same_action', kind: 'device_action', index: 1, status: 'running',
        job: { uuid: 'job-running', param: {}, feedbackData: {}, returnInfo: {}, errorInfo: [],
          startedAt: iso(-10_000), executionStatus: 'running' },
      },
      { uuid: 'node-planned', name: 'other_action', kind: 'device_action', index: 2, status: 'pending' },
    ],
  }
}

function mount(tasks = [runningTask()]) {
  const onSelectNode = vi.fn()
  const view = render(<TaskGantt connected tasks={tasks} selectedId={tasks[0]?.uuid || ''}
    onSelect={vi.fn()} onSelectNode={onSelectNode} />)
  return { ...view, onSelectNode }
}

function caption(container: HTMLElement) {
  return container.querySelector('.gantt-window-caption')?.textContent || ''
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
  scheduleData = undefined
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  scheduleData = undefined
})

describe('甘特图时间窗口', () => {
  it('暂停产生长间隔后，当前窗口保持三分钟，全部时间仍可查看暂停前历史', () => {
    const { container } = mount()
    expect(caption(container)).toContain('09/22 13:59:00 — 09/22 14:02:00')
    expect(screen.queryByRole('button', { name: /暂停前动作 · 实际执行/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /当前动作 · 已运行/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '当前 / 最近' })).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(screen.getByRole('button', { name: '全部时间' }))
    expect(screen.getByRole('button', { name: /暂停前动作 · 实际执行/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '全部时间' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('点击现在后随时间推进，不冻结在点击时刻', () => {
    const { container } = mount()
    fireEvent.click(screen.getByRole('button', { name: '全部时间' }))
    fireEvent.click(screen.getByRole('button', { name: '现在' }))
    act(() => { vi.advanceTimersByTime(10_000) })
    expect(caption(container)).toContain('09/22 13:59:10 — 09/22 14:02:10')
    expect(screen.getByRole('button', { name: '当前 / 最近' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('当前窗口保留活动任务中 CP-SAT 排好的后续节点', () => {
    const task = runningTask()
    scheduleData = {
      enabled: true, algorithm: 'CP-SAT', active_solves: 0, input_error: '',
      input: { revision: 'revision-future', partial_tasks: [], blocked: [], completion_targets: [] },
      latest: {
        revision: 'revision-future', time_origin: Math.floor(NOW / 1000), status: 'OPTIMAL', validated: true, error: '', completion_targets: [],
        nodes: [{ key: [task.uuid, 'node-future'], device_id: 'device-1', start_ms: 300_000, end_ms: 330_000, fixed: false }],
      },
    }
    const { container } = mount([task])

    expect(caption(container)).toContain('09/22 14:05:30')
    expect(screen.getByRole('button', { name: /node-future · 预计执行/ })).toBeInTheDocument()
  })

  it('部分候选刷新时保留上一轮同任务的后续预计条', () => {
    const task = runningTask()
    const timeOrigin = Math.floor(NOW / 1000)
    scheduleData = {
      enabled: true, algorithm: 'CP-SAT', active_solves: 0, input_error: '',
      input: { revision: 'revision-full', partial_tasks: [], blocked: [], completion_targets: [] },
      latest: {
        revision: 'revision-full', time_origin: timeOrigin, status: 'OPTIMAL', validated: true, error: '', completion_targets: [],
        nodes: [{ key: [task.uuid, 'node-future'], device_id: 'device-1', start_ms: 300_000, end_ms: 330_000, fixed: false }],
      },
    }
    const view = mount([task])
    expect(screen.getByRole('button', { name: /node-future · 预计执行/ })).toBeInTheDocument()

    scheduleData = {
      enabled: true, algorithm: 'CP-SAT', active_solves: 0, input_error: '',
      input: { revision: 'revision-partial', partial_tasks: [task.uuid], blocked: [], completion_targets: [] },
      latest: {
        revision: 'revision-partial', time_origin: timeOrigin + 10, status: 'OPTIMAL', validated: true, error: '', completion_targets: [],
        nodes: [{ key: [task.uuid, 'node-current'], device_id: 'device-1', start_ms: 500, end_ms: 1_500, fixed: true }],
      },
    }
    act(() => view.rerender(<TaskGantt connected tasks={[task]} selectedId={task.uuid} onSelect={vi.fn()} onSelectNode={vi.fn()} />))

    expect(screen.getByRole('button', { name: /node-future · 预计执行/ })).toBeInTheDocument()
  })

  it('没有活动任务时，当前窗口聚焦最近历史而不是当前时刻的空白', () => {
    const { container } = mount([historicalTask()])
    expect(caption(container)).toContain('13:49:10 — 09/22 13:52:10')
    expect(caption(container)).not.toContain('14:00:00')
    expect(screen.getByRole('button', { name: /历史动作 · 实际执行/ })).toBeInTheDocument()
  })

  it('时间轴支持拖动平移，并且定时刷新不会将手动窗口拉回', () => {
    const { container } = mount()
    fireEvent.click(screen.getByRole('button', { name: '全部时间' }))
    const axis = screen.getByRole('group', { name: '时间刻度，左右拖动查看时间' })
    Object.defineProperty(axis, 'getBoundingClientRect', { value: () => ({ left: 0, right: 800, width: 800, top: 0, bottom: 48 }) })
    Object.defineProperty(axis, 'setPointerCapture', { value: vi.fn() })
    Object.defineProperty(axis, 'releasePointerCapture', { value: vi.fn() })
    Object.defineProperty(axis, 'hasPointerCapture', { value: vi.fn(() => true) })
    const before = caption(container)
    fireEvent.pointerDown(axis, { pointerId: 7, clientX: 400, buttons: 1 })
    fireEvent.pointerMove(axis, { pointerId: 7, clientX: 520, buttons: 1 })
    fireEvent.pointerUp(axis, { pointerId: 7, clientX: 520 })
    const afterDrag = caption(container)
    expect(afterDrag).not.toBe(before)
    act(() => { vi.advanceTimersByTime(5_000) })
    expect(caption(container)).toBe(afterDrag)
  })

  it('时间轴可用左右键平移，且具有可访问名称', () => {
    const { container } = mount()
    const axis = screen.getByRole('group', { name: '时间刻度，左右拖动查看时间' })
    expect(axis).toHaveAttribute('tabindex', '0')
    const before = caption(container)
    fireEvent.keyDown(axis, { key: 'ArrowLeft' })
    expect(caption(container)).not.toBe(before)
  })

  it('点击任务条只选择节点，不触发时间轴拖动', () => {
    const { container, onSelectNode } = mount()
    const before = caption(container)
    fireEvent.click(screen.getByRole('button', { name: /当前动作 · 已运行/ }))
    expect(onSelectNode).toHaveBeenCalledWith('task-paused-and-resumed', 'node-current')
    expect(onSelectNode).toHaveBeenCalledTimes(1)
    expect(caption(container)).toBe(before)
  })

  it('相同动作保持同色，不同动作区分颜色，并按执行阶段区分边框状态', () => {
    const task = colorTask()
    const timeOrigin = Math.floor(NOW / 1000)
    scheduleData = {
      enabled: true, algorithm: 'CP-SAT', active_solves: 0, input_error: '',
      input: { revision: 'revision-colors', partial_tasks: [], blocked: [], completion_targets: [] },
      latest: {
        revision: 'revision-colors', time_origin: timeOrigin, status: 'OPTIMAL', validated: true, error: '', completion_targets: [],
        nodes: [{ key: [task.uuid, 'node-planned'], device_id: 'device-2', start_ms: 30_000, end_ms: 40_000, fixed: false }],
      },
    }
    mount([task])
    const sameActionBars = screen.getAllByRole('button', { name: /动作配色流程 · same_action/ })
    const otherActionBar = screen.getByRole('button', { name: /动作配色流程 · other_action/ })
    expect(sameActionBars).toHaveLength(2)
    expect(sameActionBars[0]).toHaveClass('gantt-bar-actual')
    expect(sameActionBars[1]).toHaveClass('gantt-bar-running')
    expect(otherActionBar).toHaveClass('gantt-bar-planned')
    expect(sameActionBars[0]).toHaveClass('gantt-status-succeeded')
    expect(sameActionBars[1]).toHaveClass('gantt-status-running')
    expect(sameActionBars[0].style.getPropertyValue('--gantt-action-color')).toBeTruthy()
    expect(sameActionBars[0].style.getPropertyValue('--gantt-action-color')).toBe(sameActionBars[1].style.getPropertyValue('--gantt-action-color'))
    expect(otherActionBar.style.getPropertyValue('--gantt-action-color')).not.toBe(sameActionBars[0].style.getPropertyValue('--gantt-action-color'))
  })
})
