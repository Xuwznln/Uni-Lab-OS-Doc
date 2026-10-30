import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { adaptTask } from '../lib/edgeClient'
import { TasksPage } from './TasksPage'

const taskRecord = {
  uuid: 'historical-task', status: 'canceled', control_status: 'waiting_reconciliation', cleanup_status: 'none',
  execution_plan: { nodes: [{ uuid: 'stir-node', kind: 'device_action', action_name: 'run_stirring', topological_index: 0 }] },
}
function initialJob() {
  return {
    uuid: 'historical-job', workflow_node_uuid: 'stir-node', workflow_task_uuid: 'historical-task', status: 'canceled', execution_source: 'error_manual',
    start_state: 'NOT_STARTED', edge_command_uuid: 'historical-command', uncertainty_reason: 'local_cancel_acceptance_unavailable',
    param: { seconds: 60 }, return_info: {}, error_info: [], feedback_data: {}, review_version: 'review-v1', result_reviewed: false,
    manual_review: null as null | { review_version: string; reason: string; operator_id: string; reviewed_at: string },
    control_data: {}, meta_data: { session_id: 'closed-session', source_task_uuid: 'source-task', source_job_uuid: 'source-job', source_node_id: 'source-node' },
  }
}
let job = initialJob()
let otherJobs: Array<Record<string, unknown>> = []
let posts: Array<{ key: string; body: Record<string, unknown> }> = []
let postHandler: (() => Response | Promise<Response>) | undefined
let getHandler: (() => Response | Promise<Response>) | undefined
const notify = vi.fn()
const envelope = (data: unknown) => new Response(JSON.stringify({ code: 0, data }), { status: 200 })
const context = () => ({ station_id: 'local', session_id: 'closed-session', session_stage: 'RESOLVED', job, review_version: job.review_version, result_reviewed: job.result_reviewed, manual_review: job.manual_review })

beforeEach(() => {
  job = initialJob(); otherJobs = []; posts = []; postHandler = undefined; getHandler = undefined; sessionStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async (input: string, options?: RequestInit) => {
    if (input.endsWith('/manual-action-jobs/historical-job/review')) {
      if (options?.method !== 'POST') return getHandler ? getHandler() : envelope(context())
      const body = JSON.parse(String(options.body))
      posts.push({ key: new Headers(options.headers).get('Idempotency-Key') || '', body })
      if (postHandler) return postHandler()
      job.result_reviewed = true
      job.manual_review = { review_version: job.review_version, reason: body.reason, operator_id: 'operator-1', reviewed_at: '2026-09-11T05:00:00Z' }
      return envelope({ command_uuid: 'review-command', status: 'APPLIED', result: { job_uuid: job.uuid, ...job.manual_review }, station: { mode: 'RUNNING', active_session: null }, review_context: context() })
    }
    if (input.endsWith('/workflow-tasks/historical-task/jobs')) return envelope([job, ...otherJobs])
    if (input.endsWith('/workflow-tasks/historical-task')) return envelope(taskRecord)
    if (input.includes('/workflow-interventions?')) return envelope([])
    if (input.endsWith('/execution-locks')) return envelope({ locks: [], active_device_tenancy_count: 0 })
    throw new Error(`未匹配的测试请求 ${input}`)
  }))
})
afterEach(() => { vi.clearAllMocks(); vi.unstubAllGlobals() })

function setup(connected = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  const page = (isConnected: boolean) => <QueryClientProvider client={client}><TasksPage tasks={[adaptTask(taskRecord, [job, ...otherJobs])]} workflows={[]} materials={[]} connected={isConnected} onRefresh={vi.fn()} onNotify={notify} onOpenWorkflow={vi.fn()} /></QueryClientProvider>
  const view = render(page(connected))
  return { ...view, client, page }
}
async function openNodeReview() {
  fireEvent.click(screen.getByRole('button', { name: /run_stirring，结果待核对/ }))
  const inspector = await screen.findByRole('region', { name: '节点运行详情' })
  const review = await within(inspector).findByRole('button', { name: '核对结果' })
  await waitFor(() => expect(review).toBeEnabled())
  fireEvent.click(review)
  return inspector
}
const confirmation = '我已现场核对，确认动作已停止，并已检查执行结果及物料状态'

describe('任务页历史手动动作核对', () => {
  it('已关闭会话可从矩阵节点完成核对，保留取消结果并显示审计', async () => {
    setup()
    const inspector = await openNodeReview()
    const save = within(inspector).getByRole('button', { name: '保存核对记录' })
    expect(save).toBeDisabled()
    expect(within(inspector).getByText('执行结果：已取消')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '批准' })).not.toBeInTheDocument()
    fireEvent.change(within(inspector).getByLabelText('核对说明'), { target: { value: '现场搅拌已停止，烧杯位置已核实' } })
    expect(save).toBeDisabled()
    fireEvent.click(within(inspector).getByLabelText(confirmation))
    fireEvent.click(save); fireEvent.click(save)
    await within(inspector).findByText('结果已核对')
    expect(within(inspector).getByText('执行结果：已取消')).toBeInTheDocument()
    expect(within(inspector).getByText('现场搅拌已停止，烧杯位置已核实')).toBeInTheDocument()
    expect(within(inspector).getByText(/2026-09-11T05:00:00Z/)).toBeInTheDocument()
    expect(posts).toHaveLength(1)
    expect(posts[0].key).not.toBe('')
    expect(posts[0].body).toEqual({ expected_review_version: 'review-v1', reason: '现场搅拌已停止，烧杯位置已核实', operator_confirmed: true })
    expect(job.status).toBe('canceled')
    expect(job.uncertainty_reason).toBe('local_cancel_acceptance_unavailable')
    expect(taskRecord.control_status).toBe('waiting_reconciliation')
  })

  it('旧证据版本被拒绝后重新读取并清除人工确认', async () => {
    postHandler = () => { job.review_version = 'review-v2'; return new Response(JSON.stringify({ detail: 'manual_review_version_conflict' }), { status: 409 }) }
    setup()
    const inspector = await openNodeReview()
    fireEvent.change(within(inspector).getByLabelText('核对说明'), { target: { value: '第一次现场确认' } })
    fireEvent.click(within(inspector).getByLabelText(confirmation))
    fireEvent.click(within(inspector).getByRole('button', { name: '保存核对记录' }))
    await within(inspector).findByText(/动作结果已变化/)
    await waitFor(() => expect(within(inspector).getByLabelText(confirmation)).not.toBeChecked())
    expect(posts).toHaveLength(1)
    postHandler = undefined
    fireEvent.change(within(inspector).getByLabelText('核对说明'), { target: { value: '已按新结果重新检查' } })
    fireEvent.click(within(inspector).getByLabelText(confirmation))
    fireEvent.click(within(inspector).getByRole('button', { name: '保存核对记录' }))
    await within(inspector).findByText('结果已核对')
    expect(posts[1].body.expected_review_version).toBe('review-v2')
    expect(posts[1].key).not.toBe(posts[0].key)
  })

  it('提交结果未知时重开页面保留原请求身份，人工确认复选框不自动恢复', async () => {
    postHandler = () => { throw new TypeError('network lost') }
    const first = setup()
    let inspector = await openNodeReview()
    fireEvent.change(within(inspector).getByLabelText('核对说明'), { target: { value: '已现场检查' } })
    fireEvent.click(within(inspector).getByLabelText(confirmation))
    fireEvent.click(within(inspector).getByRole('button', { name: '保存核对记录' }))
    await within(inspector).findByRole('button', { name: '确认原核对请求结果' })
    const original = structuredClone(posts[0])
    first.unmount(); postHandler = undefined; setup()
    inspector = await openNodeReview()
    expect(within(inspector).queryByLabelText(confirmation)).not.toBeChecked()
    fireEvent.click(await within(inspector).findByRole('button', { name: '确认原核对请求结果' }))
    await within(inspector).findByText('结果已核对')
    expect(posts).toEqual([original, original])
  })

  it('断线后保留记录但不可提交核对', async () => {
    const view = setup()
    const inspector = await openNodeReview()
    fireEvent.change(within(inspector).getByLabelText('核对说明'), { target: { value: '已检查' } })
    fireEvent.click(within(inspector).getByLabelText(confirmation))
    view.rerender(view.page(false))
    expect(within(inspector).getByRole('button', { name: '保存核对记录' })).toBeDisabled()
    expect(within(inspector).getByLabelText(confirmation)).not.toBeChecked()
    expect(posts).toHaveLength(0)
  })

  it('新结果版本使旧核对失效，重新勾选前不可提交', async () => {
    const { client } = setup()
    const inspector = await openNodeReview()
    fireEvent.change(within(inspector).getByLabelText('核对说明'), { target: { value: '已检查' } })
    fireEvent.click(within(inspector).getByLabelText(confirmation))
    job.review_version = 'review-v2'
    await act(async () => { await client.invalidateQueries({ queryKey: ['historical-manual-review'] }) })
    await waitFor(() => expect(within(inspector).getByLabelText(confirmation)).not.toBeChecked())
    expect(posts).toHaveLength(0)
  })

  it('旧核对请求幂等返回已应用后，仍按新证据显示需要重新核对', async () => {
    postHandler = () => { throw new TypeError('network lost') }
    setup()
    const inspector = await openNodeReview()
    fireEvent.change(within(inspector).getByLabelText('核对说明'), { target: { value: '核对旧执行结果' } })
    fireEvent.click(within(inspector).getByLabelText(confirmation))
    fireEvent.click(within(inspector).getByRole('button', { name: '保存核对记录' }))
    await within(inspector).findByRole('button', { name: '确认原核对请求结果' })
    job.review_version = 'review-v2'; job.result_reviewed = false
    job.manual_review = { review_version: 'review-v1', reason: '核对旧执行结果', reviewed_at: '2026-09-11T05:00:00Z', operator_id: 'user-1' }
    postHandler = () => envelope({ command_uuid: 'original-command', status: 'APPLIED', result: job.manual_review, review_context: { ...context(), review_version: 'review-v1', result_reviewed: true } })
    fireEvent.click(within(inspector).getByRole('button', { name: '确认原核对请求结果' }))
    await within(inspector).findByText('上次核对记录（当前结果需重新核对）')
    expect(within(inspector).queryByText('结果已核对')).not.toBeInTheDocument()
    expect(within(inspector).getByLabelText(confirmation)).not.toBeChecked()
    expect(posts[1]).toEqual(posts[0])
  })

  it('当前节点已有普通重试时，历史手动动作仍有独立入口和正确的执行证据', async () => {
    otherJobs = [{ uuid: 'latest-job', workflow_node_uuid: 'stir-node', workflow_task_uuid: 'historical-task', execution_source: 'workflow', status: 'succeeded', attempt: 2, param: { seconds: 900 } }]
    setup()
    fireEvent.click(screen.getByRole('button', { name: /run_stirring，已完成/ }))
    const inspector = await screen.findByRole('region', { name: '节点运行详情' })
    const historical = within(inspector).getByRole('region', { name: '手动动作结果核对 historical-job' })
    await waitFor(() => expect(within(historical).getByRole('button', { name: '核对结果' })).toBeEnabled())
    fireEvent.click(within(historical).getByText('查看本次动作执行记录'))
    expect(within(historical).getByText(/"seconds": 60/)).toBeInTheDocument()
    expect(within(historical).queryByText(/"seconds": 900/)).not.toBeInTheDocument()
    fireEvent.click(within(historical).getByRole('button', { name: '核对结果' }))
    expect(within(historical).getByLabelText(confirmation)).not.toBeChecked()
    expect(posts).toHaveLength(0)
  })

  it('动作仍在运行时不提供核对提交，也不查询终态核对接口', async () => {
    job.status = 'running'; setup()
    fireEvent.click(screen.getByRole('button', { name: /run_stirring，正在运行/ }))
    const inspector = await screen.findByRole('region', { name: '节点运行详情' })
    expect(within(inspector).getByText('动作尚未结束')).toBeInTheDocument()
    expect(within(inspector).queryByRole('button', { name: '核对结果' })).not.toBeInTheDocument()
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('/manual-action-jobs/'))).toBe(false)
    expect(posts).toHaveLength(0)
  })

  it('任务已更新但核对上下文仍旧时不宣布已核对，并在双方同步前禁用提交', async () => {
    job.result_reviewed = true
    job.manual_review = { review_version: 'review-v1', reason: '旧记录已核对', reviewed_at: '2026-09-11T05:00:00Z', operator_id: 'user-1' }
    const oldContext = structuredClone(context())
    const { client } = setup()
    fireEvent.click(screen.getByRole('button', { name: /run_stirring，已取消/ }))
    const inspector = await screen.findByRole('region', { name: '节点运行详情' })
    await within(inspector).findByText('结果已核对')
    job.review_version = 'review-v2'; job.result_reviewed = false
    getHandler = () => envelope(oldContext)
    await act(async () => { client.setQueryData(['workflow-task-detail', 'historical-task'], adaptTask(taskRecord, [job])) })
    await within(inspector).findByText('核对状态更新中')
    expect(within(inspector).queryByText('结果已核对')).not.toBeInTheDocument()
    expect(within(inspector).getByRole('button', { name: '核对结果' })).toBeDisabled()
    getHandler = undefined
    await waitFor(() => expect(within(inspector).getByRole('button', { name: '刷新核对状态' })).toBeEnabled())
    fireEvent.click(within(inspector).getByRole('button', { name: '刷新核对状态' }))
    await waitFor(() => expect(within(inspector).getByRole('button', { name: '核对结果' })).toBeEnabled())
    fireEvent.click(within(inspector).getByRole('button', { name: '核对结果' }))
    expect(within(inspector).getByLabelText(confirmation)).not.toBeChecked()
    expect(posts).toHaveLength(0)
  })

  it('核对状态读取失败时清除确认并停止提交，不能用旧缓存核对', async () => {
    const { client } = setup()
    const inspector = await openNodeReview()
    fireEvent.change(within(inspector).getByLabelText('核对说明'), { target: { value: '已检查' } })
    fireEvent.click(within(inspector).getByLabelText(confirmation))
    getHandler = () => new Response(JSON.stringify({ detail: 'service_unavailable' }), { status: 503 })
    await act(async () => { await client.invalidateQueries({ queryKey: ['historical-manual-review'] }) })
    await within(inspector).findByText(/核对状态读取失败/)
    expect(within(inspector).getByLabelText(confirmation)).not.toBeChecked()
    expect(within(inspector).getByRole('button', { name: '保存核对记录' })).toBeDisabled()
    expect(posts).toHaveLength(0)
  })
})
