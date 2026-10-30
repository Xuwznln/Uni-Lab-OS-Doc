import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { demoTasks } from '../data/demo'
import styles from '../styles.css?inline'
import { TasksPage } from './TasksPage'

const taskUuid = 'ea877cd4-af63-4193-b7da-82f0a14bd851'
const jobUuid = 'bb404dd6-acee-452b-a7ec-63dc7398f6f3'
const blockReason = '连续区间锁必须通过任务级人工解锁整组释放'
const reason = '已现场确认机械臂和设备停止，无在途动作，物料已盘点'
const devices = ['2d5e79ab-bb6f-5e10-82fe-8b421b56b3e5', '140b9210-e7b1-53a9-89d8-7445993c6512', '9e4fc6ec-7552-5390-a3cf-56d673b15edf']
let taskStatus: string
let cleanupStatus: string | undefined
let manualReviewAttentionResolved: boolean
let released: boolean
let activeTenancies: number
let locksReadFailure: boolean
let writes: { url: string; body: Record<string, any> }[]
let handlePost: () => Promise<Response>
let reads: string[]
let style: HTMLStyleElement
const response = (data: unknown, status = 200) => new Response(JSON.stringify({ code: 0, data }), { status })
const commandResponse = (status: string, result: Record<string, unknown> = {}) => response({ uuid: 'unlock-command', workflow_task_uuid: taskUuid, type: 'unlock_resources', idempotency_key: writes.at(-1)?.body.idempotency_key, status, result }, 201)

beforeEach(() => {
  sessionStorage.clear()
  taskStatus = 'canceled'; cleanupStatus = undefined; manualReviewAttentionResolved = false; released = false; activeTenancies = 0; locksReadFailure = false; writes = []; reads = []
  style = document.createElement('style'); style.textContent = styles; document.head.appendChild(style)
  handlePost = async () => {
    released = true
    return commandResponse('succeeded', { cleanup_status: 'settled', released: { execution_locks: 3, execution_claims: 1, device_tenancies: 0, task_material_claims: 1 } })
  }
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const url = String(input)
    if (options?.method === 'POST') { writes.push({ url, body: JSON.parse(String(options.body)) }); return handlePost() }
    reads.push(url)
    if (url.includes('/execution-locks') && locksReadFailure) throw new TypeError('Failed to fetch locks')
    return response(url.includes('/execution-locks') ? {
      workflow_task_uuid: url.match(/workflow-tasks\/([^/]+)/)?.[1], task_status: taskStatus, active_device_tenancy_count: activeTenancies,
      locks: released ? [] : devices.map((device) => ({
        uuid: `lease-${device}`, workflow_task_uuid: taskUuid, workflow_node_job_uuid: jobUuid,
        lock_key: `/devices/${device}`, scope: 'device', state: 'running', released_at: null,
        claim_uuid: 'a65c0d24-2db7-4fef-a85c-81444c5ffde8', fencing_token: 8,
        job_status: 'failed', claim_state: 'running', can_release: false, release_block_reason: blockReason,
      })),
    } : [])
  }))
})
afterEach(() => { style.remove(); vi.unstubAllGlobals() })

function mountTask(selectedUuid = taskUuid) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const notify = vi.fn()
  const view = (connected: boolean, uuid: string) => <QueryClientProvider client={client}><TasksPage tasks={[{ ...demoTasks[0], uuid, status: 'intervention_required', executionStatus: 'canceled', cleanupStatus, manualReviewAttentionResolved }]} workflows={[]} materials={[]} connected={connected} onRefresh={vi.fn()} onNotify={notify} onOpenWorkflow={vi.fn()} /></QueryClientProvider>
  const rendered = render(view(true, selectedUuid))
  return { ...rendered, client, notify, update: (connected: boolean, uuid = selectedUuid) => rendered.rerender(view(connected, uuid)) }
}

async function openTaskUnlock() {
  const button = await screen.findByRole('button', { name: '解除任务全部锁' })
  await waitFor(() => expect(button).toBeEnabled())
  fireEvent.click(button)
  return screen.getByRole('dialog', { name: '解除任务全部锁' })
}

function confirmTaskUnlock(dialog: HTMLElement) {
  fireEvent.change(within(dialog).getByRole('textbox', { name: /任务解锁说明/ }), { target: { value: reason } })
  fireEvent.click(within(dialog).getByRole('checkbox', { name: /现场确认/ }))
  fireEvent.click(within(dialog).getByRole('button', { name: /^确认(解除任务全部锁|清理交接残留)$/ }))
}

describe('任务级人工解锁', () => {
  it('从真实连续区间锁进入整任务确认：展示全Task范围，说明和现场确认缺一不可', async () => {
    mountTask()
    const dialog = await openTaskUnlock()
    expect(screen.getByRole('button', { name: '解除这组锁' })).toBeDisabled()
    expect(dialog).toHaveTextContent(taskUuid)
    expect(dialog).toHaveTextContent('等待任务可能继续运行')
    const submit = within(dialog).getByRole('button', { name: '确认解除任务全部锁' })
    expect(within(dialog).getByRole('checkbox')).not.toBeChecked()
    expect(submit).toBeDisabled()
    fireEvent.click(within(dialog).getByRole('checkbox'))
    expect(submit).toBeDisabled()
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: '   ' } })
    expect(submit).toBeDisabled()
    expect(writes).toEqual([])
  })

  it('发送任务级命令，仅succeeded显示成功，刷新锁、任务详情、列表和资源后清除3把锁', async () => {
    const { client, notify } = mountTask()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    confirmTaskUnlock(await openTaskUnlock())
    await screen.findByText(/任务资源解锁已完成/)
    await screen.findByText('当前任务没有活动执行锁。')
    expect(writes).toHaveLength(1)
    expect(writes[0].url).toBe(`/api/v1/workflow-tasks/${taskUuid}/commands`)
    expect(writes[0].body).toMatchObject({ type: 'unlock_resources', description: reason, meta_data: { confirmed_physical_safe: true, source: 'unilabos-frontend' } })
    expect(writes[0].body.idempotency_key).toBeTruthy()
    expect(notify).toHaveBeenCalledWith(expect.stringMatching(/已完成/))
    for (const queryKey of [['workflow-task-execution-locks', taskUuid], ['workflow-task-detail', taskUuid], ['edge-tasks'], ['edge-snapshot']]) {
      expect(invalidate).toHaveBeenCalledWith(expect.objectContaining({ queryKey }), expect.anything())
    }
    expect(screen.getByRole('dialog', { name: '解除任务全部锁' })).toBeInTheDocument()
  })

  it('HTTP201/code0但command rejected要显示原因，保持锁且清除确认', async () => {
    handlePost = async () => commandResponse('rejected', { reason: '任务仍有已派发或运行中的作业' })
    const { notify } = mountTask()
    confirmTaskUnlock(await openTaskUnlock())
    await screen.findByText(/任务解锁被拒绝：任务仍有已派发或运行中的作业/)
    expect(screen.queryByText(/任务资源解锁已完成/)).not.toBeInTheDocument()
    expect(screen.getAllByText(blockReason)).toHaveLength(3)
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    expect(notify).toHaveBeenCalledWith(expect.stringMatching(/拒绝/))
  })

  it('HTTP200业务错误不能显示成功，必须显示后端消息', async () => {
    handlePost = async () => new Response(JSON.stringify({ code: 1000, error: { msg: '任务资源状态检查失败' } }), { status: 200 })
    mountTask(); confirmTaskUnlock(await openTaskUnlock())
    await screen.findByText(/任务资源状态检查失败/)
    expect(screen.queryByText(/任务资源解锁已完成/)).not.toBeInTheDocument()
    expect(screen.getAllByText(blockReason)).toHaveLength(3)
  })

  it('pending命令保留原请求，只有显式确认才重发同key和body，最终刷新清锁', async () => {
    const succeeded = handlePost
    handlePost = async () => commandResponse('pending')
    mountTask(); confirmTaskUnlock(await openTaskUnlock())
    const check = await screen.findByRole('button', { name: '确认原解锁请求结果' })
    expect(screen.queryByText(/任务资源解锁已完成/)).not.toBeInTheDocument()
    expect(writes).toHaveLength(1)
    handlePost = succeeded
    fireEvent.click(check)
    await screen.findByText('当前任务没有活动执行锁。')
    expect(writes).toHaveLength(2)
    expect(writes[1]).toEqual(writes[0])
  })

  it('网络不确定保留请求，关闭重开与刷新不自动重发或恢复人工勾选', async () => {
    handlePost = async () => { throw new TypeError('Failed to fetch') }
    const first = mountTask(); confirmTaskUnlock(await openTaskUnlock())
    await screen.findByRole('button', { name: '确认原解锁请求结果' })
    expect(writes).toHaveLength(1)
    first.unmount()
    mountTask()
    const check = await screen.findByRole('button', { name: '确认原解锁请求结果' })
    expect(writes).toHaveLength(1)
    expect(screen.queryByRole('checkbox', { checked: true })).not.toBeInTheDocument()
    fireEvent.click(check)
    await waitFor(() => expect(writes).toHaveLength(2))
    expect(writes[1]).toEqual(writes[0])
  })

  it('响应丢失但锁轮询已清空时，仍能确认原解锁请求结果', async () => {
    handlePost = async () => { released = true; throw new TypeError('Failed to fetch') }
    const view = mountTask(); confirmTaskUnlock(await openTaskUnlock())
    await screen.findByText('当前任务没有活动执行锁。')
    view.unmount(); mountTask()
    await screen.findByText('当前任务没有活动执行锁。')
    const original = screen.getByRole('button', { name: '确认原解锁请求结果' })
    expect(writes).toHaveLength(1)
    expect(screen.getByRole('button', { name: '解除任务全部锁' })).toBeDisabled()
    handlePost = async () => commandResponse('succeeded', { released: { execution_locks: 3 } })
    fireEvent.click(original)
    await screen.findByText(/任务资源解锁已完成/)
    expect(writes[1]).toEqual(writes[0])
  })

  it('未提交时关闭重开不会恢复说明和人工确认，也不会发请求', async () => {
    mountTask(); const dialog = await openTaskUnlock()
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: reason } })
    fireEvent.click(within(dialog).getByRole('checkbox'))
    fireEvent.click(within(dialog).getByRole('button', { name: '关闭任务解锁对话框' }))
    const reopened = await openTaskUnlock()
    expect(within(reopened).getByRole('checkbox')).not.toBeChecked()
    expect(within(reopened).getByRole('textbox')).toHaveValue('')
    expect(writes).toEqual([])
  })

  it('仅剩设备托管而无执行锁时，仍可按终态Task处置全部资源', async () => {
    released = true; activeTenancies = 2
    mountTask()
    const dialog = await openTaskUnlock()
    expect(dialog).toHaveTextContent('全部执行锁及相关资源占用')
    expect(screen.getByText(/不能按单个 Job 解除/)).toBeInTheDocument()
    expect(writes).toEqual([])
  })

  it('无活动锁但清理需人工处理时，显示清理交接残留入口并提交任务级命令', async () => {
    cleanupStatus = 'requires_attention'; released = true
    mountTask()
    const button = await screen.findByRole('button', { name: '清理交接残留' })
    await waitFor(() => expect(button).toBeEnabled())
    fireEvent.click(button)
    const dialog = screen.getByRole('dialog', { name: '清理任务交接残留' })
    expect(dialog).toHaveTextContent('不会重新执行机器人动作')
    confirmTaskUnlock(dialog)
    await screen.findByText('任务交接残留已清理，清理状态已更新。')
    expect(button).toBeDisabled()
    expect(writes).toHaveLength(1)
    expect(writes[0].body).toMatchObject({ type: 'unlock_resources', meta_data: { confirmed_physical_safe: true } })
  })

  it('清理状态已由人工核对完成时不显示残留清理入口', async () => {
    cleanupStatus = 'requires_attention'; manualReviewAttentionResolved = true; released = true
    mountTask()
    expect(await screen.findByRole('button', { name: '解除任务全部锁' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: '清理交接残留' })).not.toBeInTheDocument()
  })

  it('原请求未知且刷新后锁读取失败时，仍保留明确确认入口；断线时禁用', async () => {
    handlePost = async () => { throw new TypeError('Failed to fetch') }
    const first = mountTask(); confirmTaskUnlock(await openTaskUnlock())
    await screen.findByRole('button', { name: '确认原解锁请求结果' })
    first.unmount(); locksReadFailure = true
    const second = mountTask()
    await screen.findByText(/读取执行锁失败/)
    expect(screen.getByRole('button', { name: '解除任务全部锁' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '确认原解锁请求结果' })).toBeEnabled()
    second.update(false)
    expect(screen.getByRole('button', { name: '确认原解锁请求结果' })).toBeDisabled()
    expect(writes).toHaveLength(1)
  })

  it('请求未完成时重复点击不会并发创建解锁命令', async () => {
    let finish!: (value: Response) => void
    handlePost = () => new Promise((resolve) => { finish = resolve })
    mountTask(); const dialog = await openTaskUnlock()
    const form = within(dialog).getByRole('textbox').closest('form')!
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: reason } })
    fireEvent.click(within(dialog).getByRole('checkbox'))
    fireEvent.submit(form); fireEvent.submit(form)
    await waitFor(() => expect(writes).toHaveLength(1))
    expect(screen.getByRole('button', { name: '确认原解锁请求结果' })).toBeDisabled()
    finish(commandResponse('pending'))
    await waitFor(() => expect(screen.getByRole('button', { name: '确认原解锁请求结果' })).toBeEnabled())
    expect(writes).toHaveLength(1)
  })

  it('命令响应属于其他任务时不能宣称成功，保留原请求等待确认', async () => {
    handlePost = async () => response({ uuid: 'other-command', workflow_task_uuid: 'other-task', type: 'unlock_resources', idempotency_key: writes.at(-1)?.body.idempotency_key, status: 'succeeded' }, 201)
    mountTask(); confirmTaskUnlock(await openTaskUnlock())
    await screen.findByRole('button', { name: '确认原解锁请求结果' })
    expect(screen.queryByText(/任务资源解锁已完成/)).not.toBeInTheDocument()
  })

  it('断线禁用提交并撤销勾选，重新连接也必须重新现场确认', async () => {
    const view = mountTask()
    const dialog = await openTaskUnlock()
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: reason } })
    fireEvent.click(within(dialog).getByRole('checkbox'))
    view.update(false)
    expect(within(dialog).getByRole('button', { name: '确认解除任务全部锁' })).toBeDisabled()
    expect(within(dialog).getByRole('checkbox')).not.toBeChecked()
    view.update(true)
    expect(within(dialog).getByRole('button', { name: '确认解除任务全部锁' })).toBeDisabled()
    expect(writes).toEqual([])
  })

  it.each(['running', 'pending', 'succeeded', 'unknown'])('真实状态%s不能通过呈现层需干预状态开启任务解锁', async (status) => {
    taskStatus = status
    mountTask()
    await screen.findAllByText(blockReason)
    const button = screen.getByRole('button', { name: '解除任务全部锁' })
    expect(button).toBeDisabled()
    expect(getComputedStyle(button).cursor).toBe('not-allowed')
    fireEvent.click(button)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(writes).toEqual([])
  })

  it('切换Task不串用原请求、说明或人工确认', async () => {
    handlePost = async () => { throw new TypeError('Failed to fetch') }
    const view = mountTask(); confirmTaskUnlock(await openTaskUnlock())
    await screen.findByRole('button', { name: '确认原解锁请求结果' })
    view.update(true, 'another-task')
    await waitFor(() => expect(screen.queryByRole('button', { name: '确认原解锁请求结果' })).not.toBeInTheDocument())
    const dialog = await openTaskUnlock()
    expect(within(dialog).getByRole('checkbox')).not.toBeChecked()
    expect(within(dialog).getByRole('textbox')).toHaveValue('')
    expect(writes).toHaveLength(1)
  })
})
