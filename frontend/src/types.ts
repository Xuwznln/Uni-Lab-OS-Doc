export type PageId = 'overview' | 'materials' | 'devices' | 'reagents' | 'operations' | 'workflows' | 'tasks'

export type ConnectionMode = 'loading' | 'connected' | 'reconnecting' | 'demo' | 'error'

export interface RuntimeCapabilities {
  canEditDefinitions: boolean
  canViewDrafts: boolean
  canRunUnpublished: boolean
  canDebug: boolean
  canExecuteDeviceActions: boolean
}

export const DEFAULT_RUNTIME_CAPABILITIES = {
  canEditDefinitions: true,
  canViewDrafts: true,
  canRunUnpublished: true,
  canDebug: true,
  canExecuteDeviceActions: true,
} satisfies RuntimeCapabilities

/** 只用于界面展示；不会作为 Shared Interface 的 Workflow Task 状态回写。 */
export type TaskPresentationStatus =
  | 'running'
  | 'admission_blocked'
  | 'succeeded'
  | 'failed'
  | 'pending'
  | 'paused'
  | 'canceling'
  | 'canceled'
  | 'timeout'
  | 'intervention_required'
  | 'execution_unknown'
  | 'unknown'

/** 由冻结执行节点和权威 Job 状态组合出的矩阵展示状态。 */
export type NodePresentationStatus =
  | 'succeeded'
  | 'running'
  | 'waiting'
  | 'failed'
  | 'pending'
  | 'skipped'
  | 'canceling'
  | 'canceled'
  | 'attention'

/** 已归类为界面语言的节点等待原因，避免视图依赖 Edge 内部锁键或状态码。 */
export interface TaskNodeWaitReason {
  code: string
  title: string
  message: string
  details: string[]
  waitingSince?: string
}

export interface WorkflowIntervention {
  uuid: string
  workflowTaskUuid: string
  workflowNodeJobUuid: string
  revision: number
  status: string
  options: { id: string; action: string; label: string; description?: string }[]
  metaData: Record<string, unknown>
  openedAt: string
}

export interface WorkflowDefinition {
  uuid: string
  name: string
  revision: number
  status: string
  description: string
  nodeCount: number
  tags: string[]
  inputContract: ContractField[]
  outputContract: ContractField[]
  sourcePath?: string
  workflowType: 'normal' | 'experiment_operation'
  operationCategoryUuid?: string
}

export interface WorkflowTarget {
  workflowUuid: string
  revision?: number
  taskUuid?: string
}

export type WorkflowAuthoringState =
  | 'applied'
  | 'applied_source_stale'
  | 'candidate_stale'
  | 'draft_invalid'
  | 'draft_missing'
  | 'unapplied_graph'
  | 'unapplied_source_only'
  | 'unknown'

export interface WorkflowSource {
  workflowRevision: number
  state: WorkflowAuthoringState
  sourceUri: string
  pythonSource: string
}

export interface WorkflowGraphNode {
  uuid: string
  name: string
  type: string
  kind: 'group' | 'material_source' | 'material_io' | 'condition' | 'repeat_until' | 'action'
  action_name?: string
  workflow_node_template_uuid?: string
  material_uuid?: string
  param?: Record<string, any>
  manual_confirmation?: Record<string, any>
  pose?: Record<string, any>
  meta_data?: Record<string, any>
  parentUuid?: string
  deviceId?: string
  authoringOrder?: number
  authoringResultName?: string
  parallelScope?: string
  materialRole?: string
  description?: string
  disabled: boolean
}

export interface WorkflowGraphEdge {
  uuid: string
  sourceNodeUuid: string
  targetNodeUuid: string
  /** 数据边使用的源/目标句柄；ready 顺序边也保留这两个字段。 */
  sourceHandleUuid?: string
  targetHandleUuid?: string
  metaData?: Record<string, any>
}

/** 工作流定义里不绑定具体库存实例的逻辑数量需求；建任务时必须逐条绑定并预留。 */
export interface WorkflowInventoryRequirement {
  uuid: string
  requirementKey: string
  consumeNodeUuid: string
  targetType: 'reagent_info' | 'current_substance' | string
  reagentInfoUuid?: string
  requiredQuantity: number
  quantityUnit: string
  allowSplit: boolean
  description?: string
  materialSourceNodeUuid?: string
}

export interface WorkflowGraph {
  workflow: WorkflowDefinition
  nodes: WorkflowGraphNode[]
  edges: WorkflowGraphEdge[]
  /** 编译自 material_source(quantity=…, quantity_unit=…) 的库存需求。 */
  inventoryRequirements?: WorkflowInventoryRequirement[]
  /** 完整图返回的模板快照，包含发布子工作流的合成节点句柄。 */
  nodeTemplates?: Array<Record<string, any>>
  handleTemplates?: Array<Record<string, any>>
}

export interface ContractField {
  name: string
  type: string
  required?: boolean
  defaultValue?: unknown
  title?: string
  description?: string
  implicit?: boolean
  schema: Record<string, unknown>
}

export interface TaskNodeJobEvidence {
  uuid: string
  executionSource?: string
  executionStatus?: string
  uncertaintyReason?: string
  reviewVersion?: string
  resultReviewed?: boolean
  manualReview?: {
    reviewVersion: string
    operatorId: string
    reviewedAt: string
    reason: string
  }
  attempt?: number
  param: unknown
  feedbackData: unknown
  returnInfo: unknown
  errorInfo: unknown[]
  startedAt?: string
  finishedAt?: string
  manualConfirmation?: {
    status: 'pending' | 'approved' | 'rejected' | 'timed_out' | 'canceled'
    deadlineAt: string
    actions: Array<'approve' | 'reject'>
  }
}

export interface TaskNode {
  uuid: string
  name: string
  /** 稳定的设备动作身份；展示名称可能被本地化，配色应使用此字段。 */
  actionName?: string
  kind: string
  index: number
  status: NodePresentationStatus
  device?: string
  materialUuid?: string
  waitReason?: TaskNodeWaitReason
  job?: TaskNodeJobEvidence
  manualReviewJobs?: TaskNodeJobEvidence[]
}

export type WorkflowTaskPriority = 'normal' | 'high'
export type WorkflowTaskPresentationPriority = WorkflowTaskPriority | 'urgent' | 'low' | 'unknown' | number

export interface WorkflowTask {
  uuid: string
  workflowUuid: string
  workflowName: string
  status: TaskPresentationStatus
  /** 原始任务终态，独立于界面的“需要干预”等提示状态。 */
  executionStatus?: string
  /** 后端持久化的任务清理状态，用于展示终态任务的残留处置入口。 */
  cleanupStatus?: string
  /** Edge 依据终态、清理状态和全部资源事实计算出的删除资格。 */
  canDelete?: boolean
  /** 删除资格检查未通过时由 Edge 返回的阻塞原因。 */
  deleteBlockers?: string[]
  /** 后端记录的任务级注意原因。 */
  attentionReason?: string
  /** 已完成人工结果核对的任务不再显示同一类清理入口。 */
  manualReviewAttentionResolved?: boolean
  priority: WorkflowTaskPresentationPriority
  sample: string
  description: string
  current: string
  progress: number
  updatedAt: string
  nodes: TaskNode[]
  materialUuids: string[]
  workflowRevision?: number
  runMode: string
  executionMode: 'normal' | 'switching_to_step' | 'step'
  dispatchGate: 'active' | 'paused'
  controlStatus: string
  /** 创建任务时冻结的公开输入；暂停任务编辑仅作用于尚未下发的后缀。 */
  input?: Record<string, unknown>
  inputContract?: ContractField[]
  matrixGroupKey: string
  trace?: {
    traceId?: string
    url: string
    mode: 'trace' | 'search'
  }
}

/** 工作流任务详情页的持久执行锁租约；释放资格由 Edge 后端权威判定。 */
export interface WorkflowTaskExecutionLock {
  uuid: string
  workflowTaskUuid: string
  workflowNodeJobUuid: string
  lockKey: string
  scope: string
  materialUuid?: string
  siteUuid?: string
  state: string
  claimUuid: string
  fencingToken: number
  jobStatus: string
  claimState: string
  canRelease: boolean
  releaseBlockReason?: string
}

export interface WorkflowTaskExecutionLockSnapshot {
  workflowTaskUuid: string
  taskStatus: string
  locks: WorkflowTaskExecutionLock[]
  activeDeviceTenancyCount: number
}

export interface WorkflowTaskExecutionLockReleaseRequest {
  expectedClaimUuid: string
  expectedFencingToken: number
  reason: string
  physicalSettlementConfirmed: boolean
}

export interface WorkflowTaskExecutionLockReleaseResult {
  status: 'released' | 'already_released' | string
  releasedLockUuids: string[]
  action?: {
    uuid?: string
    result?: string
    reason?: string
    createTime?: string
  }
}

/** 失败物料转运等待人工核验的权威上下文。 */
export interface FailedMaterialTransferSettlementContext {
  jobUuid: string
  materialUuid: string
  sourceSiteUuid: string
  targetSiteUuid: string
}

/** 操作员确认的物料转运实际位置。 */
export interface FailedMaterialTransferSettlementRequest {
  actualChangeSet: {
    kind: 'material_transfer'
    material_uuid: string
    target_owner_material_uuid: string
    target_site_uuid: string
  }
  reason: string
}

export interface MaterialTaskReference {
  taskUuid: string
  taskStatus: TaskPresentationStatus
  workflowName: string
  sample: string
}

export type MaterialCurrentLocation =
  | {
      kind: 'site'
      label: string
      siteUuid: string
      ownerMaterialUuid: string
    }
  | { kind: 'unassigned'; label: string }
  | { kind: 'structural'; label: string; siteCount: number }
  | { kind: 'unresolved'; label: string; siteUuid?: string }

export interface CapacityLimits {
  max_volume_ul?: number
  max_mass_g?: number
}

export interface MaterialRecord {
  config?: Record<string, unknown>
  capacity?: CapacityLimits
  ratedCapacity?: CapacityLimits
  uuid: string
  name: string
  category: string
  currentLocation: MaterialCurrentLocation
  configuredSource: string
  sourceNodeId?: string
  taskReferences: MaterialTaskReference[]
  barcode: string
  parentUuid?: string
  className: string
  resourceTemplateUuid?: string
  /** 资源模板显示名（快照自带时填充；列表接口需另查模板表）。 */
  templateName?: string
  sourceGraph?: string
  updatedAt: string
  isStructural: boolean
  siteCount: number
  sites: Array<{
    uuid: string
    name: string
    occupiedMaterialUuid?: string
    occupiedMaterialName?: string
    allowedResourceTemplateUuids?: string[]
  }>
  revision: number
  position: [number, number, number]
  size: [number, number, number]
}

export interface ResourceTemplateRecord {
  uuid: string
  name: string
  displayName: string
  description: string
  resourceType: string
  /** 模板标签；含 "container" 表示可承载试剂 / 样品 / 当前物质。 */
  tags?: string[]
  availableSites: Array<{ name: string; label: string }>
}

export interface ActionTemplateRecord {
  uuid: string
  name: string
  displayName: string
  description?: string
  type: string
  nodeType: string
  resourceTemplate: { uuid: string; name: string; displayName: string }
}

/** JSON Schema 子集，用于把设备动作输入渲染为可编辑表单。 */
export interface ActionSchema {
  type?: string | string[]
  title?: string
  description?: string
  properties?: Record<string, ActionSchema>
  required?: string[]
  enum?: unknown[]
  default?: unknown
  minimum?: number
  maximum?: number
  items?: ActionSchema
  anyOf?: ActionSchema[]
  [key: string]: unknown
}

/** Edge 当前可见的设备实例及其运行时动作能力。 */
export interface DeviceRuntimeRecord {
  localId: string
  materialUuid?: string
  name: string
  online: boolean
  dispatchable: boolean
  source: 'backend' | 'local_runtime'
  actions: Array<{
    name: string
    displayName: string
    /** 设备包 @action(description=...) 的业务说明；缺省时为空串。 */
    description: string
    type: string
    riskLevel: string
    busy: boolean
  }>
}

/** 单点动作详情；字段名兼容通用 Schema 参数编辑器。 */
export interface DeviceActionDefinition {
  action_id: string
  action_version: string
  name: string
  display_name: string
  description?: string
  parameter_schema: ActionSchema
  defaults: Record<string, unknown>
  device_binding: { material_uuid: string; edge_local_id: string }
}

/** 一次设备单点动作的稳定创建意图；结果未知时必须复用同一幂等键。 */
export interface DeviceActionRunIntent {
  materialUuid: string
  workflowNodeTemplateUuid: string
  param: Record<string, unknown>
  executionTimeoutSeconds: number
  idempotencyKey: string
  description: string
}

export interface DeviceActionRunReference {
  taskUuid: string
  jobUuid: string
  created: boolean
  taskStatus: string
  jobStatus: string
}

/** 单点动作页面轮询的任务与作业权威状态。 */
export interface DeviceActionRunStatus {
  taskUuid: string
  jobUuid: string
  taskStatus: string
  controlStatus: string
  jobStatus: string
  actionName: string
  param: Record<string, unknown>
  feedback: unknown
  result: unknown
  errors: unknown[]
  waitReason?: unknown
  startedAt?: string
  finishedAt?: string
  updatedAt?: string
}

/** 调度器结构控制节点模板；它没有设备句柄，参数保存在工作流节点 param 中。 */
export interface ControlTemplateRecord extends ActionTemplateRecord {
  parameterSchema: Record<string, unknown>
}

export interface ActionParameterRecord {
  handleUuid: string
  key: string
  displayName: string
  required: boolean
  schema: Record<string, unknown>
}

/** 设备动作（Action）的数据输出句柄；ready 等流程控制句柄不包含在内。 */
export interface ActionOutputRecord {
  handleUuid: string
  key: string
  displayName: string
  schema: Record<string, unknown>
}

export interface OperationCategoryRecord {
  uuid: string
  name: string
  sortOrder: number
}

export interface ReagentInfoRecord {
  uuid: string
  name: string
  nameEn?: string
  aliases: string[]
  cas?: string
  molecularFormula?: string
  smiles?: string
  inchiKey?: string
  molecularWeight?: number
  densityGPerMl?: number
  physicalState: 'solid' | 'liquid' | 'gas' | 'other' | 'unknown'
  description?: string
  metadata?: Record<string, unknown>
  createdAt?: string
  updatedAt: string
}

export interface CompoundLookupResult {
  cas: string
  status: 'ok' | 'registered' | 'not_found' | 'unavailable'
  message?: string
  compound?: {
    name?: string
    molecularFormula?: string
    smiles?: string
    inchiKey?: string
    molecularWeight?: number
  }
}

export interface ReagentRecord {
  configuredCapacity?: CapacityLimits
  maximumCapacity?: CapacityLimits
  ratedCapacity?: CapacityLimits
  materialRevision?: number
  containerCapacity?: CapacityLimits
  loadingLimits?: CapacityLimits
  uuid: string
  materialUuid: string
  reagentInfoUuid: string
  name: string
  nameEn?: string
  aliases?: string[]
  cas?: string
  molecularFormula?: string
  smiles?: string
  inchiKey?: string
  molecularWeight?: number
  physicalState: string
  quantity?: number
  quantityUnit?: string
  concentrationValue?: number
  concentrationUnit?: string
  densityGPerMl?: number
  /** 入库时保存的密度来源，与目录当前参考密度分开。 */
  densitySource?: string
  containerName?: string
  containerBarcode?: string
  /** 未结束任务对该瓶的活动预留量，与 quantity 同单位。 */
  activeWorkflowReservedQuantity?: number
  description?: string
  /** 扩展元数据；后端合并未指定键，保留已有分装血缘。 */
  metaData?: Record<string, unknown>
  /** 仅在实例元数据明确提供时显示；操作请求的 source 保存在历史台账。 */
  source?: string
  /** 由分装产生时指向源瓶试剂；手工录入的瓶子为空。 */
  sourceReagentUuid?: string
  dispenseCommandId?: string
  revision: number
  createdAt?: string
  updatedAt: string
}

export interface ReagentHistoryRecord {
  maximumCapacity?: CapacityLimits
  previousMaximumCapacity?: CapacityLimits
  loadingLimits?: CapacityLimits
  previousLoadingLimits?: CapacityLimits
  containerCapacity?: CapacityLimits
  uuid: string
  materialUuid: string
  reagentUuid: string
  eventType: 'add' | 'adjust' | 'consume' | 'remove' | string
  operatorType: string
  quantityDelta: number
  quantityUnit: string
  revision: number
  recordedAt: string
  resultQuantity?: number
  resultQuantityUnit?: string
  source?: string
  workflowTaskUuid?: string
  workflowNodeJobUuid?: string
  traceId?: string
  /** 同一次分装的所有台账共用 causation_id（即分装命令 ID）。 */
  causationId?: string
  sourceReagentUuid?: string
  targetReagentUuids?: string[]
}

export interface RunPreflightCheck {
  type: string
  status: 'passed' | 'blocked' | 'deferred' | 'confirmation_required'
  code: string
  message: string
  blocking: boolean
  nodeUuid?: string
  nodeName?: string
}

export interface RunPreflightReport {
  workflowUuid: string
  workflowRevision: number
  runMode: string
  status: 'runnable_now' | 'temporarily_unavailable' | 'invalid'
  canRun: boolean
  checkedAt: string
  summary: {
    executionNodeCount: number
    passedCheckCount: number
    blockingCheckCount: number
    deferredCheckCount: number
    confirmationRequiredCount: number
  }
  checks: RunPreflightCheck[]
}

export interface WorkflowStepCandidate {
  nodeUuid: string
  name: string
  kind: string
  deviceId?: string
  actionName?: string
}

export interface WorkflowStepState {
  workflowTaskUuid: string
  executionMode: 'normal' | 'switching_to_step' | 'step'
  controlStatus: string
  inFlightJobCount: number
  requiresSelection: boolean
  canStep: boolean
  candidates: WorkflowStepCandidate[]
}

export interface EdgeSnapshot {
  capabilities: RuntimeCapabilities
  workflows: WorkflowDefinition[]
  tasks: WorkflowTask[]
  materials: MaterialRecord[]
  materialTotal: number
  workflowLoaded: number
  workflowTotal: number
}
