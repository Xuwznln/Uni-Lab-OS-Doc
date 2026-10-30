import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  Check,
  CircleDot,
  Code2,
  Copy,
  FileInput,
  FileJson,
  FlaskConical,
  GitBranch,
  LoaderCircle,
  Network,
  Play,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Send,
  Workflow as WorkflowIcon,
  X,
} from 'lucide-react'
import { createWorkflowTask, importWorkflowJson, importWorkflowPython, insertCompositeWorkflow, loadPublishedWorkflowContracts, loadWorkflowGraph, loadWorkflowPreflight, loadWorkflowSource, loadWorkflowTaskGraph, publishWorkflow } from '../lib/edgeClient'
import { DEFAULT_RUNTIME_CAPABILITIES } from '../types'
import type { ContractField, MaterialRecord, PageId, RuntimeCapabilities, WorkflowDefinition, WorkflowTarget, WorkflowTaskPriority } from '../types'
import { Button, EmptyState, PageHeader, Panel, PanelHeader } from '../components/ui'
import { CompositeWorkflowParameterEditor, compositeContractFields, compositeParameterDrafts, compositeParameterProblem, serialiseCompositeParameters } from '../components/CompositeWorkflowParameterEditor'
import { serialiseTaskInput } from './TasksPage'
import { sourceSiteOptions } from '../lib/sourceSiteOptions'
import { WorkflowDag } from '../components/WorkflowDag'

interface PendingChildInvocation {
  /** 用户刚选择、尚未写入父工作流的发布合同。 */
  contract: Record<string, any>
  /** 从发布合同生成的调用输入字段。 */
  fields: ContractField[]
  /** 本次调用独立保存的参数文本。 */
  drafts: Record<string, string>
}

function tagForWorkflow(workflow: WorkflowDefinition) {
  if (workflow.tags[0]) return workflow.tags[0]
  return workflow.status === 'source' ? '源码定义' : workflow.status
}

type ReadinessTone = 'ready' | 'warning' | 'error' | 'neutral' | 'loading'

function resourceSlotSchema(field: ContractField): Record<string, any> | undefined {
  const visit = (schema: Record<string, any>): Record<string, any> | undefined => {
    if (schema.$slot === 'ResourceSlot') return schema
    if (!Array.isArray(schema.anyOf)) return undefined
    return schema.anyOf.map((item: unknown) => item && typeof item === 'object' ? visit(item as Record<string, any>) : undefined).find(Boolean)
  }
  return visit(field.schema)
}

function initialRunInput(workflow?: WorkflowDefinition): Record<string, string> {
  return Object.fromEntries((workflow?.inputContract || []).map((field) => {
    if (field.defaultValue === undefined || field.defaultValue === null) return [field.name, '']
    if (resourceSlotSchema(field) && typeof field.defaultValue === 'object') {
      return [field.name, String((field.defaultValue as Record<string, unknown>).uuid || '')]
    }
    return [field.name, typeof field.defaultValue === 'object' ? JSON.stringify(field.defaultValue) : String(field.defaultValue)]
  }))
}

function serialisePreflightInput(fields: ContractField[], values: Record<string, string>) {
  return serialiseTaskInput(
    fields.map((field) => field.required ? { ...field, required: false } : field),
    values,
  )
}

function ReadinessIcon({ tone }: { tone: ReadinessTone }) {
  if (tone === 'loading') return <LoaderCircle className="spin" size={12} />
  if (tone === 'ready') return <Check size={12} />
  if (tone === 'neutral') return <CircleDot size={12} />
  return <AlertCircle size={12} />
}

export function WorkflowsPage({
  workflows,
  materials,
  connected,
  onNavigate,
  onNotify,
  onSelectWorkflow,
  targetWorkflow,
  capabilities,
  catalogStatus = 'ready',
}: {
  workflows: WorkflowDefinition[]
  materials: MaterialRecord[]
  connected: boolean
  onNavigate: (page: PageId) => void
  onNotify: (message: string) => void
  onSelectWorkflow?: (target: WorkflowTarget) => void
  targetWorkflow?: WorkflowTarget
  capabilities?: RuntimeCapabilities
  catalogStatus?: 'loading' | 'error' | 'ready'
}) {
  capabilities = capabilities || DEFAULT_RUNTIME_CAPABILITIES
  const [query, setQuery] = useState('')
  const availableWorkflows = useMemo(
    // 从主流程进入实验操作时展示指定子流程，避免悄悄退回列表第一项。
    () => workflows.filter((workflow) => workflow.workflowType !== 'experiment_operation'
      || workflow.uuid === targetWorkflow?.workflowUuid),
    [workflows, targetWorkflow?.workflowUuid],
  )
  const [selectedId, setSelectedId] = useState(
    availableWorkflows.some((workflow) => workflow.uuid === targetWorkflow?.workflowUuid)
      ? targetWorkflow?.workflowUuid || ''
      : availableWorkflows[0]?.uuid || '',
  )
  const [workspaceView, setWorkspaceView] = useState<'topology' | 'contract' | 'diagnostics' | 'run'>('topology')
  const [childPickerOpen, setChildPickerOpen] = useState(false)
  const [pendingChildInvocation, setPendingChildInvocation] = useState<PendingChildInvocation | null>(null)
  const [runInput, setRunInput] = useState<Record<string, string>>({})
  const [runDescription, setRunDescription] = useState('从实验运营控制台创建')
  const [runMode, setRunMode] = useState<'normal' | 'step'>('normal')
  const [runPriority, setRunPriority] = useState<WorkflowTaskPriority>('normal')
  const [sourceTarget, setSourceTarget] = useState<{
    uuid: string
    name: string
    path: string
  } | null>(null)
  const pythonImportRef = useRef<HTMLInputElement>(null)
  const jsonImportRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()
  const [knownNodeCounts, setKnownNodeCounts] = useState<Record<string, number>>({})
  const publishedChildrenQuery = useQuery({
    queryKey: ['published-child-workflows'],
    queryFn: ({ signal }) => loadPublishedWorkflowContracts(signal),
    enabled: childPickerOpen && connected,
    staleTime: 15_000,
  })

  useEffect(() => {
    if (targetWorkflow?.workflowUuid && availableWorkflows.some((workflow) => workflow.uuid === targetWorkflow.workflowUuid)) {
      setSelectedId(targetWorkflow.workflowUuid)
      return
    }
    if (!availableWorkflows.some((workflow) => workflow.uuid === selectedId)) {
      setSelectedId(availableWorkflows[0]?.uuid || '')
    }
  }, [availableWorkflows, selectedId, targetWorkflow?.workflowUuid])

  const visibleWorkflows = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return availableWorkflows
    return availableWorkflows.filter((workflow) => [workflow.name, workflow.uuid, workflow.description]
      .join(' ')
      .toLowerCase()
      .includes(needle))
  }, [query, availableWorkflows])

  const selected = visibleWorkflows.find((workflow) => workflow.uuid === selectedId) || visibleWorkflows[0]
  const selectedTaskSnapshotUuid = targetWorkflow && selected?.uuid === targetWorkflow.workflowUuid
    ? targetWorkflow.taskUuid
    : undefined
  const graphQuery = useQuery({
    queryKey: ['workflow-graph', selected?.uuid, selectedTaskSnapshotUuid],
    queryFn: ({ signal }) => selectedTaskSnapshotUuid
      ? loadWorkflowTaskGraph(selectedTaskSnapshotUuid, signal)
      : loadWorkflowGraph(selected!.uuid, signal),
    enabled: Boolean(selected),
    retry: 2,
    retryDelay: (attempt) => Math.min(300 * 2 ** attempt, 1_200),
  })
  const preflightQuery = useQuery({
    queryKey: ['workflow-run-preflight', selected?.uuid, selected?.revision, runInput, runMode],
    queryFn: ({ signal }) => loadWorkflowPreflight(
      selected!.uuid,
      serialisePreflightInput(selected!.inputContract, runInput),
      signal,
      runMode,
    ),
    enabled: false,
    retry: false,
    staleTime: 0,
  })
  const sourceQuery = useQuery({
    queryKey: ['workflow-source', sourceTarget?.uuid],
    queryFn: ({ signal }) => loadWorkflowSource(sourceTarget!.uuid, signal),
    enabled: Boolean(sourceTarget && connected),
    retry: false,
    staleTime: 0,
  })

  useEffect(() => {
    if (!sourceTarget) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSourceTarget(null)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [sourceTarget])

  useEffect(() => {
    if (!selected || !graphQuery.data) return
    const count = graphQuery.data.nodes.length
    setKnownNodeCounts((current) => current[selected.uuid] === count
      ? current
      : { ...current, [selected.uuid]: count })
  }, [graphQuery.data, selected])

  const detail = graphQuery.data?.workflow || selected
  const graphNodes = graphQuery.data?.nodes || []
  const graphEdges = graphQuery.data?.edges || []
  const materialDependencies = graphNodes.filter((node) => (
    node.type === 'material_source' || node.kind === 'material_source' || node.action_name === 'material_source'
  )).map((node) => {
    const templateUuid = String(node.param?.resource_template_uuid || '')
    const materialUuid = String(node.param?.material_uuid || '')
    const mountUuid = String(node.param?.mount?.uuid || '')
    return {
      uuid: String(node.uuid || ''),
      name: String(node.name || 'Material Source'),
      mode: String(node.param?.mode || 'existing'),
      templateUuid,
      mountUuid: String(node.param?.mount?.uuid || node.meta_data?.unilab?.resource_refs?.mount?.resource_id || ''),
      candidates: materials.filter((material) => (
        materialUuid ? material.uuid === materialUuid : Boolean(
          templateUuid && material.resourceTemplateUuid === templateUuid
          && (!mountUuid || (material.currentLocation.kind === 'site' && material.currentLocation.ownerMaterialUuid === mountUuid))
        )
      )),
    }
  })
  const preflight = preflightQuery.data
  /** 编译自 quantity_requirement 的数量需求；Edge 建任务时按来源容器自动绑定并预留，界面只读展示。 */
  const inventoryRequirements = graphQuery.data?.inventoryRequirements || []
  useEffect(() => setRunInput(initialRunInput(detail)), [detail?.uuid, detail?.revision])
  const siteOptions = sourceSiteOptions(graphQuery.data, detail?.inputContract || [], materials)
  const selectedSourceOptions = [...siteOptions].map(([name, options]) => options.find((option) => option.value === runInput[name]))
  const selectedSourceMaterials = selectedSourceOptions.map((option) => option?.materialUuid).filter(Boolean)
  const sourceSitesReady = selectedSourceOptions.every(Boolean) && new Set(selectedSourceMaterials).size === selectedSourceMaterials.length
  const requiredInputReady = sourceSitesReady && Boolean(detail) && detail.inputContract.every((field) => (
    !field.required || field.defaultValue !== undefined || Boolean(runInput[field.name]?.trim())
  ))
  const boundMaterials = detail?.inputContract.flatMap((field) => {
    const selectedMaterial = materials.find((material) => material.uuid === runInput[field.name])
    return selectedMaterial ? [{ field, material: selectedMaterial }] : []
  }) || []
  const taskMutation = useMutation({
    mutationFn: async () => {
      if (!connected) throw new Error('Edge 未连接，写操作已暂停')
      if (!detail) throw new Error('请选择工作流')
      if (!requiredInputReady) throw new Error('请选择有效且不重复的来源库位，并填写必填输入')
      return createWorkflowTask({
        workflowUuid: detail.uuid,
        description: runDescription,
        input: serialiseTaskInput(detail.inputContract, runInput),
        runMode,
        priority: runPriority,
      })
    },
    onMutate: () => onNavigate('tasks'),
    onSuccess: (task) => {
      onNotify(`任务 ${task.uuid || ''} 已提交到 Edge`)
      void queryClient.invalidateQueries({ queryKey: ['edge-tasks'] }, { cancelRefetch: false })
    },
    onError: (error) => onNotify(`任务提交失败：${error instanceof Error ? error.message : '未知错误'}`),
  })
  const publishMutation = useMutation({
    mutationFn: ({ workflowUuid, revision }: { workflowUuid: string; revision: number; name: string }) => (
      publishWorkflow(workflowUuid, revision)
    ),
    onSuccess: async (_contract, workflow) => {
      onNotify(`工作流“${workflow.name}”已发布`)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['workflow-graph', workflow.workflowUuid] }),
        queryClient.invalidateQueries({ queryKey: ['edge-snapshot'] }),
      ])
    },
    onError: (error) => onNotify(`发布失败：${error instanceof Error ? error.message : '未知错误'}`),
  })

  async function importFile(file: File, kind: 'python' | 'json') {
    if (!connected) return
    try {
      const imported = kind === 'python' ? await importWorkflowPython(file) : await importWorkflowJson(file, 'normal')
      onNotify(`已导入工作流“${imported.name}”（${imported.uuid}）`)
      await queryClient.invalidateQueries({ queryKey: ['edge-snapshot'] })
    } catch (error) {
      onNotify(`导入失败：${error instanceof Error ? error.message : '未知错误'}`)
    }
  }
  /**
   * 把用户确认的一次已发布实验操作调用写入当前父工作流。
   * 参数：``pending`` 包含发布合同和本次调用独立填写的固定参数。
   * 返回：无；成功后刷新父图与工作流目录。异常：接口错误转成页面通知，
   * 必填值缺失或格式错误时保留表单供用户修正。
   */
  async function referenceChildWorkflow(pending: PendingChildInvocation) {
    if (!connected) return
    if (!detail) return
    const contract = pending.contract
    const contractUuid = String(contract.uuid || '')
    if (!contractUuid) { onNotify('引用失败：发布合同缺少 UUID'); return }
    try {
      const param = serialiseCompositeParameters(pending.fields, pending.drafts)
      const requirements = Array.isArray(contract.executor_requirements) ? contract.executor_requirements : []
      const deviceBindings = Object.fromEntries(requirements.map((requirement: Record<string, unknown>) => {
        const templateUuid = String(requirement.resource_template_uuid || '')
        const device = materials.find((material) => material.resourceTemplateUuid === templateUuid)
        return [String(requirement.key || ''), device?.uuid || '']
      }).filter(([key, value]) => key && value))
      if (requirements.length && Object.keys(deviceBindings).length !== requirements.length) {
        onNotify('引用失败：当前工作区没有满足子工作流要求的设备实例')
        return
      }
      await insertCompositeWorkflow({
        parentWorkflowUuid: detail.uuid,
        revision: detail.revision,
        contractUuid,
        deviceBindings,
        pose: { x: 120 + graphNodes.length * 220, y: 180 },
        param,
      })
      setChildPickerOpen(false)
      setPendingChildInvocation(null)
      onNotify(`已引用实验操作“${String(contract.name || contract.workflow_uuid)}”，当前工作流已回到 source`)
      await queryClient.invalidateQueries({ queryKey: ['workflow-graph', detail.uuid] })
      await queryClient.invalidateQueries({ queryKey: ['edge-snapshot'] })
    } catch (error) { onNotify(`引用失败：${error instanceof Error ? error.message : '未知错误'}`) }
  }

  async function refreshPreflight() {
    const result = await preflightQuery.refetch()
    onNotify(result.isError ? 'Preflight 读取失败' : 'Preflight 报告已更新')
  }

  async function copySource() {
    if (!sourceQuery.data) return
    try {
      await navigator.clipboard.writeText(sourceQuery.data.pythonSource)
      onNotify('工作流源码已复制')
    } catch {
      onNotify('复制失败，请在源码区域手动选择复制')
    }
  }

  const preflightTone: ReadinessTone = preflightQuery.isFetching
    ? 'loading'
    : preflightQuery.isError
      ? 'error'
      : preflight?.status === 'runnable_now'
        ? 'ready'
        : preflight?.status === 'invalid'
          ? 'error'
          : preflight
            ? 'warning'
            : 'neutral'
  const preflightStatusLabel = preflight?.status === 'runnable_now'
    ? '当前可提交，派发时仍会复核'
    : preflight?.status === 'temporarily_unavailable'
      ? '当前条件暂不可用'
      : preflight?.status === 'invalid'
        ? '执行计划无效'
        : preflightQuery.isError
          ? (preflightQuery.error instanceof Error ? preflightQuery.error.message : '检查失败')
          : preflightQuery.isFetching
            ? '正在读取 Edge 报告'
            : '尚未检查'
  return (
    <div className="page workflows-page">
      <PageHeader
        eyebrow="WORKFLOW DEFINITIONS"
        title="工作流"
        description="管理 Edge 已加载定义、发布修订、运行合同和工作流节点拓扑。"
        actions={
          <>
            <input ref={pythonImportRef} hidden type="file" accept=".py,text/x-python" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void importFile(file, 'python') }} />
            <input ref={jsonImportRef} hidden type="file" accept=".json,application/json" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void importFile(file, 'json') }} />
            <Button icon={<FileInput size={16} />} disabled={!connected} onClick={() => pythonImportRef.current?.click()}>导入 Python</Button>
            <Button icon={<FileJson size={16} />} disabled={!connected} onClick={() => jsonImportRef.current?.click()}>导入 JSON</Button>
            <Button tone="primary" icon={<Plus size={17} />} onClick={() => onNotify('新建工作流将在作者工作台中开放')}>新建工作流</Button>
          </>
        }
      />

      <div className={`workflow-layout workflow-layout-${workspaceView}`}>
        <Panel className="workflow-library">
          <PanelHeader title="工作流目录" description={catalogStatus === 'loading' ? '正在读取目录…' : catalogStatus === 'error' ? '目录连接失败' : `${availableWorkflows.length} 个工作流${availableWorkflows.some(workflow => workflow.workflowType === 'experiment_operation') ? '（含当前实验操作）' : ''}`} action={<WorkflowIcon size={18} />} />
          <label className="search-field">
            <Search size={16} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索名称或 UUID" />
          </label>
          <div className="workflow-list">
            {visibleWorkflows.map((workflow) => {
              const hasLoadedNodeCount = Object.prototype.hasOwnProperty.call(knownNodeCounts, workflow.uuid)
              const nodeCount = knownNodeCounts[workflow.uuid] ?? workflow.nodeCount
              return (
                <button
                  type="button"
                  className={`workflow-list-item ${selected?.uuid === workflow.uuid ? 'active' : ''}`}
                  key={workflow.uuid}
                  onClick={() => {
                    setSelectedId(workflow.uuid)
                    onSelectWorkflow?.({ workflowUuid: workflow.uuid, revision: workflow.revision })
                  }}
                >
                  <span className="workflow-list-icon"><GitBranch size={17} /></span>
                  <div><strong>{workflow.name}</strong><small>r{workflow.revision} · {hasLoadedNodeCount || workflow.nodeCount > 0 ? `${nodeCount} 节点` : '节点数待加载'}</small></div>
                  <em>{tagForWorkflow(workflow)}</em>
                </button>
              )
            })}
            {!visibleWorkflows.length ? <EmptyState title={catalogStatus === 'loading' ? '正在加载工作流' : catalogStatus === 'error' ? '工作流目录加载失败' : '没有匹配的工作流'} description={catalogStatus === 'loading' ? '目录加载完成后会显示工作流，请稍候。' : catalogStatus === 'error' ? '请检查服务连接后重试，当前无法确认目录内容。' : '调整搜索词后重试。'} /> : null}
          </div>
          <div className="library-summary">
            <div><span>已发布</span><strong>{availableWorkflows.filter((item) => item.status === 'published').length}</strong></div>
            <div><span>输入合同</span><strong>{workflows.reduce((sum, item) => sum + item.inputContract.length, 0)}</strong></div>
          </div>
        </Panel>

        {detail ? (
          <div className="workflow-main-column">
            <Panel className="workflow-definition">
              <div className="definition-hero">
                <span className="definition-icon"><WorkflowIcon size={22} /></span>
                <div><span className="definition-tag">{tagForWorkflow(detail)}</span><h2>{detail.name}</h2><p>{detail.description}</p></div>
                <div className="definition-actions">
                  <Button
                    tone="ghost"
                    icon={<Code2 size={15} />}
                    disabled={!connected}
                    onClick={() => {
                      if (!detail.sourcePath) {
                        onNotify('该定义没有可读取的 Python 源文件')
                        return
                      }
                      setSourceTarget({ uuid: detail.uuid, name: detail.name, path: detail.sourcePath })
                    }}
                  >查看源码</Button>
                  {!selectedTaskSnapshotUuid && detail.status !== 'published' ? (
                    <Button
                      icon={publishMutation.isPending ? <LoaderCircle className="spin" size={15} /> : <Send size={15} />}
                      disabled={!connected || publishMutation.isPending}
                      onClick={() => publishMutation.mutate({ workflowUuid: detail.uuid, revision: detail.revision, name: detail.name })}
                    >{publishMutation.isPending ? '发布中…' : '发布'}</Button>
                  ) : null}
                  <Button
                    icon={preflightQuery.isFetching ? <LoaderCircle className="spin" size={15} /> : <ShieldCheck size={15} />}
                    disabled={!connected || preflightQuery.isFetching}
                    onClick={() => {
                      setWorkspaceView('diagnostics')
                      void refreshPreflight()
                    }}
                  >运行 Preflight</Button>
                  <Button tone="primary" icon={<Play size={15} />} onClick={() => setWorkspaceView('run')}>进入运行准备</Button>
                </div>
              </div>
              <div className="definition-metrics">
                <div><span>工作流 UUID</span><code>{detail.uuid}</code></div>
                <div>
                  <span>应用修订</span>
                  <strong>r{detail.revision}</strong>
                  {selectedTaskSnapshotUuid ? <small className="revision-target-warning">Task 冻结修订</small> : null}
                </div>
                <div><span>拓扑规模</span><strong>{graphNodes.length || detail.nodeCount || '—'} 节点 · {graphEdges.length} 连线</strong></div>
                <div><span>来源</span><code>{detail.sourcePath || 'Edge runtime'}</code></div>
              </div>
              <div className="workflow-workspace-tabs" aria-label="工作流工作区视图">
                <button className={workspaceView === 'topology' ? 'active' : ''} onClick={() => setWorkspaceView('topology')}><GitBranch size={14} />拓扑</button>
                <button className={workspaceView === 'contract' ? 'active' : ''} onClick={() => setWorkspaceView('contract')}><FileJson size={14} />合同</button>
                <button className={workspaceView === 'diagnostics' ? 'active' : ''} onClick={() => setWorkspaceView('diagnostics')}><ShieldCheck size={14} />诊断</button>
                <button className={workspaceView === 'run' ? 'active' : ''} onClick={() => setWorkspaceView('run')}><Play size={14} />运行准备</button>
              </div>
              {workspaceView === 'topology' ? <div className="workflow-canvas">
                <div className="canvas-toolbar"><span><GitBranch size={15} />发布修订拓扑</span><div className="canvas-toolbar-actions"><small>{graphQuery.isFetching ? '正在读取图…' : graphQuery.isError ? '图接口不可用，显示定义摘要' : 'Edge 权威图'}</small><Button icon={<WorkflowIcon size={13} />} disabled={!connected || !detail} onClick={() => setChildPickerOpen((open) => !open)}>引用已发布子工作流</Button></div></div>
                {childPickerOpen ? <div className="child-workflow-picker"><header><div><strong>{pendingChildInvocation ? `配置“${String(pendingChildInvocation.contract.name || pendingChildInvocation.contract.workflow_uuid)}”` : '选择可引用的实验操作'}</strong><small>{pendingChildInvocation ? '参数只属于本次调用；再次引用同一操作时可填写不同值。' : '仅展示已发布的 experiment_operation；普通工作流不会出现在这里。'}</small></div><button type="button" onClick={() => { setPendingChildInvocation(null); setChildPickerOpen(false) }}>×</button></header>{pendingChildInvocation ? <section className="child-invocation-form"><CompositeWorkflowParameterEditor operationName={String(pendingChildInvocation.contract.name || pendingChildInvocation.contract.workflow_uuid)} invocationIndex={1} fields={pendingChildInvocation.fields} drafts={pendingChildInvocation.drafts} onChange={(parameterName, text) => setPendingChildInvocation((current) => current ? { ...current, drafts: { ...current.drafts, [parameterName]: text } } : current)} /><footer><Button onClick={() => setPendingChildInvocation(null)}>返回列表</Button><Button tone="primary" disabled={Boolean(compositeParameterProblem(pendingChildInvocation.fields, pendingChildInvocation.drafts))} onClick={() => void referenceChildWorkflow(pendingChildInvocation)}>确认引用{String(pendingChildInvocation.contract.name || pendingChildInvocation.contract.workflow_uuid)}</Button></footer></section> : publishedChildrenQuery.isFetching ? <p>正在读取已发布实验操作…</p> : publishedChildrenQuery.data?.length ? <div>{publishedChildrenQuery.data.map((contract) => <button type="button" key={String(contract.uuid)} onClick={() => { const fields = compositeContractFields(contract.input_contract?.parameters); setPendingChildInvocation({ contract, fields, drafts: compositeParameterDrafts(fields) }) }}><WorkflowIcon size={15} /><span><strong>{String(contract.name || contract.workflow_uuid)}</strong><small>{String(contract.workflow_uuid || '')} · r{String(contract.workflow_revision || contract.version || '')} · {Array.isArray(contract.input_contract?.parameters) ? contract.input_contract.parameters.length : 0} 个输入 · {Array.isArray(contract.output_contract?.outputs) ? contract.output_contract.outputs.length : 0} 个输出</small></span><Plus size={14} /></button>)}</div> : <p>暂无可引用的已发布实验操作。</p>}</div> : null}
                <WorkflowDag
                  key={`${detail.uuid}:${detail.revision}`}
                  nodes={graphNodes}
                  edges={graphEdges}
                  loading={graphQuery.isFetching}
                  error={graphQuery.isError}
                  onRetry={() => void graphQuery.refetch()}
                />
              </div> : null}
              {workspaceView === 'contract' ? (
                <div className="workflow-inline-contracts">
                  <section><header><strong>运行输入</strong><span>{detail.inputContract.length}</span></header>{detail.inputContract.map((field) => <div key={field.name}><code>{field.name}</code><span>{field.type}</span><em>{field.required ? '必填' : '可选'}</em></div>)}</section>
                  <section><header><strong>正式输出</strong><span>{detail.outputContract.length}</span></header>{detail.outputContract.map((field) => <div key={field.name}><code>{field.name}</code><span>{field.type}</span><em>输出</em></div>)}</section>
                </div>
              ) : null}
              {workspaceView === 'diagnostics' ? (
                <div className="workflow-diagnostics">
                  <header>
                    <div><strong>运行前诊断</strong><small>来自 Edge Preflight 的只读判断，不创建任务或占用资源</small></div>
                    <div className="diagnostic-actions">
                      <span className={`diagnostic-status readiness-${preflightTone}`} role={preflightQuery.isError ? 'alert' : 'status'}><ReadinessIcon tone={preflightTone} />{preflightStatusLabel}</span>
                      <Button icon={preflightQuery.isFetching ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />} disabled={!connected || preflightQuery.isFetching} onClick={() => void refreshPreflight()}>{preflight ? '刷新 Preflight' : '运行 Preflight'}</Button>
                    </div>
                  </header>
                  {!preflight ? <EmptyState title="尚未运行 Preflight" description="执行检查后可按节点查看阻断、延后与人工确认项。" /> : (
                    <div className="diagnostic-checks">
                      {preflight.checks.map((check, index) => <div className={`diagnostic-check check-${check.status}`} key={`${check.code}-${index}`}><span><ReadinessIcon tone={check.status === 'passed' ? 'ready' : check.status === 'blocked' ? 'error' : 'warning'} /></span><div><strong>{check.nodeName || check.type}</strong><p>{check.message}</p><code>{check.code}</code></div><em>{check.status}</em></div>)}
                      {!preflight.checks.length ? <EmptyState title="没有诊断项" description="当前报告未返回逐项检查结果。" /> : null}
                    </div>
                  )}
                </div>
              ) : null}
              {workspaceView === 'run' ? (
                <div className="workflow-run-preparation">
                  <header className="run-preparation-header"><div><span>COMPOSITE RUN SETUP</span><strong>工作流 × 物料运行准备</strong><small>绑定公开输入与权威物料，检查当前引用和库位，再由 Edge 决定是否准入。</small></div><span className={`run-gate gate-${preflightTone}`}><ReadinessIcon tone={preflightTone} />{preflightStatusLabel}</span></header>
                  <div className="run-preparation-grid">
                    <section className="run-input-panel">
                      <div className="run-section-title"><span>01</span><div><strong>运行输入与物料绑定</strong><small>{detail.inputContract.length} 个公开输入 · {detail.inputContract.filter(resourceSlotSchema).length} 个手动物料槽位 · {materialDependencies.length} 个自动物料源</small></div></div>
                      {capabilities.canDebug ? (
                        <label className="run-description"><span>运行方式</span><select aria-label="运行方式" value={runMode} onChange={(event) => setRunMode(event.target.value as 'normal' | 'step')}><option value="normal">自动运行</option><option value="step">单步调试</option></select></label>
                      ) : null}
                      <label className="run-description"><span>任务优先级</span><select aria-label="任务优先级" value={runPriority} onChange={(event) => setRunPriority(event.target.value as WorkflowTaskPriority)}><option value="normal">普通优先级</option><option value="high">高优先级</option></select></label>
                      <label className="run-description"><span>任务描述</span><input value={runDescription} onChange={(event) => setRunDescription(event.target.value)} /></label>
                      <div className="run-fields">
                        {detail.inputContract.map((field) => {
                          const slot = resourceSlotSchema(field)
                          const allowed = Array.isArray(slot?.allowed_resource_template_uuids) ? new Set(slot.allowed_resource_template_uuids.map(String)) : undefined
                          const options = slot ? materials.filter((material) => !allowed || Boolean(material.resourceTemplateUuid && allowed.has(material.resourceTemplateUuid))) : []
                          const sites = siteOptions.get(field.name)
                          return <label className={slot ? 'run-field resource-binding-field' : 'run-field'} key={field.name}><span><code>{field.title || field.name}</code><em>{field.required ? '必填' : '可选'} · {field.type}</em></span>{sites ? <select value={runInput[field.name] || ''} onChange={(event) => setRunInput((current) => ({ ...current, [field.name]: event.target.value }))}><option value="">{sites.length ? '请选择来源库位' : '没有符合要求的库位'}</option>{sites.map((site) => <option key={site.value} value={site.value}>{site.label}</option>)}</select> : slot ? <select value={runInput[field.name] || ''} onChange={(event) => setRunInput((current) => ({ ...current, [field.name]: event.target.value }))}><option value="">{options.length ? '选择权威物料实例' : '没有符合模板约束的物料'}</option>{options.map((material) => <option key={material.uuid} value={material.uuid}>{material.name} · {material.currentLocation.label}</option>)}</select> : <input value={runInput[field.name] || ''} onChange={(event) => setRunInput((current) => ({ ...current, [field.name]: event.target.value }))} placeholder={field.type} />}</label>
                        })}
                        {!detail.inputContract.length ? <EmptyState title="无需运行输入" description="该工作流可直接进入 Preflight。" /> : null}
                      </div>
                    </section>
                    {inventoryRequirements.length ? (
                      <section className="run-input-panel reagent-binding-panel">
                        <div className="run-section-title"><span>01b</span><div><strong>试剂用量需求</strong><small>{inventoryRequirements.length} 条 · 建任务时按来源容器自动预留，成功后按此量扣减</small></div></div>
                        <div className="bound-material-list">
                          {inventoryRequirements.map((requirement) => (
                            <article key={requirement.uuid}><header><span><FlaskConical size={15} /></span><div><strong>{requirement.requirementKey}</strong><code>需 {requirement.requiredQuantity} {requirement.quantityUnit}</code></div></header>{requirement.description ? <p>{requirement.description}</p> : null}</article>
                          ))}
                        </div>
                      </section>
                    ) : null}
                    <aside className="run-context-panel">
                      <div className="run-section-title"><span>02</span><div><strong>物料上下文</strong><small>权威库位与未结束任务引用</small></div></div>
                      <div className="bound-material-list">
                        {boundMaterials.map(({ field, material }) => <article key={field.name}><header><span><FlaskConical size={15} /></span><div><strong>{material.name}</strong><code>{field.name}</code></div></header><dl><div><dt>当前位置</dt><dd>{material.currentLocation.label}</dd></div><div><dt>任务引用</dt><dd className={material.taskReferences.length ? 'risk' : ''}>{material.taskReferences.length ? `${material.taskReferences.length} 个未结束任务` : '无未结束任务引用'}</dd></div><div><dt>UUID</dt><dd title={material.uuid}>{material.uuid}</dd></div></dl></article>)}
                        {materialDependencies.map((dependency) => <article className="material-dependency-card" key={dependency.uuid}><header><span><Network size={15} /></span><div><strong>{dependency.name}</strong><code>运行时自动解析 · {dependency.mode}</code></div><em>{dependency.candidates.length} 个候选</em></header><dl><div><dt>模板 UUID</dt><dd title={dependency.templateUuid}>{dependency.templateUuid || '未声明'}</dd></div><div><dt>挂载资源</dt><dd title={dependency.mountUuid}>{dependency.mountUuid || '未声明'}</dd></div>{dependency.candidates.slice(0, 2).map((material) => <div key={material.uuid}><dt>候选物料</dt><dd title={material.uuid}>{material.name} · {material.currentLocation.label}</dd></div>)}</dl></article>)}
                        {!boundMaterials.length && !materialDependencies.length ? <EmptyState title="没有物料依赖" description="该工作流既没有 ResourceSlot，也没有 Material Source。" /> : null}
                      </div>
                    </aside>
                  </div>
                  <footer className="run-preparation-footer"><div className="run-steps"><span className={requiredInputReady ? 'done' : ''}><i>1</i>输入完整</span><b /><span className={preflight ? 'done' : ''}><i>2</i>Preflight</span><b /><span className={preflight?.canRun ? 'done' : ''}><i>3</i>Edge 准入</span></div><div><Button icon={preflightQuery.isFetching ? <LoaderCircle className="spin" size={14} /> : <ShieldCheck size={14} />} disabled={!connected || !requiredInputReady || preflightQuery.isFetching} onClick={() => void preflightQuery.refetch()}>运行 Preflight</Button><Button tone="primary" icon={taskMutation.isPending ? <LoaderCircle className="spin" size={14} /> : <Send size={14} />} disabled={!connected || !requiredInputReady || !preflight?.canRun || taskMutation.isPending} onClick={() => taskMutation.mutate()}>提交任务</Button></div></footer>
                </div>
              ) : null}
            </Panel>

            {workspaceView === 'topology' ? <section className="workflow-bottom-grid">
              <Panel className="contract-panel">
                <PanelHeader title="输入合同" description="创建任务时允许提交的参数" action={<span className="count-badge">{detail.inputContract.length}</span>} />
                <div className="contract-list">
                  {detail.inputContract.length ? detail.inputContract.map((field) => (
                    <div key={field.name}><code>{field.name}</code><span>{field.type}</span><em>{field.required ? '必填' : `默认 ${field.defaultValue ?? '—'}`}</em></div>
                  )) : <EmptyState title="没有输入参数" description="该工作流未声明输入参数；能否运行请查看 Preflight。" />}
                </div>
              </Panel>
              <Panel className="contract-panel">
                <PanelHeader title="输出合同" description="任务完成后的正式结果" action={<span className="count-badge">{detail.outputContract.length}</span>} />
                <div className="contract-list">
                  {detail.outputContract.length ? detail.outputContract.map((field) => (
                    <div key={field.name}><code>{field.name}</code><span>{field.type}</span><em>正式输出</em></div>
                  )) : <EmptyState title="没有正式输出" description="结果保留在 Job return_info。" />}
                </div>
              </Panel>
            </section> : null}
          </div>
        ) : <Panel><EmptyState title={catalogStatus === 'loading' ? '等待工作流目录加载' : catalogStatus === 'error' ? '无法读取工作流定义' : '没有工作流定义'} description={catalogStatus === 'ready' ? '确认 Edge 已加载工作流运行时。' : '服务连接完成后再显示工作流详情。'} /></Panel>}

      </div>
      {sourceTarget ? (
        <div
          className="dialog-backdrop workflow-source-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setSourceTarget(null)
          }}
        >
          <section
            className="workflow-source-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="workflow-source-title"
          >
            <header>
              <div>
                <span>PYTHON WORKFLOW SOURCE</span>
                <h2 id="workflow-source-title">{sourceTarget.name} 源码</h2>
                <p>只读展示 Edge 当前授权工作区中的源码文件。</p>
              </div>
              <button type="button" aria-label="关闭源码查看器" onClick={() => setSourceTarget(null)}><X size={17} /></button>
            </header>
            <div className="workflow-source-toolbar">
              <div><Code2 size={15} /><code>{sourceTarget.path}</code></div>
              <Button icon={<Copy size={14} />} disabled={!sourceQuery.data} onClick={() => void copySource()}>复制源码</Button>
            </div>
            <div className="workflow-source-content">
              {sourceQuery.isFetching ? <EmptyState title="正在读取源码" description="正在从 Edge 授权工作区读取 Python 文件。" /> : null}
              {sourceQuery.isError ? (
                <div className="workflow-source-error" role="alert">
                  <AlertCircle size={18} />
                  <div><strong>源码读取失败</strong><p>{sourceQuery.error instanceof Error ? sourceQuery.error.message : '未知错误'}</p></div>
                  <Button icon={<RefreshCw size={14} />} onClick={() => void sourceQuery.refetch()}>重试</Button>
                </div>
              ) : null}
              {sourceQuery.data ? <pre aria-label="Python 源码"><code>{sourceQuery.data.pythonSource}</code></pre> : null}
            </div>
            {sourceQuery.data ? (
              <footer>
                <span>当前源码状态：{sourceQuery.data.state}</span>
                <span>工作流修订：r{sourceQuery.data.workflowRevision}</span>
                <code title={sourceQuery.data.sourceUri}>{sourceQuery.data.sourceUri}</code>
              </footer>
            ) : null}
          </section>
        </div>
      ) : null}
    </div>
  )
}
