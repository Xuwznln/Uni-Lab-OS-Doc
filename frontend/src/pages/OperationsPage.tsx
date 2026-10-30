import { useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, ChevronLeft, ChevronRight, Eye, FileInput, FileJson, FlaskConical, GripVertical, Pencil, Plus, Save, Search, Send, Sparkles, Trash2, Workflow } from 'lucide-react'
import { createExperimentOperation, deleteExperimentOperation, ensureWorkflowSequenceEdges, importWorkflowJson, importWorkflowPython, insertCompositeWorkflow, loadActionOutputs, loadActionParameters, loadActionTemplates, loadControlTemplates, loadExperimentOperations, loadOperationCategories, loadPublishedWorkflowContracts, loadWorkflowGraph, patchWorkflowNode, publishWorkflow, updateExperimentOperation } from '../lib/edgeClient'
import { operationOwnedNodes } from '../lib/operationGraphScope'
import type { OperationInputBinding } from '../lib/edgeClient'
import type { ActionParameterRecord, ActionTemplateRecord, ControlTemplateRecord, ContractField, MaterialRecord, WorkflowDefinition } from '../types'
import { Button, EmptyState, PageHeader, Panel, PanelHeader } from '../components/ui'
import { ControlParameterEditor } from '../components/ControlParameterEditor'
import { ActionParameterEditor, hasUsableInputBinding, removeInvalidUpstreamBindings } from '../components/ActionParameterEditor'
import type { DraftAction, DraftInputBinding } from '../components/ActionParameterEditor'
import { CompositeWorkflowParameterEditor, compositeContractFields, compositeParameterDrafts, compositeParameterProblem, serialiseCompositeParameters } from '../components/CompositeWorkflowParameterEditor'

/**
 * 把页面草稿中的设备动作（Action）身份转换为保存接口接受的输入来源。
 * @param bindings 以目标输入句柄 UUID 为键的页面草稿绑定。
 * @returns 保留真实来源句柄、并把来源草稿身份交给保存层解析的绑定。
 */
function persistInputBindings(bindings: Record<string, DraftInputBinding>): Record<string, OperationInputBinding> {
  return Object.fromEntries(Object.entries(bindings).map(([handleUuid, binding]) => [
    handleUuid,
    'parameter' in binding
      ? { parameter: binding.parameter }
      : { kind: 'node_output', sourceNodeId: binding.sourceActionId, sourceHandleUuid: binding.sourceHandleUuid },
  ]))
}
interface DraftControl { id: string; nodeUuid?: string; templateUuid: string; nodeType: string; name: string; description?: string; param: Record<string, unknown>; parameterSchema: Record<string, unknown> }
interface ChildWorkflowRequirement { key: string; resourceTemplateUuid: string; displayName: string }
interface ChildWorkflowRef {
  /** 父工作流编辑草稿中的独立调用身份；同一发布合同可出现多次。 */
  id: string
  contractUuid: string
  workflowUuid: string
  name: string
  revision: number
  invocationUuid?: string
  x: number
  y: number
  requirements: ChildWorkflowRequirement[]
  deviceBindings: Record<string, string>
  parameters: ContractField[]
  parameterDrafts: Record<string, string>
}
interface EditableContractField {
  id: string
  name: string
  type: string
  required: boolean
  defaultText: string
  title: string
  description: string
  schema: Record<string, unknown>
  implicit?: boolean
}

const CONTRACT_TYPES = [
  { value: 'string', label: '文本' },
  { value: 'integer', label: '整数' },
  { value: 'number', label: '数值' },
  { value: 'boolean', label: '布尔值' },
  { value: 'object', label: '对象（JSON）' },
  { value: 'array', label: '数组（JSON）' },
  { value: 'ResourceSlot', label: '资源槽位' },
]

function contractType(field: ContractField): string {
  if (field.schema.$slot) return 'ResourceSlot'
  const type = field.schema.type || field.type
  return typeof type === 'string' && CONTRACT_TYPES.some((item) => item.value === type) ? type : 'string'
}

function contractDefaultText(field: ContractField): string {
  if (field.defaultValue === undefined) return ''
  return typeof field.defaultValue === 'object' ? JSON.stringify(field.defaultValue) : String(field.defaultValue)
}

function editableField(field?: ContractField, kind: 'input' | 'output' = 'input'): EditableContractField {
  const fallbackName = kind === 'input' ? 'input_1' : 'output_1'
  const type = field ? contractType(field) : 'string'
  return {
    id: crypto.randomUUID(), name: field?.name || fallbackName, type, required: kind === 'output' ? false : field ? Boolean(field.required) : true,
    defaultText: field ? contractDefaultText(field) : '', title: field?.title || '', description: field?.description || '',
    schema: field?.schema && Object.keys(field.schema).length ? { ...field.schema } : { type: type === 'ResourceSlot' ? undefined : type },
    implicit: field?.implicit,
  }
}

function parseContractDefault(field: EditableContractField): unknown {
  if (field.required || !field.defaultText.trim()) return undefined
  if (field.type === 'integer') return Number.parseInt(field.defaultText, 10)
  if (field.type === 'number') return Number(field.defaultText)
  if (field.type === 'boolean') return field.defaultText === 'true'
  if (field.type === 'object' || field.type === 'array') return JSON.parse(field.defaultText)
  if (field.type === 'ResourceSlot') return JSON.parse(field.defaultText)
  return field.defaultText
}

function serialiseInputContract(fields: EditableContractField[]): Record<string, unknown> {
  return { version: 1, parameters: fields.map((field) => {
    const schema = field.type === 'ResourceSlot' ? { $slot: 'ResourceSlot' } : { type: field.type }
    const descriptor: Record<string, unknown> = { name: field.name.trim(), schema, required: field.required }
    if (field.title.trim()) descriptor.title = field.title.trim()
    if (field.description.trim()) descriptor.description = field.description.trim()
    if (!field.required) descriptor.default = parseContractDefault(field)
    return descriptor
  }) }
}

function serialiseOutputContract(fields: EditableContractField[]): Record<string, unknown> {
  return { version: 1, outputs: fields.map((field) => {
    const schema = field.type === 'ResourceSlot' ? { $slot: 'ResourceSlot' } : { type: field.type }
    const descriptor: Record<string, unknown> = { name: field.name.trim(), schema, implicit: Boolean(field.implicit) }
    if (field.title.trim()) descriptor.title = field.title.trim()
    if (field.description.trim()) descriptor.description = field.description.trim()
    return descriptor
  }) }
}

function displayContractType(type: string): string {
  return CONTRACT_TYPES.find((item) => item.value === type)?.label || type
}

function ContractEditor({ kind, fields, setFields }: { kind: 'input' | 'output'; fields: EditableContractField[]; setFields: (fields: EditableContractField[]) => void }) {
  const [expandedId, setExpandedId] = useState('')
  const label = kind === 'input' ? '输入参数' : '输出参数'
  function update(id: string, patch: Partial<EditableContractField>) {
    setFields(fields.map((field) => field.id === id ? { ...field, ...patch } : field))
  }
  function add() {
    const prefix = kind === 'input' ? 'input' : 'output'
    const used = new Set(fields.map((field) => field.name))
    let index = fields.length + 1
    while (used.has(`${prefix}_${index}`)) index += 1
    const field = editableField(undefined, kind)
    field.name = `${prefix}_${index}`
    setFields([...fields, field]); setExpandedId(field.id)
  }
  function remove(id: string) {
    setFields(fields.filter((field) => field.id !== id)); if (expandedId === id) setExpandedId('')
  }
  return <section className="contract-editor" aria-label={label}>
    <header className="contract-editor-heading"><div><strong>{label}</strong><small>{fields.length} 个公开参数 · 保存后由 OS 校验</small></div><button type="button" onClick={add}><Plus size={14} /> 添加{label}</button></header>
    <div className="contract-field-list">{fields.map((field, index) => <div className={`contract-field ${expandedId === field.id ? 'expanded' : ''}`} key={field.id}>
      <button type="button" className="contract-field-row" onClick={() => setExpandedId(expandedId === field.id ? '' : field.id)}><span className="contract-field-mark">◇</span><strong>{field.name || '未命名参数'}</strong><span>{displayContractType(field.type)}</span><em>{kind === 'output' ? '输出' : field.required ? '必填' : '有默认值'}</em><span className="contract-field-detail">详情</span></button>
      {expandedId === field.id ? <div className="contract-field-details">
        <label><span>变量名</span><input value={field.name} onChange={(event) => update(field.id, { name: event.target.value })} placeholder="例如 volume" /></label>
        <label><span>数据类型</span><select value={field.type} onChange={(event) => update(field.id, { type: event.target.value, schema: event.target.value === 'ResourceSlot' ? { $slot: 'ResourceSlot' } : { type: event.target.value } })}>{CONTRACT_TYPES.map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}</select></label>
        {kind === 'input' && <div className="contract-checks"><label><input type="checkbox" checked={field.required} onChange={(event) => update(field.id, { required: event.target.checked, defaultText: event.target.checked ? '' : field.defaultText })} /><span>必填参数</span></label><label><input type="checkbox" checked={!field.required && Boolean(field.defaultText)} onChange={(event) => update(field.id, { required: false, defaultText: event.target.checked ? (field.type === 'object' ? '{}' : field.type === 'array' ? '[]' : field.type === 'boolean' ? 'false' : '') : '' })} /><span>设置默认值</span></label></div>}
        {kind === 'input' && !field.required ? <label className="wide"><span>默认值 {field.type === 'object' || field.type === 'array' || field.type === 'ResourceSlot' ? '（JSON）' : ''}</span><input value={field.defaultText} onChange={(event) => update(field.id, { defaultText: event.target.value })} placeholder={field.type === 'object' ? '{}' : field.type === 'array' ? '[]' : `请输入${displayContractType(field.type)}`} /></label> : null}
        <label><span>显示名称（可选）</span><input value={field.title} onChange={(event) => update(field.id, { title: event.target.value })} placeholder="给使用者看的名称" /></label>
        <label><span>说明（可选）</span><input value={field.description} onChange={(event) => update(field.id, { description: event.target.value })} placeholder="说明这个参数的用途" /></label>
        {kind === 'output' ? <label className="contract-check-single"><input type="checkbox" checked={Boolean(field.implicit)} onChange={(event) => update(field.id, { implicit: event.target.checked })} /><span>系统隐式输出</span></label> : null}
        <button type="button" className="contract-delete" onClick={() => remove(field.id)}><Trash2 size={13} /> 删除参数</button>
      </div> : null}
    </div>)}</div>
    {!fields.length ? <div className="contract-empty">暂无{label}。点击上方“添加{label}”开始配置。</div> : null}
  </section>
}

/** 判断工作流图节点是否为调度器的结构控制节点。 */
function isControlNode(node: { type?: unknown; meta_data?: Record<string, any> }): boolean {
  const nodeType = String(node.type || '').toLowerCase()
  const executorKind = String(node.meta_data?.unilab?.executor_kind || '').toLowerCase()
  return nodeType === 'condition' || nodeType === 'repeat_until' || executorKind === 'condition' || executorKind === 'repeat_until'
}

/** 提取控制节点在所有调度绑定位置引用的 workflow_input 参数名。 */
function controlWorkflowParameters(controls: DraftControl[]): string[] {
  const names = new Set<string>()
  controls.forEach((control) => {
    // 条件节点使用 bindings；循环节点还允许在 initial_carry/next_carry
    // 中绑定工作流输入。三处都纳入输入合同，避免只在退出条件中绑定时被漏报。
    for (const key of ['bindings', 'initial_carry', 'next_carry']) {
      const bindings = control.param?.[key]
      if (!bindings || typeof bindings !== 'object' || Array.isArray(bindings)) continue
      Object.values(bindings as Record<string, unknown>).forEach((binding) => {
        if (!binding || typeof binding !== 'object') return
        const record = binding as Record<string, unknown>
        if (record.kind === 'workflow_input' && typeof record.parameter === 'string' && record.parameter.trim()) names.add(record.parameter.trim())
      })
    }
  })
  return [...names]
}

/** 为新控制节点创建可直接填写的结构参数；节点成员由配置面板选择。 */
function defaultControlParam(nodeType: string): Record<string, unknown> {
  if (nodeType === 'condition') return {
    predecessor_node_uuids: [], bindings: {},
    // label 由分支顺序映射为 if/elif/else，用户只需要填写条件和成员节点。
    branches: [{ label: 'if', condition: { lit: true }, node_uuids: [], entry_node_uuids: [], exit_node_uuids: [] }],
  }
  return {
    predecessor_node_uuids: [], successor_node_uuids: [], loop_variable: 'loop', max_iterations: 3,
    // 默认 carry 让新加入的循环节点即使只选择循环体也能保存；需要复杂状态时再改变量值。
    initial_carry: { done: { kind: 'literal', value: false } },
    next_carry: { done: { kind: 'literal', value: true } },
    until: { lit: true }, bindings: {},
    node_uuids: [], entry_node_uuids: [], exit_node_uuids: [],
  }
}

/** 判断控制节点是否缺少调度器运行所需的分支或循环体。 */
function controlStructureIncomplete(control: DraftControl): boolean {
  const param = control.param
  if (!param || typeof param !== 'object' || Array.isArray(param)) return true
  if (control.nodeType === 'condition') {
    if (!Array.isArray(param.branches) || param.branches.length === 0) return true
    return param.branches.some((branch) => {
      if (!branch || typeof branch !== 'object' || Array.isArray(branch)) return true
      const value = branch as Record<string, unknown>
      return !Array.isArray(value.node_uuids) || value.node_uuids.length === 0 || !Array.isArray(value.entry_node_uuids) || value.entry_node_uuids.length === 0 || !Array.isArray(value.exit_node_uuids) || value.exit_node_uuids.length === 0
    })
  }
  return !Array.isArray(param.node_uuids) || param.node_uuids.length === 0 || !Array.isArray(param.entry_node_uuids) || param.entry_node_uuids.length === 0 || !Array.isArray(param.exit_node_uuids) || param.exit_node_uuids.length === 0
}

function contractTypeForActionSchema(schema: Record<string, unknown>): string {
  if (schema.$slot) return 'ResourceSlot'
  const rawType = schema.type
  const type = Array.isArray(rawType) ? rawType.find((item) => item !== 'null') : rawType
  return typeof type === 'string' && CONTRACT_TYPES.some((item) => item.value === type) ? type : 'string'
}

function contractFieldForActionParameter(field: ActionParameterRecord, name: string): EditableContractField {
  const type = contractTypeForActionSchema(field.schema)
  return { id: crypto.randomUUID(), name, type, required: field.required, defaultText: '', title: field.displayName, description: '', schema: { ...field.schema } }
}

/**
 * 展示实验操作编排台，负责收集用户输入并调用公开工作流保存接口。
 * @param inputMaterials 当前 Edge 工作空间内可绑定的物料实例。
 * @param connected 当前页面是否可以访问 Edge 数据面。
 * @param onNotify 向应用外层报告成功或错误消息的回调。
 * @returns 实验操作列表或当前工作流编辑界面。
 */
export function OperationsPage({ materials: inputMaterials, connected, onNotify }: { materials: MaterialRecord[]; connected: boolean; onNotify: (message: string) => void }) {
  // Edge 以物料实例 UUID 作为固定执行器的权威身份。旧快照可能没有
  // source_node_id，因此在页面读模型中回退到物料 UUID，保证设备选择可用。
  const materials = useMemo(() => inputMaterials.map((material) => ({ ...material, sourceNodeId: material.sourceNodeId || material.uuid })), [inputMaterials])
  const queryClient = useQueryClient()
  const [creating, setCreating] = useState(false)
  const [editingUuid, setEditingUuid] = useState('')
  const [editingTags, setEditingTags] = useState<string[]>([])
  const [selectedOperation, setSelectedOperation] = useState<WorkflowDefinition | null>(null)
  const [selectedOperationNodes, setSelectedOperationNodes] = useState<Array<{ uuid: string; name: string; bindingLabel: string; childUuid?: string }>>([])
  const [expandedNodeCount, setExpandedNodeCount] = useState(0)
  const [selectedDeviceUuid, setSelectedDeviceUuid] = useState('')
  const [query, setQuery] = useState('')
  const [operationScope, setOperationScope] = useState<'top' | 'internal' | 'all'>('top')
  const [operationFolder, setOperationFolder] = useState('')
  const [operationSearch, setOperationSearch] = useState('')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [inputContractFields, setInputContractFields] = useState<EditableContractField[]>([])
  const [outputContractFields, setOutputContractFields] = useState<EditableContractField[]>([])
  const [categoryUuid, setCategoryUuid] = useState('')
  const [draft, setDraft] = useState<DraftAction[]>([])
  const [draftControls, setDraftControls] = useState<DraftControl[]>([])
  const [childWorkflowRefs, setChildWorkflowRefs] = useState<ChildWorkflowRef[]>([])
  const [childPickerOpen, setChildPickerOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [expandedActionId, setExpandedActionId] = useState('')
  const [expandedControlId, setExpandedControlId] = useState('')
  const [paramDrafts, setParamDrafts] = useState<Record<string, string>>({})
  const pythonImportRef = useRef<HTMLInputElement>(null)
  const jsonImportRef = useRef<HTMLInputElement>(null)
  const operationsQuery = useQuery({ queryKey: ['experiment-operations', 'library'], queryFn: ({ signal }) => loadExperimentOperations(signal, true), enabled: connected })
  const libraryOperations = operationsQuery.data || []
  const scopedOperations = libraryOperations.filter((operation) => operationScope === 'all' || operation.tags.includes('operation:internal') === (operationScope === 'internal'))
  const operationFolders = [...new Set(scopedOperations.flatMap((operation) => operation.tags.filter((tag) => tag.startsWith('folder:')).map((tag) => tag.slice(7))))].sort((a, b) => a.localeCompare(b, 'zh-CN'))
  const visibleOperations = scopedOperations.filter((operation) =>
    (!operationFolder || (operationFolder === '__untagged__' ? !operation.tags.some((tag) => tag.startsWith('folder:')) : operation.tags.includes(`folder:${operationFolder}`))) &&
    `${operation.name} ${operation.uuid} ${operation.description} ${operation.tags.join(' ')}`.toLocaleLowerCase().includes(operationSearch.trim().toLocaleLowerCase()),
  )
  const categoriesQuery = useQuery({ queryKey: ['operation-categories'], queryFn: ({ signal }) => loadOperationCategories(signal), enabled: connected })
  const actionsQuery = useQuery({ queryKey: ['action-templates'], queryFn: ({ signal }) => loadActionTemplates(signal), enabled: connected })
  const controlTemplatesQuery = useQuery({ queryKey: ['control-templates'], queryFn: ({ signal }) => loadControlTemplates(signal), enabled: connected, staleTime: 30_000 })
  const publishedChildrenQuery = useQuery({ queryKey: ['published-child-workflows'], queryFn: ({ signal }) => loadPublishedWorkflowContracts(signal, true), enabled: childPickerOpen && connected, staleTime: 15_000 })
  const deviceGroups = useMemo(() => {
    const groups = new Map<string, { uuid: string; name: string; actions: ActionTemplateRecord[] }>()
    for (const action of actionsQuery.data || []) {
      const uuid = action.resourceTemplate.uuid
      if (!uuid) continue
      const group = groups.get(uuid) || { uuid, name: action.resourceTemplate.displayName || action.resourceTemplate.name, actions: [] }
      group.actions.push(action); groups.set(uuid, group)
    }
    return [...groups.values()].sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))
  }, [actionsQuery.data])
  const selectedDevice = deviceGroups.find((device) => device.uuid === selectedDeviceUuid) || deviceGroups[0]
  const visibleActions = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return (selectedDevice?.actions || []).filter((action) => !needle || [action.displayName, action.name].join(' ').toLowerCase().includes(needle))
  }, [query, selectedDevice])
  const materialsForResourceTemplate = (resourceTemplateUuid: string) => materials.filter((material) => material.resourceTemplateUuid === resourceTemplateUuid)
  const missingBinding = draft.some((action) => !action.materialUuid || !action.deviceId)
  const missingChildBinding = childWorkflowRefs.some((child) => child.requirements.some((requirement) => !child.deviceBindings[requirement.key] && !materialsForResourceTemplate(requirement.resourceTemplateUuid).length))
  const childParameterProblem = childWorkflowRefs.map((child) => compositeParameterProblem(child.parameters, child.parameterDrafts)).find(Boolean) || ''
  const missingParameter = draft.some((action) => action.fields.some((field) => field.required && action.param[field.key] === undefined && !hasUsableInputBinding(action.inputBindings[field.handleUuid])))
  const manualConfirmationProblem = draft.some((action) => {
    const timeout = action.manualConfirmation?.timeoutSeconds
    return timeout !== undefined && (!Number.isInteger(timeout) || timeout < 1 || timeout > 86400)
  })
  const controlParameterProblem = draftControls.some((control) => !control.param || typeof control.param !== 'object' || Array.isArray(control.param))
  const duplicateContractName = [...inputContractFields, ...outputContractFields].some((field, index, all) => field.name.trim() && all.findIndex((candidate) => candidate.name.trim() === field.name.trim()) !== index)
  const emptyContractName = [...inputContractFields, ...outputContractFields].some((field) => !field.name.trim() || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(field.name.trim()))
  const contractProblem = emptyContractName ? '工作流参数名称必须是字母、数字或下划线，且不能以数字开头' : duplicateContractName ? '输入和输出参数名称不能重复' : ''
  const workflowParameters = [...new Set([...draft.flatMap((action) => Object.values(action.inputBindings).flatMap((binding) => 'parameter' in binding && binding.parameter ? [binding.parameter] : [])), ...controlWorkflowParameters(draftControls)])]
  const missingWorkflowContract = workflowParameters.some((parameter) => !inputContractFields.some((field) => field.name.trim() === parameter.trim()))
  const incompatibleWorkflowContract = draft.some((action) => action.fields.some((field) => {
    const binding = action.inputBindings[field.handleUuid]
    const parameter = binding && 'parameter' in binding ? binding.parameter.trim() : ''
    if (!parameter) return false
    const contract = inputContractFields.find((candidate) => candidate.name.trim() === parameter)
    return Boolean(contract && contract.type !== contractTypeForActionSchema(field.schema))
  }))
  const workflowContractProblem = missingWorkflowContract
    ? '节点绑定的工作流参数尚未加入输入参数合同'
    : incompatibleWorkflowContract
      ? '工作流参数类型与节点输入类型不兼容'
      : ''
  const saveProblem = !name.trim()
    ? '请填写操作名称'
    : !draft.length && !draftControls.length && !childWorkflowRefs.length
      ? '请从左侧加入至少一个 Action，或引用一个已发布子工作流'
      : missingBinding
        ? '存在未绑定设备实例的步骤'
        : missingChildBinding
          ? '子工作流存在未绑定的执行设备'
          : childParameterProblem
            ? childParameterProblem
            : manualConfirmationProblem
              ? '人工确认超时必须是 1 到 86400 之间的整数秒'
              : missingParameter
                ? '存在未配置的必填节点参数'
                : controlParameterProblem
                  ? '控制节点参数必须是对象'
                  : draftControls.some(controlStructureIncomplete)
                    ? '控制节点还没有配置分支或循环体'
                    : workflowContractProblem || contractProblem

  const builderNodes = useMemo(() => [
    ...childWorkflowRefs.map((child) => ({ id: child.id, nodeUuid: child.invocationUuid, name: child.name, kind: 'action' as const })),
    ...draft.map((action) => ({ id: action.id, nodeUuid: action.nodeUuid, name: action.name, kind: 'action' as const })),
    ...draftControls.map((control) => ({ id: control.id, nodeUuid: control.nodeUuid, name: control.name, kind: 'control' as const, nodeType: control.nodeType })),
  ], [childWorkflowRefs, draft, draftControls])

  function ensureInputContractForBinding(parameterName: string, field: ActionParameterRecord) {
    const parameter = parameterName.trim()
    if (!parameter || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(parameter)) return
    setInputContractFields((current) => {
      const existing = current.find((candidate) => candidate.name.trim() === parameter)
      if (existing) {
        // 保留用户已经修改的类型；保存前检查会报告不兼容复用，避免静默覆盖。
        return field.required && !existing.required
          ? current.map((candidate) => candidate.id === existing.id ? { ...candidate, required: true } : candidate)
          : current
      }
      return [...current, contractFieldForActionParameter(field, parameter)]
    })
  }

  /** 开始创建空白实验操作，并清除上一轮参数草稿与展开状态。 */
  function beginCreate() {
    if (!connected) return
    setCreating(true); setEditingUuid(''); setSelectedOperation(null); setName(''); setDescription(''); setCategoryUuid(''); setInputContractFields([]); setOutputContractFields([]); setChildWorkflowRefs([]); setChildPickerOpen(false); setDraft([]); setDraftControls([]); setParamDrafts({}); setExpandedActionId(''); setExpandedControlId('')
  }
  async function viewOperation(operation: WorkflowDefinition) {
    setSelectedOperation(operation)
    setSelectedOperationNodes([])
    try {
      const graph = await loadWorkflowGraph(operation.uuid)
      const nodes = operationOwnedNodes(graph.nodes).filter((node) => !node.parentUuid)
      setSelectedOperation({ ...graph.workflow, nodeCount: nodes.length })
      setExpandedNodeCount(graph.nodes.length)
      const deviceLabel = (uuid: string) => materials.find((material) => material.uuid === uuid)?.name || uuid
      setSelectedOperationNodes(nodes.map((node) => {
        const composite = node.meta_data?.unilab?.composite
        const devices = composite?.device_bindings && typeof composite.device_bindings === 'object'
          ? [...new Set(Object.values(composite.device_bindings).map(String))].map(deviceLabel)
          : []
        return {
          uuid: node.uuid, name: node.name || node.uuid,
          childUuid: composite?.child_workflow_uuid,
          bindingLabel: composite
            ? `子操作${devices.length ? ' · ' + devices.join('、') : ' · 在子操作中查看设备'}`
            : node.type === 'workflow_command'
              ? 'OS 本地服务，无需设备实例'
            : isControlNode(node) || node.kind === 'group'
              ? '结构节点，无需设备实例'
              : node.material_uuid
                ? `设备：${deviceLabel(node.material_uuid)}`
                : '运行时按设备类型选择',
        }
      }))
    } catch (error) { onNotify(`读取实验操作失败：${error instanceof Error ? error.message : '未知错误'}`) }
  }
  /**
   * 读取既有节点、真实句柄与数据边，并恢复成可编辑的页面草稿。
   * @param operation 用户选择编辑的实验操作定义。
   * @returns 图和句柄加载完成、页面状态恢复后结束。
   */
  async function beginEdit(operation: WorkflowDefinition) {
    if (!connected) return
    try {
      const graph = await loadWorkflowGraph(operation.uuid)
      const ownedNodes = operationOwnedNodes(graph.nodes)
      const actionNodes = ownedNodes
        .filter((node) => node.workflow_node_template_uuid && !node.meta_data?.unilab?.composite && !isControlNode(node))
        .map((node, index) => ({ node, index }))
        .sort((left, right) => {
          const leftSequence = Number(left.node.meta_data?.unilab?.sequence_index ?? left.node.authoringOrder ?? Number.MAX_SAFE_INTEGER)
          const rightSequence = Number(right.node.meta_data?.unilab?.sequence_index ?? right.node.authoringOrder ?? Number.MAX_SAFE_INTEGER)
          return leftSequence - rightSequence || left.index - right.index
        })
        .map(({ node }) => node)
      const actionNodeUuids = new Set(actionNodes.map((node) => String(node.uuid)))
      const actions = await Promise.all(actionNodes.map(async (node) => {
        const [fields, outputs] = await Promise.all([
          loadActionParameters(String(node.workflow_node_template_uuid)),
          loadActionOutputs(String(node.workflow_node_template_uuid)),
        ])
        const inputBindings: Record<string, DraftInputBinding> = {}
        const rawBindings = node.meta_data?.unilab?.input_bindings
        if (rawBindings && typeof rawBindings === 'object' && !Array.isArray(rawBindings)) {
          Object.entries(rawBindings).forEach(([handleUuid, value]) => {
            if (value && typeof value === 'object' && !Array.isArray(value) && typeof (value as Record<string, unknown>).parameter === 'string') {
              inputBindings[handleUuid] = { parameter: String((value as Record<string, unknown>).parameter) }
            }
          })
        }
        fields.forEach((field) => {
          const edge = graph.edges.find((candidate) => candidate.targetNodeUuid === node.uuid && candidate.targetHandleUuid === field.handleUuid && actionNodeUuids.has(String(candidate.sourceNodeUuid)) && candidate.sourceHandleUuid)
          if (edge) inputBindings[field.handleUuid] = { kind: 'node_output', sourceActionId: String(edge.sourceNodeUuid), sourceHandleUuid: String(edge.sourceHandleUuid) }
        })
        const actionTemplate = actionsQuery.data?.find((item) => item.uuid === String(node.workflow_node_template_uuid))
        const isManualConfirmation = String(node.type || '').toLowerCase() === 'manual_confirm'
        const rawManualConfirmation = node.manual_confirmation
        const rawTimeout = rawManualConfirmation && typeof rawManualConfirmation === 'object'
          ? Number(rawManualConfirmation.timeout_seconds)
          : NaN
        return {
          id: crypto.randomUUID(), nodeUuid: String(node.uuid), templateUuid: String(node.workflow_node_template_uuid), materialUuid: String(node.material_uuid || ''),
          // 老版本导入的节点可能没有 executor_binding；OS 的权威固定执行器
          // 身份就是 material_uuid，用它补齐，避免保存被前端提前拦截。
          deviceId: String(node.meta_data?.unilab?.executor_binding?.device_id || node.material_uuid || ''), name: String(node.name || ''), description: String(node.description || node.name || ''),
          // 记录模板原始类型；取消人工确认时恢复设备动作类型，而不是把
          // manual_confirm 当成一个新的设备模板类型提交给 OS。
          nodeType: actionTemplate?.nodeType || (isManualConfirmation ? 'ILab' : String(node.type || 'ILab')),
          manualConfirmation: isManualConfirmation
            ? { timeoutSeconds: Number.isFinite(rawTimeout) ? rawTimeout : 3600 }
            : undefined,
          fields, outputs, param: { ...(node.param || {}) }, inputBindings,
        }
      }))
      const actionIdByNodeUuid = new Map(actions.map((action) => [String(action.nodeUuid || ''), action.id]))
      actions.forEach((action) => Object.entries(action.inputBindings).forEach(([handleUuid, binding]) => {
        if (!('kind' in binding) || binding.kind !== 'node_output') return
        const sourceActionId = actionIdByNodeUuid.get(binding.sourceActionId)
        if (sourceActionId) action.inputBindings[handleUuid] = { ...binding, sourceActionId }
      }))
      const controls: DraftControl[] = ownedNodes.filter((node) => node.workflow_node_template_uuid && !node.meta_data?.unilab?.composite && isControlNode(node)).map((node) => {
        const template = controlTemplatesQuery.data?.find((item) => item.uuid === String(node.workflow_node_template_uuid))
        return {
          id: crypto.randomUUID(), nodeUuid: String(node.uuid), templateUuid: String(node.workflow_node_template_uuid), nodeType: String(node.type || node.meta_data?.unilab?.executor_kind || ''),
          name: String(node.name || ''), description: String(node.description || node.name || ''),
          param: node.param && typeof node.param === 'object' && !Array.isArray(node.param) ? { ...node.param } : {},
          parameterSchema: template?.parameterSchema || { type: 'object' },
        }
      })
      const children: ChildWorkflowRef[] = ownedNodes.filter((node) => !node.parentUuid && node.meta_data?.unilab?.composite).map((node, index) => {
        const composite = node.meta_data?.unilab?.composite || {}
        const rawPose = (node as unknown as { pose?: Record<string, unknown> }).pose || {}
        const requirements = Array.isArray(composite.executor_requirements) ? composite.executor_requirements.map((item: Record<string, unknown>) => ({ key: String(item.key || ''), resourceTemplateUuid: String(item.resource_template_uuid || ''), displayName: String(item.display_name || item.key || '执行设备') })).filter((item: ChildWorkflowRequirement) => item.key) : []
        const bindings = composite.device_bindings && typeof composite.device_bindings === 'object' ? Object.fromEntries(Object.entries(composite.device_bindings).map(([key, value]) => [key, String(value)])) : {}
        const parameters = compositeContractFields(composite.contract_compatibility?.parameters)
        return { id: String(node.uuid), contractUuid: String(composite.contract_uuid || ''), workflowUuid: String(composite.child_workflow_uuid || ''), name: String(node.name || composite.child_workflow_uuid || '子工作流'), revision: Number(composite.child_workflow_revision || 1), invocationUuid: String(node.uuid), x: Number(rawPose.x || 120 + index * 220), y: Number(rawPose.y || 180), requirements, deviceBindings: bindings, parameters, parameterDrafts: compositeParameterDrafts(parameters, node.param || {}) }
      }).filter((child) => child.contractUuid && child.invocationUuid)
      const inputFields = (operation.inputContract || []).map((field) => editableField(field, 'input'))
      const declaredNames = new Set(inputFields.map((field) => field.name.trim()))
      for (const action of actions) {
        for (const field of action.fields) {
          const binding = action.inputBindings[field.handleUuid]
          const parameter = binding && 'parameter' in binding ? binding.parameter.trim() : ''
          if (parameter && !declaredNames.has(parameter)) {
            inputFields.push(contractFieldForActionParameter(field, parameter))
            declaredNames.add(parameter)
          }
        }
      }
      setEditingTags(operation.tags); setEditingUuid(operation.uuid); setCreating(true); setSelectedOperation(null); setName(operation.name); setDescription(operation.description); setCategoryUuid(operation.operationCategoryUuid || ''); setInputContractFields(inputFields); setOutputContractFields((operation.outputContract || []).map((field) => editableField(field, 'output'))); setChildWorkflowRefs(children); setChildPickerOpen(false); setDraft(actions); setDraftControls(controls); setExpandedActionId(''); setExpandedControlId('')
    } catch (error) { onNotify(`读取实验操作失败：${error instanceof Error ? error.message : '未知错误'}`) }
  }
  /**
   * 加入一个设备动作（Action），同时读取它的输入和输出句柄供后续连线。
   * @param templateUuid 用户选择的设备动作模板 UUID。
   * @returns 模板句柄读取并加入页面草稿后结束。
   */
  async function addAction(templateUuid: string) {
    if (!connected) return
    const template = actionsQuery.data?.find((item) => item.uuid === templateUuid)
    if (!template) return
    const device = materials.find((material) => material.resourceTemplateUuid === template.resourceTemplate.uuid)
    try {
      const [fields, outputs] = await Promise.all([loadActionParameters(templateUuid), loadActionOutputs(templateUuid)])
      const id = crypto.randomUUID()
      const param = Object.fromEntries(fields.filter((field) => field.schema.default !== undefined).map((field) => [field.key, field.schema.default]))
      setDraft((current) => [...current, { id, templateUuid, nodeType: template.nodeType, materialUuid: device?.uuid || '', deviceId: device?.uuid || '', name: template.displayName, description: template.description || template.displayName, fields, outputs, param, inputBindings: {} }])
      setExpandedControlId('')
      setExpandedActionId(id)
    } catch (error) { onNotify(`读取设备动作参数失败：${error instanceof Error ? error.message : '未知错误'}`) }
  }

  /** 把设备包提供的条件或循环模板加入编辑画布，初始参数可直接在配置面板填写。 */
  function addControl(templateUuid: string) {
    const template = controlTemplatesQuery.data?.find((item) => item.uuid === templateUuid)
    if (!template) return
    const id = crypto.randomUUID()
    setDraftControls((current) => [...current, {
      id, templateUuid: template.uuid, nodeType: template.nodeType, name: template.displayName,
      description: template.description || template.displayName, param: defaultControlParam(template.nodeType),
      parameterSchema: template.parameterSchema,
    }])
    setExpandedControlId(id)
  }

  /** 接收结构化控制面板更新，所有成员关系都保留在对应节点的 param 中。 */
  function updateControlParam(id: string, param: Record<string, unknown>) {
    setDraftControls((current) => current.map((control) => control.id === id ? { ...control, param } : control))
  }
  /**
   * 更新设备动作（Action）输入来源；工作流参数登记到合同，上游输出仅登记数据边。
   * @param actionId 目标设备动作草稿身份。
   * @param field 目标输入句柄。
   * @param binding 新来源；undefined 表示恢复为固定值。
   * @returns 无返回值。
   */
  function updateActionBinding(actionId: string, field: ActionParameterRecord, binding: DraftInputBinding | undefined) {
    if (binding && 'parameter' in binding) ensureInputContractForBinding(binding.parameter, field)
    setDraft((current) => current.map((action) => {
      if (action.id !== actionId) return action
      const nextBindings = { ...action.inputBindings }
      if (binding) nextBindings[field.handleUuid] = binding
      else delete nextBindings[field.handleUuid]
      return { ...action, inputBindings: nextBindings }
    }))
  }
  /**
   * 更新设备动作（Action）输入的固定值；切换来源时用 undefined 清理旧值。
   * @param actionId 目标设备动作草稿身份。
   * @param field 目标输入句柄。
   * @param value 新固定值；undefined 表示清理旧值。
   * @returns 无返回值。
   */
  function updateActionLiteral(actionId: string, field: ActionParameterRecord, value: unknown) {
    setDraft((current) => current.map((action) => {
      if (action.id !== actionId) return action
      const param = value === undefined
        ? Object.fromEntries(Object.entries(action.param).filter(([key]) => key !== field.key))
        : { ...action.param, [field.key]: value }
      return { ...action, param }
    }))
    setParamDrafts((current) => {
      const next = { ...current }
      delete next[`${actionId}:${field.key}`]
      return next
    })
  }
  /**
   * 保存尚未通过 JSON 解析的对象或数组输入文本。
   * @param key 由设备动作草稿身份与字段名组成的文本键。
   * @param value 用户当前输入的原始文本。
   * @returns 无返回值。
   */
  function updateActionLiteralDraft(key: string, value: string) {
    setParamDrafts((current) => ({ ...current, [key]: value }))
  }
  /** 开关设备动作的人工确认包装；底层模板和设备绑定保持不变。 */
  function updateActionManualConfirmation(actionId: string, config: DraftAction['manualConfirmation']) {
    setDraft((current) => current.map((action) => action.id === actionId
      ? { ...action, manualConfirmation: config }
      : action))
  }
  /**
   * 调整设备动作（Action）顺序，并清除因重排变成反向引用的上游绑定。
   * @param index 当前设备动作在草稿中的位置。
   * @param direction 向前移动为 -1，向后移动为 1。
   * @returns 无返回值。
   */
  function moveAction(index: number, direction: -1 | 1) {
    const nextIndex = index + direction
    if (nextIndex < 0 || nextIndex >= draft.length) return
    setDraft((current) => {
      const next = [...current]
      ;[next[index], next[nextIndex]] = [next[nextIndex], next[index]]
      return removeInvalidUpstreamBindings(next)
    })
  }
  function moveChild(index: number, direction: -1 | 1) {
    const nextIndex = index + direction
    if (nextIndex < 0 || nextIndex >= childWorkflowRefs.length) return
    setChildWorkflowRefs((current) => {
      const next = [...current]
      ;[next[index], next[nextIndex]] = [next[nextIndex], next[index]]
      return next.map((child, childIndex) => ({ ...child, x: 120 + childIndex * 220 }))
    })
  }
  /**
   * 保存当前工作流图，并在图保存后补齐顶层 ready 顺序边。
   * @returns 保存、子工作流展开与列表刷新完成后结束。
   */
  async function submit() {
    if (!connected) return
    if (saveProblem) { onNotify(saveProblem); return }
    setSaving(true)
    let createdNewWorkflow = false
    let savedWorkflowUuid = editingUuid
    try {
      let inputContract: Record<string, unknown>; let outputContract: Record<string, unknown>
      try { inputContract = serialiseInputContract(inputContractFields); outputContract = serialiseOutputContract(outputContractFields) } catch { throw new Error('工作流参数默认值不是有效 JSON，请检查对象或数组参数') }
      let savedRevision = 1
      if (editingUuid) {
        await updateExperimentOperation({ workflowUuid: editingUuid, tags: editingTags, name: name.trim(), description: description.trim(), categoryUuid: categoryUuid || undefined, inputContract, outputContract, actions: draft.map(({ id, nodeUuid, templateUuid, nodeType, materialUuid, deviceId, name: actionName, description: actionDescription, param, inputBindings, manualConfirmation }) => ({ draftId: id, nodeUuid, templateUuid, nodeType, materialUuid, deviceId, name: actionName, description: actionDescription, param, inputBindings: persistInputBindings(inputBindings), manualConfirmation })), controls: draftControls.map(({ id, nodeUuid, templateUuid, nodeType, name: controlName, description: controlDescription, param }) => ({ draftId: id, nodeUuid, templateUuid, nodeType, name: controlName, description: controlDescription, param })) })
        const savedGraph = await loadWorkflowGraph(editingUuid)
        savedRevision = savedGraph.workflow.revision
        onNotify(`实验操作“${name}”已保存，状态已退回 source`)
      } else {
        const created = await createExperimentOperation({ name: name.trim(), description: description.trim(), categoryUuid: categoryUuid || undefined, inputContract, outputContract, actions: draft.map(({ id, templateUuid, nodeType, materialUuid, deviceId, name: actionName, description: actionDescription, param, inputBindings, manualConfirmation }) => ({ draftId: id, templateUuid, nodeType, materialUuid, deviceId, name: actionName, description: actionDescription, param, inputBindings: persistInputBindings(inputBindings), manualConfirmation })), controls: draftControls.map(({ id, templateUuid, nodeType, name: controlName, description: controlDescription, param }) => ({ draftId: id, templateUuid, nodeType, name: controlName, description: controlDescription, param })) })
        savedWorkflowUuid = created.workflowUuid
        createdNewWorkflow = true
        savedRevision = created.revision
        onNotify(`实验操作“${name}”已保存为 source，可确认后发布`)
      }
      for (const [childIndex, child] of childWorkflowRefs.entries()) {
        const childPose = { x: 120 + childIndex * 220, y: child.y }
        const childParam = serialiseCompositeParameters(child.parameters, child.parameterDrafts)
        const resolvedBindings = { ...child.deviceBindings }
        for (const requirement of child.requirements) {
          if (!resolvedBindings[requirement.key]) {
            const device = materials.find((material) => material.resourceTemplateUuid === requirement.resourceTemplateUuid)
            if (device) resolvedBindings[requirement.key] = device.uuid
          }
        }
        if (child.invocationUuid) {
          const patched = await patchWorkflowNode(child.invocationUuid, { pose: childPose, param: childParam })
          savedRevision = Number(patched?.workflow?.revision || patched?.revision || savedRevision + 1)
          continue
        }
        const invocationUuid = crypto.randomUUID()
        const inserted = await insertCompositeWorkflow({ parentWorkflowUuid: savedWorkflowUuid, revision: savedRevision, contractUuid: child.contractUuid, invocationUuid, deviceBindings: resolvedBindings, pose: childPose, param: childParam })
        child.invocationUuid = invocationUuid
        savedRevision = Number(inserted?.workflow?.revision || inserted?.revision || savedRevision + 1)
      }
      const childNodeUuids = childWorkflowRefs.map((child) => child.invocationUuid).filter((uuid): uuid is string => Boolean(uuid))
      const savedGraph = await loadWorkflowGraph(savedWorkflowUuid)
      const actionNodeUuids = [...savedGraph.nodes]
        .filter((node) => !node.parentUuid && !node.meta_data?.unilab?.composite && !isControlNode(node))
        .sort((left, right) => Number(left.meta_data?.unilab?.sequence_index ?? Number.MAX_SAFE_INTEGER) - Number(right.meta_data?.unilab?.sequence_index ?? Number.MAX_SAFE_INTEGER))
        .map((node) => String(node.uuid))
      // 结构控制节点的分支/循环依赖不由线性顺序边表达；存在控制节点时保留
      // 导入图的原有结构，避免把分支成员重新串成错误的 ready 链。
      if (!savedGraph.nodes.some((node) => isControlNode(node))) {
        await ensureWorkflowSequenceEdges(savedWorkflowUuid, [...childNodeUuids, ...actionNodeUuids])
      }
      setCreating(false); setDraft([]); setDraftControls([]); setExpandedActionId(''); setExpandedControlId('')
      await queryClient.invalidateQueries({ queryKey: ['experiment-operations'] })
      await queryClient.invalidateQueries({ queryKey: ['edge-snapshot'] })
    } catch (error) {
      // 创建流程是多步提交；子工作流/连线失败时回滚刚创建的父流程，
      // 防止列表残留一个没有节点的空实验操作。
      if (createdNewWorkflow && savedWorkflowUuid) {
        try { await deleteExperimentOperation(savedWorkflowUuid) } catch { /* 保留原始错误 */ }
      }
      onNotify(`保存失败：${error instanceof Error ? error.message : '未知错误'}`)
    } finally { setSaving(false) }
  }

  async function publish(operation: WorkflowDefinition) {
    if (!connected) return
    try {
      const result = await publishWorkflow(operation.uuid, operation.revision)
      const refresh = result.dependent_refresh
      const pending = Array.isArray(refresh?.pending) ? refresh.pending : []
      const updated = Array.isArray(refresh?.updated_workflow_uuids) ? refresh.updated_workflow_uuids : []
      let message = `实验操作“${operation.name}”已发布`
      if (updated.length) message += `；${updated.length} 个父工作流已更新`
      if (pending.length) {
        message += `；${pending.length} 个父工作流未更新，请处理后再运行：`
        message += pending.map((item: { workflow_uuid?: string; message?: string }) =>
          `${item.workflow_uuid || '未知工作流'}（${item.message || '请查看工作流诊断'}）`,
        ).join('；')
      }
      onNotify(message)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['experiment-operations'] }),
        queryClient.invalidateQueries({ queryKey: ['published-child-workflows'] }),
        queryClient.invalidateQueries({ queryKey: ['workflow-graph'] }),
        queryClient.invalidateQueries({ queryKey: ['edge-snapshot'] }),
      ])
    }
    catch (error) { onNotify(`发布失败：${error instanceof Error ? error.message : '未知错误'}`) }
  }

  async function remove(operation: WorkflowDefinition) {
    if (!connected) return
    if (!window.confirm(`确认删除实验操作“${operation.name}”？该操作不可恢复。`)) return
    try { await deleteExperimentOperation(operation.uuid); setSelectedOperation(null); onNotify(`已删除实验操作“${operation.name}”`); await queryClient.invalidateQueries({ queryKey: ['experiment-operations'] }) }
    catch (error) { onNotify(`删除失败：${error instanceof Error ? error.message : '未知错误'}`) }
  }

  const expandedAction = draft.find((action) => action.id === expandedActionId)

  async function importFile(file: File, kind: 'python' | 'json') {
    if (!connected) return
    try {
      const imported = kind === 'python' ? await importWorkflowPython(file) : await importWorkflowJson(file, 'experiment_operation')
      if (imported.workflowType !== 'experiment_operation') {
        onNotify(`导入失败：文件“${file.name}”不是实验操作类型，请声明 workflow_type=experiment_operation`)
        return
      }
      onNotify(`已导入实验操作“${imported.name}”（${imported.uuid}）`)
      await queryClient.invalidateQueries({ queryKey: ['experiment-operations'] })
      await queryClient.invalidateQueries({ queryKey: ['edge-snapshot'] })
    } catch (error) { onNotify(`导入失败：${error instanceof Error ? error.message : '未知错误'}`) }
  }

 return <div className="page operations-page" data-writable={connected}>
    <PageHeader eyebrow="EXPERIMENT OPERATIONS" title="实验室操作" description="实验操作可包含设备 Action、条件和循环节点，也可被普通工作流引用为子工作流。" actions={creating ? <Button icon={<ChevronLeft size={15} />} onClick={() => setCreating(false)}>返回实验操作</Button> : <><input ref={pythonImportRef} hidden type="file" accept=".py,text/x-python" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void importFile(file, 'python') }} /><input ref={jsonImportRef} hidden type="file" accept=".json,application/json" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void importFile(file, 'json') }} /><Button disabled={!connected} icon={<FileInput size={15} />} onClick={() => pythonImportRef.current?.click()}>导入 Python</Button><Button disabled={!connected} icon={<FileJson size={15} />} onClick={() => jsonImportRef.current?.click()}>导入 JSON</Button><Button tone="primary" icon={<Sparkles size={16} />} disabled={!connected} onClick={beginCreate}>创建实验操作</Button></>} />
    <div className={`operation-authoring-layout ${creating ? 'is-creating' : ''}`}>
      <Panel className="device-action-browser"><PanelHeader title="设备模板" description={`${deviceGroups.length} 个设备 · ${actionsQuery.data?.length || 0} 个 Action`} />
        <label className="search-field"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索当前设备的 Action" /></label>
        <div className="device-template-list">{deviceGroups.map((device) => <button key={device.uuid} className={selectedDevice?.uuid === device.uuid ? 'active' : ''} onClick={() => { setSelectedDeviceUuid(device.uuid); setQuery('') }}><span><FlaskConical size={15} /></span><div><strong>{device.name}</strong><small>{device.actions.length} 个 Action</small></div><ChevronRight size={13} /></button>)}</div>
        {selectedDevice ? <section className="device-actions"><header><strong>{selectedDevice.name}</strong><span>{visibleActions.length} 个 Action</span></header>{visibleActions.map((action) => <button key={action.uuid} disabled={!connected || !creating} onClick={() => creating ? void addAction(action.uuid) : undefined}><div><strong>{action.displayName}</strong><code>{action.name}</code></div>{creating ? <Plus size={14} /> : null}</button>)}</section> : null}
        <section className="device-actions control-template-list"><header><strong>工作流控制节点</strong><span>{controlTemplatesQuery.data?.length || 0} 个</span></header>{controlTemplatesQuery.data?.map((control) => <div className="control-template-card" key={control.uuid}><div><strong>{control.displayName}</strong><code>{control.nodeType}</code><small>{control.description || '调度器在运行时处理，不占用设备执行器。'}</small></div>{creating ? <button type="button" disabled={!connected} aria-label={`加入画布 ${control.displayName}`} onClick={() => addControl(control.uuid)}><Plus size={13} /> 加入画布</button> : <span>创建实验操作后可加入</span>}</div>)}{!controlTemplatesQuery.data?.length ? <small className="control-template-empty">当前设备包未提供条件或循环节点。</small> : null}</section>
      </Panel>
      {creating ? <Panel className="operation-builder"><div className="builder-heading"><div><span>EXPERIMENT OPERATION EDITOR</span><h2>{editingUuid ? '编辑实验操作' : '创建实验操作'}</h2>{editingUuid ? <code>{editingUuid}</code> : null}<p>{editingUuid ? '保存修改后状态退回 source，需要重新发布。' : '选择设备 Action、条件分支或循环体节点，组成可复用实验操作。控制节点由 Scheduler 本地执行。'}</p></div><div className="builder-heading-actions"><strong>{draft.length + draftControls.length + childWorkflowRefs.length} 个节点</strong><Button tone="primary" icon={<Save size={15} />} disabled={!connected || saving} title={saveProblem || undefined} onClick={() => void submit()}>{saving ? '正在保存…' : '保存实验操作'}</Button></div></div>
        <div className="operation-meta"><label><span>操作名称</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：S09 移液并混匀" /></label><label><span>操作类别</span><select value={categoryUuid} onChange={(event) => setCategoryUuid(event.target.value)}><option value="">未分类</option>{categoriesQuery.data?.map((category) => <option key={category.uuid} value={category.uuid}>{category.name}</option>)}</select></label><label className="wide"><span>说明</span><input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="描述该实验操作的目的和约束" /></label></div>
        <div className="save-requirements" data-ready={!saveProblem || undefined}>{saveProblem || `配置完整 · ${workflowParameters.length} 个工作流参数`}</div>
        <section className="workflow-contract-editor"><header><div><strong>工作流参数</strong><span>对外暴露给父工作流的输入和输出合同</span></div><span className="contract-editor-hint">参数名称需唯一；保存时由 OS 做最终校验</span></header><div className="workflow-contract-editors"><ContractEditor kind="input" fields={inputContractFields} setFields={setInputContractFields} /><ContractEditor kind="output" fields={outputContractFields} setFields={setOutputContractFields} /></div></section>
        <section className="child-reference-section"><header><div><strong>子工作流引用</strong><small>仅可引用已发布的实验操作；同一实验操作可作为多个独立调用加入。</small></div><Button icon={<Workflow size={14} />} onClick={() => setChildPickerOpen((open) => !open)}>引用已发布子工作流</Button></header>{childPickerOpen ? <div className="child-reference-picker">{publishedChildrenQuery.isFetching ? <p>正在读取已发布实验操作…</p> : publishedChildrenQuery.data?.length ? publishedChildrenQuery.data.map((contract) => { const contractUuid = String(contract.uuid || ''); const invocationCount = childWorkflowRefs.filter((child) => child.contractUuid === contractUuid).length; return <button type="button" key={contractUuid} onClick={() => { const requirements = Array.isArray(contract.executor_requirements) ? contract.executor_requirements.map((item: Record<string, unknown>) => ({ key: String(item.key || ''), resourceTemplateUuid: String(item.resource_template_uuid || ''), displayName: String(item.display_name || item.key || '执行设备') })).filter((item: ChildWorkflowRequirement) => item.key) : []; const bindings = Object.fromEntries(requirements.map((requirement) => { const device = materials.find((material) => material.resourceTemplateUuid === requirement.resourceTemplateUuid && material.sourceNodeId); return [requirement.key, device?.uuid || ''] })); const parameters = compositeContractFields(contract.input_contract?.parameters); setChildWorkflowRefs((current) => [...current, { id: crypto.randomUUID(), contractUuid, workflowUuid: String(contract.workflow_uuid || ''), name: String(contract.name || contract.workflow_uuid), revision: Number(contract.workflow_revision || contract.version || 1), x: 120 + current.length * 220, y: 180, requirements, deviceBindings: bindings, parameters, parameterDrafts: {} }]); setChildPickerOpen(false) }}><Workflow size={14} /><span><strong>{String(contract.name || contract.workflow_uuid)}</strong><small>{String(contract.workflow_uuid || '')} · r{String(contract.workflow_revision || contract.version || '')} · {Array.isArray(contract.input_contract?.parameters) ? contract.input_contract.parameters.length : 0} 个输入 · {Array.isArray(contract.executor_requirements) ? contract.executor_requirements.length : 0} 个执行设备</small></span><em>{invocationCount ? `再添加（已有 ${invocationCount} 次）` : '添加'}</em></button> }) : <p>暂无可引用的已发布实验操作。</p>}</div> : null}{childWorkflowRefs.length ? <div className="child-reference-list">{childWorkflowRefs.map((child, childIndex) => <div key={child.id}><Workflow size={13} /><span><strong>{child.name}</strong><small>{child.workflowUuid} · r{child.revision} · 调用 {childIndex + 1}</small>{child.requirements.map((requirement) => <label className="child-binding" key={requirement.key}><span>{requirement.displayName}</span><select aria-label={`${child.name} 调用 ${childIndex + 1} ${requirement.displayName}`} value={child.deviceBindings[requirement.key] || ''} onChange={(event) => setChildWorkflowRefs((current) => current.map((item) => item.id === child.id ? { ...item, deviceBindings: { ...item.deviceBindings, [requirement.key]: event.target.value } } : item))}><option value="">选择设备实例</option>{materials.filter((material) => material.resourceTemplateUuid === requirement.resourceTemplateUuid && material.sourceNodeId).map((material) => <option key={material.uuid} value={material.uuid}>{material.name}</option>)}</select></label>)}<CompositeWorkflowParameterEditor operationName={child.name} invocationIndex={childIndex + 1} fields={child.parameters} drafts={child.parameterDrafts} onChange={(parameterName, text) => setChildWorkflowRefs((current) => current.map((item) => item.id === child.id ? { ...item, parameterDrafts: { ...item.parameterDrafts, [parameterName]: text } } : item))} /></span><div className="child-reference-controls"><button type="button" disabled={childIndex === 0} aria-label={`子工作流 ${child.name} 调用 ${childIndex + 1} 上移`} onClick={() => setChildWorkflowRefs((current) => { const next = [...current]; [next[childIndex - 1], next[childIndex]] = [next[childIndex], next[childIndex - 1]]; return next })}>↑</button><button type="button" disabled={childIndex === childWorkflowRefs.length - 1} aria-label={`子工作流 ${child.name} 调用 ${childIndex + 1} 下移`} onClick={() => setChildWorkflowRefs((current) => { const next = [...current]; [next[childIndex], next[childIndex + 1]] = [next[childIndex + 1], next[childIndex]]; return next })}>↓</button><button type="button" aria-label={`移除子工作流 ${child.name} 调用 ${childIndex + 1}`} onClick={() => setChildWorkflowRefs((current) => current.filter((item) => item.id !== child.id))}>×</button></div></div>)}</div> : null}</section>
        {workflowParameters.length ? <div className="workflow-parameter-summary"><strong>节点绑定的工作流参数</strong>{workflowParameters.map((parameter) => <code key={parameter}>{parameter}</code>)}</div> : null}
        <ControlParameterEditor controls={draftControls} nodes={builderNodes} workflowParameters={inputContractFields.map((field) => field.name.trim()).filter(Boolean)} expandedId={expandedControlId} onToggle={(id) => { setExpandedActionId(''); setExpandedControlId(expandedControlId === id ? '' : id) }} onParamChange={updateControlParam} />
        <div className="operation-canvas" aria-label="实验操作节点画布">{childWorkflowRefs.map((child, index) => <div className="operation-canvas-item" key={child.id}><article className="operation-canvas-node child-workflow-node"><header><span>{String(index + 1).padStart(2, '0')}</span><Workflow size={13} /><div className="step-controls"><button aria-label={`上移子工作流 ${child.name} 调用 ${index + 1}`} disabled={index === 0} onClick={() => moveChild(index, -1)}>←</button><button aria-label={`下移子工作流 ${child.name} 调用 ${index + 1}`} disabled={index === childWorkflowRefs.length - 1} onClick={() => moveChild(index, 1)}>→</button><button aria-label={`移除子工作流 ${child.name} 调用 ${index + 1}`} onClick={() => setChildWorkflowRefs((current) => current.filter((item) => item.id !== child.id))}>×</button></div></header><div className="node-body"><Workflow size={17} /><strong>{child.name}</strong><small>已发布实验操作 · 子工作流调用 {index + 1}</small><span>{child.requirements.length} 个执行器 · 输入/输出已暴露</span></div><span className="node-port">子工作流 ready</span></article>{index < childWorkflowRefs.length + draft.length - 1 ? <span className="canvas-edge"><ArrowRight size={16} /><small>ready</small></span> : null}</div>)}{draft.map((item, index) => { const template = actionsQuery.data?.find((action) => action.uuid === item.templateUuid); const devices = materials.filter((material) => material.resourceTemplateUuid === template?.resourceTemplate.uuid && material.sourceNodeId); const requiredCount = item.fields.filter((field) => field.required).length; const position = childWorkflowRefs.length + index; return <div className="operation-canvas-item" key={item.id}><article className={`operation-canvas-node ${expandedActionId === item.id ? 'selected' : ''}`}><header><span>{String(position + 1).padStart(2, '0')}</span><GripVertical size={13} /><div className="step-controls"><button aria-label={`上移 ${item.name}`} disabled={index === 0} onClick={() => moveAction(index, -1)}>←</button><button aria-label={`下移 ${index} ${item.name}`} disabled={index === draft.length - 1} onClick={() => moveAction(index, 1)}>→</button><button aria-label={`删除节点 ${item.name}`} onClick={() => setDraft((current) => current.filter((action) => action.id !== item.id))}>×</button></div></header><button className="node-body" onClick={() => { setExpandedControlId(''); setExpandedActionId(item.id) }}><FlaskConical size={17} /><strong>{item.name}</strong><small>{template?.resourceTemplate.displayName || '未知设备模板'}</small><span>{requiredCount} 必填 · {item.fields.length - requiredCount} 选填</span></button><select aria-label={`设备实例 ${item.name}`} value={item.materialUuid} onChange={(event) => { const device = devices.find((candidate) => candidate.uuid === event.target.value); setDraft((current) => current.map((action) => action.id === item.id ? { ...action, materialUuid: device?.uuid || '', deviceId: device?.sourceNodeId || '' } : action)) }}><option value="">选择设备实例</option>{devices.map((device) => <option value={device.uuid} key={device.uuid}>{device.name}</option>)}</select><button className="node-port" aria-label={`配置参数 ${item.name}`} onClick={() => { setExpandedControlId(''); setExpandedActionId(item.id) }}>参数</button></article>{position < childWorkflowRefs.length + draft.length - 1 ? <span className="canvas-edge"><ArrowRight size={16} /><small>ready</small></span> : null}</div> })}{draftControls.map((control, index) => { const position = childWorkflowRefs.length + draft.length + index; return <div className="operation-canvas-item" key={control.id}><article className={`operation-canvas-node control-canvas-node ${expandedControlId === control.id ? 'selected' : ''}`}><header><span>{String(position + 1).padStart(2, '0')}</span><Workflow size={13} /><div className="step-controls"><button aria-label={`配置控制节点 ${control.name}`} onClick={() => { setExpandedActionId(''); setExpandedControlId(control.id) }}>⚙</button><button aria-label={`移除控制节点 ${control.name}`} onClick={() => { setDraftControls((current) => current.filter((item) => item.id !== control.id)); setExpandedControlId((current) => current === control.id ? '' : current) }}>×</button></div></header><button type="button" className="node-body" onClick={() => { setExpandedActionId(''); setExpandedControlId(control.id) }}><Workflow size={17} /><strong>{control.name}</strong><small>{control.nodeType}</small><span>{controlStructureIncomplete(control) ? '待配置分支/循环体' : '结构参数已配置'}</span></button><button type="button" className="node-port" aria-label={`编辑控制节点 ${control.name}`} onClick={() => { setExpandedActionId(''); setExpandedControlId(control.id) }}>配置结构</button></article></div> })}{!draft.length && !draftControls.length && !childWorkflowRefs.length ? <EmptyState title="尚未添加工作流节点" description="从左侧选择设备或控制节点，再点击加入实验操作。" /> : null}</div>
        {expandedAction ? <ActionParameterEditor action={expandedAction} actions={draft} paramDrafts={paramDrafts} workflowParameters={inputContractFields.map((field) => field.name.trim()).filter(Boolean)} onClose={() => setExpandedActionId('')} onBindingChange={(field, binding) => updateActionBinding(expandedAction.id, field, binding)} onLiteralChange={(field, value) => updateActionLiteral(expandedAction.id, field, value)} onLiteralDraftChange={updateActionLiteralDraft} onManualConfirmationChange={(config) => updateActionManualConfirmation(expandedAction.id, config)} onNotify={onNotify} /> : null}
      </Panel> : <Panel className="operation-library operation-home"><PanelHeader title="实验操作" description={`${libraryOperations.filter((operation) => !operation.tags.includes('operation:internal')).length} 个顶层实验室操作 · ${libraryOperations.filter((operation) => operation.tags.includes('operation:internal')).length} 个子操作`} action={<Workflow size={17} />} />
        <div className="operation-library-filters">
          <div className="operation-scope" role="group" aria-label="操作范围">{([['top', '顶层操作'], ['internal', '子操作'], ['all', '全部操作']] as const).map(([scope, label]) => <button key={scope} type="button" aria-pressed={operationScope === scope} onClick={() => { setOperationScope(scope); setOperationFolder(''); setSelectedOperation(null) }}>{label}</button>)}</div>
          <div className="operation-library-search"><label>文件夹<select aria-label="操作文件夹" value={operationFolder} onChange={(event) => setOperationFolder(event.target.value)}><option value="">全部文件夹</option>{operationFolders.map((folder) => <option key={folder} value={folder}>{folder}</option>)}<option value="__untagged__">未标记</option></select></label><label>搜索<input aria-label="搜索操作" placeholder="名称、UUID 或标签" value={operationSearch} onChange={(event) => setOperationSearch(event.target.value)} /></label></div>
          <small aria-live="polite">{operationsQuery.isFetching ? '正在读取操作目录…' : `显示 ${visibleOperations.length} / ${scopedOperations.length} 个操作`}</small>
          {operationsQuery.isError ? <p role="alert">操作目录加载失败，请重试。<button onClick={() => void operationsQuery.refetch()}>重试</button></p> : null}
        </div><div className="operation-list">{visibleOperations.map((operation) => <article key={operation.uuid} className={selectedOperation?.uuid === operation.uuid ? 'selected' : ''}><span><Workflow size={16} /></span><div><strong>{operation.name}</strong><small>{operation.tags.includes('operation:internal') ? '子操作' : '顶层操作'} · r{operation.revision} · {operation.status === 'published' ? '已发布' : 'source'}</small><code>{operation.uuid}</code><small>{operation.tags.filter((tag) => tag.startsWith('folder:')).map((tag) => tag.slice(7)).join(' · ')}</small></div><div className="operation-row-actions"><button title="查看" aria-label={`查看 ${operation.name} ${operation.uuid}`} onClick={() => void viewOperation(operation)}><Eye size={13} /></button><button title="编辑" aria-label={`编辑 ${operation.name} ${operation.uuid}`} onClick={() => void beginEdit(operation)}><Pencil size={13} /></button>{operation.status !== 'published' ? <button title="发布" aria-label={`发布 ${operation.name} ${operation.uuid}`} onClick={() => void publish(operation)}><Send size={13} /></button> : null}<button className="danger" title="删除" aria-label={`删除 ${operation.name} ${operation.uuid}`} onClick={() => void remove(operation)}><Trash2 size={13} /></button></div></article>)}{!visibleOperations.length && !operationsQuery.isFetching && !operationsQuery.isError ? <EmptyState title="没有匹配的操作" description="切换操作范围、文件夹或修改搜索条件。" /> : null}</div>{selectedOperation ? <aside className="operation-detail"><header><div><small>实验操作详情</small><h3>{selectedOperation.name}</h3><code>{selectedOperation.uuid}</code></div><button aria-label="关闭详情" onClick={() => setSelectedOperation(null)}>×</button></header><dl><div><dt>状态</dt><dd>{selectedOperation.status}</dd></div><div><dt>修订</dt><dd>r{selectedOperation.revision}</dd></div><div><dt>本层步骤</dt><dd>{selectedOperation.nodeCount}</dd></div><div><dt>文件夹</dt><dd>{selectedOperation.tags.filter((tag) => tag.startsWith('folder:')).map((tag) => tag.slice(7)).join(' · ') || '未标记'}</dd></div><div><dt>类别</dt><dd>{selectedOperation.operationCategoryUuid || '未分类'}</dd></div></dl><p>{selectedOperation.description || '暂无说明'}</p><section className="operation-detail-nodes"><strong>本层步骤明细</strong>{expandedNodeCount > selectedOperation.nodeCount ? <p>包含子操作及所有分支共 {expandedNodeCount} 个节点，不代表一次运行全部执行。</p> : null}{selectedOperationNodes.map((node, index) => <div key={node.uuid}><span>{String(index + 1).padStart(2, '0')}</span><p><strong>{node.name}</strong><code>{node.uuid}</code><small>{node.bindingLabel}</small>{node.childUuid ? <button onClick={() => void viewOperation({ ...selectedOperation, uuid: node.childUuid!, name: node.name })}>查看子操作</button> : null}</p></div>)}</section><footer><Button icon={<Pencil size={13} />} onClick={() => void beginEdit(selectedOperation)}>编辑</Button>{selectedOperation.status !== 'published' ? <Button tone="primary" icon={<Send size={13} />} onClick={() => void publish(selectedOperation)}>发布</Button> : null}<Button icon={<Trash2 size={13} />} onClick={() => void remove(selectedOperation)}>删除</Button></footer></aside> : null}</Panel>}
    </div>
  </div>
}
