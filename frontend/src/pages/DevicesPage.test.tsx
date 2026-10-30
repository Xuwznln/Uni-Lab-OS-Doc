import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DeviceActionRunRequestError, TaskCommandRequestError } from '../lib/edgeClient'
import * as edgeClient from '../lib/edgeClient'
import { handlingDevice } from '../test/stationHandlingFixtures'
import type { ActionTemplateRecord, DeviceActionDefinition, DeviceRuntimeRecord, MaterialRecord } from '../types'
import { DevicesPage } from './DevicesPage'

vi.mock('../lib/edgeClient', async () => {
  const actual = await vi.importActual<typeof import('../lib/edgeClient')>('../lib/edgeClient')
  return {
    ...actual,
    loadActionTemplates: vi.fn(),
    loadDeviceRuntimeCatalog: vi.fn(),
    loadDeviceActionDefinition: vi.fn(),
    createDeviceActionRun: vi.fn(),
    loadDeviceActionRunStatus: vi.fn(),
    commandWorkflowTask: vi.fn(),
  }
})

const device: MaterialRecord = {
  ...handlingDevice,
  uuid: 'material-pump',
  name: '一号泵',
  sourceNodeId: 'pump-1',
  resourceTemplateUuid: 'pump-template',
  config: { category: 'device', simulator: true },
}

const plate: MaterialRecord = {
  ...handlingDevice,
  uuid: 'plate-1',
  name: '样品板一号',
  sourceNodeId: 'plate-A',
  resourceTemplateUuid: 'plate-template',
  category: 'plate',
  config: { category: 'plate' },
}

const template: ActionTemplateRecord = {
  uuid: 'template-hold',
  name: 'hold',
  displayName: '保持',
  description: '夹持-保持当前压力',
  type: 'UniLabJsonCommand',
  nodeType: 'device_action',
  resourceTemplate: { uuid: 'pump-template', name: 'pump', displayName: '注射泵' },
}

const runtime: DeviceRuntimeRecord = {
  localId: 'pump-1',
  materialUuid: 'material-pump',
  name: '一号泵',
  online: true,
  dispatchable: true,
  source: 'backend',
  actions: [{ name: 'hold', displayName: '保持', description: '夹持-保持当前压力', type: 'UniLabJsonCommand', riskLevel: 'normal', busy: false }],
}

const definition: DeviceActionDefinition = {
  action_id: 'template-hold',
  action_version: '',
  name: 'hold',
  display_name: '保持',
  description: '保持指定秒数',
  device_binding: { material_uuid: 'material-pump', edge_local_id: 'pump-1' },
  parameter_schema: {
    type: 'object',
    properties: {
      duration_seconds: { type: 'integer', title: '持续秒数', minimum: 1 },
      enabled: { type: 'boolean', title: '启用' },
      plate: {
        type: 'object', title: '样品板', 'x-unilabos-material-lock': true,
        allowed_resource_template_uuids: ['plate-template'],
      },
    },
    required: ['duration_seconds', 'enabled', 'plate'],
  },
  defaults: { duration_seconds: 2, enabled: true },
}

const fullCapabilities = {
  canEditDefinitions: true,
  canViewDrafts: true,
  canRunUnpublished: true,
  canDebug: true,
  canExecuteDeviceActions: true,
}

function renderPage(options: { connected?: boolean; canExecuteDeviceActions?: boolean } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  const callbacks = { onSelectDevice: vi.fn(), onOpenTask: vi.fn(), onRefresh: vi.fn(), onNotify: vi.fn() }
  return {
    ...render(<QueryClientProvider client={client}><DevicesPage
      materials={[device, plate]}
      connected={options.connected ?? true}
      capabilities={{ ...fullCapabilities, canExecuteDeviceActions: options.canExecuteDeviceActions ?? true }}
      {...callbacks}
    /></QueryClientProvider>),
    client,
    ...callbacks,
  }
}

async function selectAndFillAction() {
  fireEvent.click(await screen.findByRole('button', { name: /夹持-保持当前压力hold/ }))
  const duration = await screen.findByLabelText('持续秒数')
  expect(duration).toHaveValue(2)
  fireEvent.change(duration, { target: { value: '3' } })
  fireEvent.change(screen.getByLabelText('启用'), { target: { value: 'false' } })
  fireEvent.change(screen.getByLabelText('样品板'), { target: { value: 'plate-1' } })
  fireEvent.click(screen.getByLabelText(/我已核对设备现场和动作参数/))
}

beforeEach(() => {
  sessionStorage.clear()
  vi.mocked(edgeClient.loadActionTemplates).mockResolvedValue([template, {
    ...template, uuid: 'template-prime', name: 'prime', displayName: '灌注',
  }, {
    ...template, uuid: 'other-hold', resourceTemplate: { uuid: 'other-template', name: 'other', displayName: '其他设备' },
  }])
  vi.mocked(edgeClient.loadDeviceRuntimeCatalog).mockResolvedValue([runtime])
  vi.mocked(edgeClient.loadDeviceActionDefinition).mockResolvedValue(definition)
  vi.mocked(edgeClient.createDeviceActionRun).mockResolvedValue({
    taskUuid: 'task-1', jobUuid: 'job-1', created: true, taskStatus: 'running', jobStatus: 'dispatched',
  })
  vi.mocked(edgeClient.loadDeviceActionRunStatus).mockResolvedValue({
    taskUuid: 'task-1', jobUuid: 'job-1', taskStatus: 'succeeded', controlStatus: 'active',
    jobStatus: 'succeeded', actionName: 'hold', param: { duration_seconds: 3, enabled: false, plate: { uuid: 'plate-1' } },
    feedback: { progress: 1 }, result: { completed: true }, errors: [], finishedAt: '2026-09-13T08:00:00Z',
  })
  vi.mocked(edgeClient.commandWorkflowTask).mockResolvedValue({ status: 'succeeded' })
})

afterEach(() => {
  sessionStorage.clear()
  vi.clearAllMocks()
})

describe('DevicesPage', () => {
  it('动作参数读取失败后允许重新读取并恢复参数表单', async () => {
    vi.mocked(edgeClient.loadDeviceActionDefinition)
      .mockRejectedValueOnce(new Error('动作参数读取超时，请检查 Edge 服务后重试'))
      .mockResolvedValueOnce(definition)
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: /夹持-保持当前压力hold/ }))
    expect(await screen.findByRole('alert', {}, { timeout: 3_000 })).toHaveTextContent('动作参数读取超时')
    expect(edgeClient.loadDeviceActionDefinition).toHaveBeenCalledOnce()

    fireEvent.click(screen.getByRole('button', { name: '重新读取动作参数' }))

    expect(await screen.findByLabelText('持续秒数')).toHaveValue(2)
    expect(edgeClient.loadDeviceActionDefinition).toHaveBeenCalledTimes(2)
  })

  it('只展示设备运行时声明的匹配模板，并以类型化参数创建持久单点任务', async () => {
    const { onOpenTask } = renderPage()
    expect(await screen.findByText('一号泵')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /夹持-保持当前压力hold/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /灌注prime/ })).not.toBeInTheDocument()
    expect(screen.queryByText('其他设备')).not.toBeInTheDocument()

    await selectAndFillAction()
    fireEvent.click(screen.getByRole('button', { name: '执行单点动作' }))

    await waitFor(() => expect(edgeClient.createDeviceActionRun).toHaveBeenCalledOnce())
    expect(edgeClient.createDeviceActionRun).toHaveBeenCalledWith({
      materialUuid: 'material-pump',
      workflowNodeTemplateUuid: 'template-hold',
      param: { duration_seconds: 3, enabled: false, plate: { uuid: 'plate-1' } },
      executionTimeoutSeconds: 120,
      idempotencyKey: expect.any(String),
      description: '一号泵 · 保持',
    })
    expect(await screen.findByText('已取得明确终态')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '在任务页查看' }))
    expect(onOpenTask).toHaveBeenCalledWith('task-1')
  })

  it('结果未知时冻结参数，并用完全相同的意图重试', async () => {
    vi.mocked(edgeClient.createDeviceActionRun)
      .mockRejectedValueOnce(new DeviceActionRunRequestError('请求结果未知', true))
      .mockResolvedValueOnce({ taskUuid: 'task-1', jobUuid: 'job-1', created: false, taskStatus: 'running', jobStatus: 'running' })
    renderPage()
    await selectAndFillAction()
    fireEvent.click(screen.getByRole('button', { name: '执行单点动作' }))
    expect(await screen.findByText('上一条动作请求结果未知')).toBeInTheDocument()
    expect(screen.getByText(/原目标：一号泵 · 保持/)).toBeInTheDocument()
    expect(screen.getByLabelText('持续秒数')).toBeDisabled()
    expect(screen.getByRole('button', { name: /一号泵pump-1/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /夹持-保持当前压力hold/ })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: '重试原请求' }))
    await waitFor(() => expect(edgeClient.createDeviceActionRun).toHaveBeenCalledTimes(2))
    expect(vi.mocked(edgeClient.createDeviceActionRun).mock.calls[1][0])
      .toEqual(vi.mocked(edgeClient.createDeviceActionRun).mock.calls[0][0])
  })

  it('在发送动作 POST 前同步保存完整创建意图', async () => {
    let finishCreate: ((value: { taskUuid: string; jobUuid: string; created: boolean; taskStatus: string; jobStatus: string }) => void) | undefined
    vi.mocked(edgeClient.createDeviceActionRun).mockImplementation(() => new Promise((resolve) => { finishCreate = resolve }))
    renderPage()
    await selectAndFillAction()
    fireEvent.click(screen.getByRole('button', { name: '执行单点动作' }))
    await waitFor(() => expect(edgeClient.createDeviceActionRun).toHaveBeenCalledOnce())

    expect(JSON.parse(String(sessionStorage.getItem('unilab:device-action:uncertain-intent'))))
      .toEqual(vi.mocked(edgeClient.createDeviceActionRun).mock.calls[0][0])
    finishCreate?.({ taskUuid: 'task-1', jobUuid: 'job-1', created: true, taskStatus: 'running', jobStatus: 'running' })
    await waitFor(() => expect(sessionStorage.getItem('unilab:device-action:uncertain-intent')).toBeNull())
  })

  it('运行时明确没有动作时关闭模板兜底', async () => {
    vi.mocked(edgeClient.loadDeviceRuntimeCatalog).mockResolvedValue([{ ...runtime, actions: [] }])
    renderPage()
    expect(await screen.findByText('没有可控设备')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /夹持-保持当前压力hold/ })).not.toBeInTheDocument()
  })

  it('Backend 设备 UUID 不匹配时不使用 localId 旁路派发门禁', async () => {
    vi.mocked(edgeClient.loadDeviceRuntimeCatalog).mockResolvedValue([{ ...runtime, materialUuid: 'another-material' }])
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /夹持-保持当前压力hold/ }))
    await screen.findByLabelText('持续秒数')
    expect(screen.getByText('设备当前不在线，不能执行动作。')).toBeInTheDocument()
    expect(screen.getByLabelText(/我已核对设备现场和动作参数/)).toBeDisabled()
  })

  it('断开 Edge 时保留运行身份，但禁止发送停止命令', () => {
    sessionStorage.setItem('unilab:device-action:last-run', JSON.stringify({
      taskUuid: 'task-1', jobUuid: 'job-1', created: true, taskStatus: 'running', jobStatus: 'running',
    }))
    renderPage({ connected: false })
    const stop = screen.getByRole('button', { name: '停止任务' })
    expect(stop).toBeDisabled()
    fireEvent.click(stop)
    expect(edgeClient.commandWorkflowTask).not.toHaveBeenCalled()
  })

  it('运行中动作的停止请求防双击并携带稳定幂等键', async () => {
    sessionStorage.setItem('unilab:device-action:last-run', JSON.stringify({
      taskUuid: 'task-1', jobUuid: 'job-1', created: true, taskStatus: 'running', jobStatus: 'running',
    }))
    vi.mocked(edgeClient.loadDeviceActionRunStatus).mockResolvedValue({
      taskUuid: 'task-1', jobUuid: 'job-1', taskStatus: 'running', controlStatus: 'active',
      jobStatus: 'running', actionName: 'hold', param: {}, feedback: {}, result: {}, errors: [],
    })
    let finishCommand: ((value: Record<string, unknown>) => void) | undefined
    vi.mocked(edgeClient.commandWorkflowTask).mockImplementation(() => new Promise((resolve) => { finishCommand = resolve }))
    renderPage()
    const stop = await screen.findByRole('button', { name: '停止任务' })
    fireEvent.click(stop)
    fireEvent.click(stop)
    expect(edgeClient.commandWorkflowTask).toHaveBeenCalledOnce()
    expect(edgeClient.commandWorkflowTask).toHaveBeenCalledWith('task-1', 'cancel', undefined, expect.any(String))
    finishCommand?.({ status: 'succeeded' })
    expect(await screen.findByRole('button', { name: '停止请求已受理' })).toBeDisabled()
    expect(JSON.parse(String(sessionStorage.getItem('unilab:device-action:cancel-intent')))).toMatchObject({
      taskUuid: 'task-1', state: 'accepted', idempotencyKey: expect.any(String),
    })
  })

  it('停止结果未知时复用原键，明确拒绝后允许生成新键', async () => {
    sessionStorage.setItem('unilab:device-action:last-run', JSON.stringify({
      taskUuid: 'task-1', jobUuid: 'job-1', created: true, taskStatus: 'running', jobStatus: 'running',
    }))
    vi.mocked(edgeClient.loadDeviceActionRunStatus).mockResolvedValue({
      taskUuid: 'task-1', jobUuid: 'job-1', taskStatus: 'running', controlStatus: 'active',
      jobStatus: 'running', actionName: 'hold', param: {}, feedback: {}, result: {}, errors: [],
    })
    vi.mocked(edgeClient.commandWorkflowTask)
      .mockRejectedValueOnce(new TaskCommandRequestError('网络中断', true))
      .mockRejectedValueOnce(new TaskCommandRequestError('scheduler_unavailable', false))
      .mockResolvedValueOnce({ status: 'succeeded' })
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '停止任务' }))
    const retry = await screen.findByRole('button', { name: '确认原停止请求' })
    const firstKey = vi.mocked(edgeClient.commandWorkflowTask).mock.calls[0][3]
    expect(retry).toBeEnabled()
    fireEvent.click(retry)
    await waitFor(() => expect(edgeClient.commandWorkflowTask).toHaveBeenCalledTimes(2))
    expect(vi.mocked(edgeClient.commandWorkflowTask).mock.calls[1][3]).toBe(firstKey)

    fireEvent.click(await screen.findByRole('button', { name: '停止任务' }))
    await waitFor(() => expect(edgeClient.commandWorkflowTask).toHaveBeenCalledTimes(3))
    expect(vi.mocked(edgeClient.commandWorkflowTask).mock.calls[2][3]).not.toBe(firstKey)
  })
})

/**
 * 动作列表主行显示设备包写的业务说明，动作名仍以代码形式保留，便于对照与检索。
 */
it('shows the device package action description instead of repeating the action name', async () => {
  renderPage()
  const action = await screen.findByRole('button', { name: /夹持-保持当前压力hold/ })
  expect(within(action).getByText('夹持-保持当前压力')).toBeInTheDocument()
  expect(within(action).getByText('hold')).toBeInTheDocument()
})
