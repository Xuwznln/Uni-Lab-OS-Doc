import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OperationsPage } from './OperationsPage'

vi.mock('../lib/edgeClient', async () => {
  const actual = await vi.importActual<typeof import('../lib/edgeClient')>('../lib/edgeClient')
  return {
    ...actual,
    loadExperimentOperations: vi.fn(async () => []),
    loadOperationCategories: vi.fn(async () => []),
    loadActionTemplates: vi.fn(async () => []),
    loadActionParameters: vi.fn(async () => []),
    loadActionOutputs: vi.fn(async () => []),
    loadWorkflowGraph: vi.fn(async () => ({
      workflow: { uuid: 'parent-workflow', name: '父工作流', revision: 3, status: 'source', description: '', nodeCount: 0, tags: [], inputContract: [], outputContract: [], workflowType: 'experiment_operation' as const },
      nodes: [],
      edges: [],
    })),
    loadControlTemplates: vi.fn(async () => [
      {
        uuid: 'condition-template',
        name: 'condition',
        displayName: '条件',
        description: '按条件选择分支。',
        type: 'condition',
        nodeType: 'condition',
        resourceTemplate: { uuid: 'host-template', name: 'host_node', displayName: '工作流控制' },
        parameterSchema: { type: 'object', required: ['branches'] },
      },
      {
        uuid: 'repeat-template',
        name: 'repeat_until',
        displayName: '重复直到',
        description: '满足条件后退出循环。',
        type: 'repeat_until',
        nodeType: 'repeat_until',
        resourceTemplate: { uuid: 'host-template', name: 'host_node', displayName: '工作流控制' },
        parameterSchema: { type: 'object', required: ['max_iterations'] },
      },
    ]),
    loadPublishedWorkflowContracts: vi.fn(async () => []),
    createExperimentOperation: vi.fn(async () => ({ workflowUuid: 'parent-workflow', revision: 1 })),
    insertCompositeWorkflow: vi.fn(async () => ({ workflow: { revision: 2 } })),
    ensureWorkflowSequenceEdges: vi.fn(async () => undefined),
    publishWorkflow: vi.fn(async () => ({})),
  }
})

afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

function renderPage(onNotify = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <OperationsPage materials={[]} connected onNotify={onNotify} />
    </QueryClientProvider>,
  )
}

describe('实验操作发布结果', () => {
  it.each([false, true])('分别提示子流程发布与父流程更新结果：pending=%s', async (pending) => {
    const client = await import('../lib/edgeClient')
    vi.mocked(client.loadExperimentOperations).mockResolvedValueOnce([{
      uuid: 'child-publication', name: '连续发布测试', revision: 2, status: 'source',
      description: '', nodeCount: 3, tags: [], inputContract: [], outputContract: [],
      workflowType: 'experiment_operation',
    }])
    vi.mocked(client.publishWorkflow).mockResolvedValueOnce({ dependent_refresh: {
      updated_workflow_uuids: pending ? [] : ['parent-publication'],
      pending: pending ? [{ workflow_uuid: 'parent-publication', message: '新增了必填参数' }] : [],
    } })
    const notify = vi.fn()
    renderPage(notify)
    fireEvent.click(await screen.findByRole('button', { name: '发布 连续发布测试 child-publication' }))
    await waitFor(() => expect(notify).toHaveBeenCalled())
    const message = String(notify.mock.calls[0][0])
    expect(message).toContain('已发布')
    if (pending) {
      expect(message).toContain('1 个父工作流未更新')
      expect(message).toContain('parent-publication')
      expect(message).toContain('新增了必填参数')
    } else {
      expect(message).toContain('1 个父工作流已更新')
    }
  })
})

describe('OperationsPage upstream output binding', () => {
  /** 验证用户只能从目标节点之前的兼容输出中选择，并记录真实来源句柄。 */
  it('offers outputs from an earlier action and lets the user select one as the input source', async () => {
    const edgeClient = await import('../lib/edgeClient')
    vi.mocked(edgeClient.loadActionTemplates).mockResolvedValueOnce([
      {
        uuid: 'action-template-1',
        name: 'measure',
        displayName: '测量一',
        type: 'device_action',
        nodeType: 'compute',
        resourceTemplate: { uuid: 'device-template', name: 'device', displayName: '设备' },
      },
      {
        uuid: 'action-template-2',
        name: 'measure_again',
        displayName: '测量二',
        type: 'device_action',
        nodeType: 'compute',
        resourceTemplate: { uuid: 'device-template', name: 'device', displayName: '设备' },
      },
    ])
    vi.mocked(edgeClient.loadActionParameters).mockResolvedValue([{
      handleUuid: 'input-handle',
      key: 'sample_id',
      displayName: '样品',
      required: true,
      schema: { type: 'string' },
    }])
    vi.mocked(edgeClient.loadActionOutputs).mockResolvedValue([{
      handleUuid: 'output-handle',
      key: 'sample_id',
      displayName: '样品',
      schema: { type: 'string' },
    }])

    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '创建实验操作' }))
    const firstAdd = await screen.findByRole('button', { name: /测量一\s*measure/ })
    await act(async () => { fireEvent.click(firstAdd) })
    await screen.findByLabelText('设备实例 测量一')
    const secondAdd = await screen.findByRole('button', { name: /测量二\s*measure_again/ })
    await act(async () => { fireEvent.click(secondAdd) })
    let providers = await screen.findAllByLabelText('参数来源 测量二 sample_id')
    await waitFor(() => expect(screen.getAllByLabelText('参数来源 测量二 sample_id')).toHaveLength(1))
    providers = screen.getAllByLabelText('参数来源 测量二 sample_id')

    const provider = providers[0]
    expect(within(provider).getByRole('option', { name: '上游节点输出' })).toBeEnabled()
    fireEvent.change(provider, { target: { value: 'upstream' } })

    const upstreamSelect = screen.getByLabelText<HTMLSelectElement>('上游输出 测量二 sample_id')
    const upstreamOption = screen.getByRole<HTMLOptionElement>('option', { name: '测量一 · 样品' })
    fireEvent.change(upstreamSelect, { target: { value: upstreamOption.value } })

    expect(upstreamSelect.value).toBe(upstreamOption.value)
    expect(upstreamSelect.value).toMatch(/\|output-handle$/)

    fireEvent.click(screen.getByRole('button', { name: '关闭节点参数' }))
    fireEvent.click(screen.getByRole('button', { name: '上移 测量二' }))
    fireEvent.click(screen.getByRole('button', { name: '配置参数 测量二' }))
    expect(screen.getByLabelText<HTMLSelectElement>('参数来源 测量二 sample_id').value).toBe('literal')
  })

  /** 验证编辑器按作者顺序还原乱序返回的节点，避免把合法上游误判成下游。 */
  it('restores upstream choices by sequence index when an existing graph returns nodes out of order', async () => {
    const edgeClient = await import('../lib/edgeClient')
    vi.mocked(edgeClient.loadExperimentOperations).mockResolvedValueOnce([{
      uuid: 'workflow-1', name: '乱序工作流', revision: 1, status: 'source', description: '', nodeCount: 2,
      tags: [], inputContract: [], outputContract: [], workflowType: 'experiment_operation',
    }])
    vi.mocked(edgeClient.loadWorkflowGraph).mockResolvedValueOnce({
      workflow: {
        uuid: 'workflow-1', name: '乱序工作流', revision: 1, status: 'source', description: '', nodeCount: 2,
        tags: [], inputContract: [], outputContract: [], workflowType: 'experiment_operation',
      },
      nodes: [
        { uuid: 'target-node', name: '目标动作', type: 'device_action', kind: 'action', workflow_node_template_uuid: 'target-template', material_uuid: 'target-device', meta_data: { unilab: { sequence_index: 1 } }, disabled: false },
        { uuid: 'source-node', name: '来源动作', type: 'device_action', kind: 'action', workflow_node_template_uuid: 'source-template', material_uuid: 'source-device', meta_data: { unilab: { sequence_index: 0 } }, disabled: false },
      ],
      edges: [{ uuid: 'data-edge', sourceNodeUuid: 'source-node', targetNodeUuid: 'target-node', sourceHandleUuid: 'source-output', targetHandleUuid: 'target-input', metaData: { unilab: { edge_kind: 'data' } } }],
    })
    vi.mocked(edgeClient.loadActionParameters).mockImplementation(async (templateUuid) => templateUuid === 'target-template' ? [{
      handleUuid: 'target-input', key: 'sample_id', displayName: '样品', required: true, schema: { type: ['number', 'null'] },
    }] : [])
    vi.mocked(edgeClient.loadActionOutputs).mockImplementation(async (templateUuid) => templateUuid === 'source-template' ? [{
      handleUuid: 'source-output', key: 'sample_id', displayName: '样品', schema: { type: ['integer', 'null'] },
    }] : [])

    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '编辑 乱序工作流 workflow-1' }))
    fireEvent.click(await screen.findByRole('button', { name: '配置参数 目标动作' }))

    const provider = await screen.findByLabelText('参数来源 目标动作 sample_id')
    fireEvent.change(provider, { target: { value: 'upstream' } })
    expect(screen.getByRole('option', { name: '来源动作 · 样品' })).toBeInTheDocument()
  })
})

describe('OperationsPage control-node authoring', () => {
  it('lets a user add and fill condition/repeat nodes without editing one large JSON blob', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '创建实验操作' }))

    const conditionAction = await screen.findByRole('button', { name: '加入画布 条件' })
    fireEvent.click(conditionAction)
    expect(within(screen.getByLabelText('实验操作节点画布')).getByText('条件')).toBeInTheDocument()
    expect(screen.getByText('条件分支')).toBeInTheDocument()
    expect(screen.queryByLabelText('条件 参数 JSON')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '加入画布 重复直到' }))
    expect(within(screen.getByLabelText('实验操作节点画布')).getByText('重复直到')).toBeInTheDocument()
    expect(screen.getByText('循环体执行哪些节点')).toBeInTheDocument()
    expect(screen.getByLabelText('重复直到 第一轮初值')).toBeInTheDocument()
    expect(screen.getByLabelText('重复直到 下一轮值')).toBeInTheDocument()
    expect(screen.queryByLabelText('重复直到 循环变量绑定')).not.toBeInTheDocument()
  })
})

describe('OperationsPage composite workflow authoring', () => {
  /** 验证同一已发布实验操作可作为两个独立调用加入父工作流。 */
  it('lets a user add the same published experiment operation twice', async () => {
    const edgeClient = await import('../lib/edgeClient')
    vi.mocked(edgeClient.loadPublishedWorkflowContracts).mockResolvedValue([
      {
        uuid: 'contract-1',
        workflow_uuid: 'child-workflow-1',
        workflow_revision: 3,
        name: '子操作 A',
        executor_requirements: [],
      },
    ])

    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '创建实验操作' }))
    fireEvent.click(screen.getByRole('button', { name: '引用已发布子工作流' }))
    fireEvent.click(await screen.findByRole('button', { name: /子操作 A.*添加/ }))
    fireEvent.click(screen.getByRole('button', { name: '引用已发布子工作流' }))
    fireEvent.click(await screen.findByRole('button', { name: /子操作 A.*添加/ }))

    const canvas = screen.getByLabelText('实验操作节点画布')
    expect(within(canvas).getAllByText('子操作 A')).toHaveLength(2)
  })

  /** 验证重复调用分别填写的固定参数会传给各自的组合调用接口。 */
  it('keeps fixed input values independent for two invocations of the same operation', async () => {
    const edgeClient = await import('../lib/edgeClient')
    vi.mocked(edgeClient.loadPublishedWorkflowContracts).mockResolvedValue([
      {
        uuid: 'contract-1',
        workflow_uuid: 'child-workflow-1',
        workflow_revision: 3,
        name: '子操作 A',
        executor_requirements: [],
        input_contract: {
          version: 1,
          parameters: [{ name: 'value', title: '数值', schema: { type: 'number' }, required: true }],
        },
      },
    ])

    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '创建实验操作' }))
    fireEvent.change(screen.getByLabelText('操作名称'), { target: { value: '重复调用父工作流' } })
    for (let index = 0; index < 2; index += 1) {
      fireEvent.click(screen.getByRole('button', { name: '引用已发布子工作流' }))
      fireEvent.click(await screen.findByRole('button', { name: /子操作 A.*添加/ }))
    }

    fireEvent.change(screen.getByLabelText('子工作流参数 子操作 A 调用 1 value'), { target: { value: '1' } })
    fireEvent.change(screen.getByLabelText('子工作流参数 子操作 A 调用 2 value'), { target: { value: '2' } })
    fireEvent.click(screen.getByRole('button', { name: '保存实验操作' }))

    await waitFor(() => expect(edgeClient.insertCompositeWorkflow).toHaveBeenCalledTimes(2))
    expect(vi.mocked(edgeClient.insertCompositeWorkflow).mock.calls.map(([payload]) => payload.param)).toEqual([
      { value: 1 },
      { value: 2 },
    ])
  })
})

describe('实验操作的复合边界', () => {
  it('库存服务显示为 OS 本地服务，不提示绑定设备', async () => {
    const client = await import('../lib/edgeClient')
    const workflow = { uuid: 'stocktake', name: '库存盘点', revision: 1, status: 'published', description: '', nodeCount: 1, tags: [], inputContract: [], outputContract: [], workflowType: 'experiment_operation' as const }
    vi.mocked(client.loadExperimentOperations).mockResolvedValueOnce([workflow])
    vi.mocked(client.loadWorkflowGraph).mockResolvedValueOnce({ workflow, edges: [], nodes: [
      { uuid: 'check', name: '实测校正', type: 'workflow_command', kind: 'action', disabled: false },
    ] })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '查看 库存盘点 stocktake' }))
    expect(await screen.findByText('OS 本地服务，无需设备实例')).toBeInTheDocument()
    expect(screen.queryByText('运行时按设备类型选择')).not.toBeInTheDocument()
  })

  it('详情和编辑只展示本层步骤，不把子操作内部动作复制到父操作', async () => {
    const client = await import('../lib/edgeClient')
    const workflow = {
      uuid: 'boundary-parent', name: '复合转运', revision: 2, status: 'published',
      description: '', nodeCount: 5, tags: [], inputContract: [], outputContract: [],
      workflowType: 'experiment_operation' as const,
    }
    vi.mocked(client.loadExperimentOperations).mockResolvedValueOnce([workflow])
    vi.mocked(client.loadWorkflowGraph).mockResolvedValue({
      workflow, edges: [],
      nodes: [
        { uuid: 'nested-action', name: '子操作内部动作', type: 'ILab', kind: 'action', disabled: false, parentUuid: 'nested-condition', workflow_node_template_uuid: 'inner-template', material_uuid: 'robot-device' },
        { uuid: 'sibling', name: '定位气缸', type: 'ILab', kind: 'action', disabled: false, authoringOrder: 1, workflow_node_template_uuid: 'outer-template', material_uuid: 'staging-device' },
        { uuid: 'nested-condition', name: '子操作内部条件', type: 'condition', kind: 'condition', disabled: false, parentUuid: 'invocation', workflow_node_template_uuid: 'condition-template' },
        { uuid: 'invocation', name: '取收集器组', type: 'workflow', kind: 'action', disabled: false, authoringOrder: 0, meta_data: { unilab: { composite: { contract_uuid: 'child-contract', child_workflow_uuid: 'child-operation', child_workflow_revision: 2, executor_requirements: [], device_bindings: { executor_1: 'robot-device' } } } } },
        { uuid: 'local-condition', name: '本层条件', type: 'condition', kind: 'condition', disabled: false, authoringOrder: 2, workflow_node_template_uuid: 'condition-template', param: { branches: [] } },
      ],
    })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '查看 复合转运 boundary-parent' }))
    await screen.findByText('定位气缸')
    const detail = screen.getByRole('heading', { name: '复合转运', level: 3 }).closest('aside')!
    expect(within(detail).queryByText('子操作内部动作')).not.toBeInTheDocument()
    expect(within(detail).queryByText('子操作内部条件')).not.toBeInTheDocument()
    expect(within(detail).queryByText('未绑定设备实例')).not.toBeInTheDocument()
    expect(within(detail).getByText('3')).toBeInTheDocument()
    fireEvent.click(within(detail).getByRole('button', { name: /^编辑$/ }))
    await screen.findByRole('button', { name: '配置参数 定位气缸' })
    expect(screen.queryByRole('button', { name: '配置参数 子操作内部动作' })).not.toBeInTheDocument()
    expect(vi.mocked(client.loadActionParameters)).not.toHaveBeenCalledWith('inner-template')
  })
})

describe('操作目录分类查询', () => {
  it('默认保留顶层入口，子操作可按文件夹和 UUID 查询并打开详情', async () => {
    const client = await import('../lib/edgeClient')
    const base = { revision: 2, status: 'published', description: '', nodeCount: 0, inputContract: [], outputContract: [], workflowType: 'experiment_operation' as const }
    const child = { ...base, uuid: 'collect-child', name: '收集上料', tags: ['operation:internal', 'folder:04_collect'] }
    vi.mocked(client.loadExperimentOperations).mockResolvedValueOnce([
      { ...base, uuid: 'top-1', name: '粉末收集', tags: ['operation:top-level', 'folder:11_parallel'] },
      child,
      { ...base, uuid: 'robot-child', name: '机械臂换刀', tags: ['operation:internal', 'folder:06_robot'] },
    ])
    renderPage()
    expect(await screen.findByRole('button', { name: '查看 粉末收集 top-1' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '查看 收集上料 collect-child' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '子操作' }))
    expect(screen.getByRole('button', { name: '查看 收集上料 collect-child' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '查看 粉末收集 top-1' })).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('操作文件夹'), { target: { value: '04_collect' } })
    expect(screen.queryByRole('button', { name: '查看 机械臂换刀 robot-child' })).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('搜索操作'), { target: { value: 'COLLECT-CHILD' } })
    vi.mocked(client.loadWorkflowGraph).mockResolvedValueOnce({ workflow: child, nodes: [], edges: [] })
    fireEvent.click(screen.getByRole('button', { name: '查看 收集上料 collect-child' }))
    await waitFor(() => expect(client.loadWorkflowGraph).toHaveBeenCalledWith('collect-child'))
    fireEvent.change(screen.getByLabelText('搜索操作'), { target: { value: '不存在' } })
    expect(screen.getByText('没有匹配的操作')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('搜索操作'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: '全部操作' }))
    expect(screen.getByLabelText('操作文件夹')).toHaveValue('')
    expect(screen.getByText('显示 3 / 3 个操作')).toBeInTheDocument()
  })
})
