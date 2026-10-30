import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Cpu,
  FlaskConical,
  Play,
  RefreshCw,
  Search,
  Square,
  Wifi,
  WifiOff,
} from 'lucide-react'
import {
  DeviceActionRunRequestError,
  TaskCommandRequestError,
  commandWorkflowTask,
  createDeviceActionRun,
  loadActionTemplates,
  loadDeviceActionDefinition,
  loadDeviceActionRunStatus,
  loadDeviceRuntimeCatalog,
} from '../lib/edgeClient'
import { ManualActionParameters, parameterDefaults, parseManualParameters } from '../components/ManualActionParameters'
import { Button, EmptyState, PageHeader, Panel, PanelHeader } from '../components/ui'
import { DEFAULT_RUNTIME_CAPABILITIES } from '../types'
import type {
  ActionTemplateRecord,
  DeviceActionRunIntent,
  DeviceActionRunReference,
  DeviceActionRunStatus,
  DeviceRuntimeRecord,
  MaterialRecord,
  RuntimeCapabilities,
} from '../types'
import './DevicesPage.css'

const terminalTaskStatuses = new Set(['succeeded', 'failed', 'canceled', 'timeout'])
const terminalJobStatuses = new Set(['succeeded', 'failed', 'canceled', 'timeout', 'skipped'])
const runStorageKey = 'unilab:device-action:last-run'
const uncertainIntentStorageKey = 'unilab:device-action:uncertain-intent'
const cancelIntentStorageKey = 'unilab:device-action:cancel-intent'
const taskStatusLabels: Record<string, string> = {
  pending: '待调度', running: '运行中', succeeded: '已完成', failed: '失败',
  canceled: '已取消', canceling: '取消中', timeout: '超时', paused: '已暂停',
}

const jobStatusLabels: Record<string, string> = {
  pending: '待派发', dispatched: '已派发', running: '运行中', succeeded: '成功',
  failed: '失败', cancel_requested: '停止请求中', canceled: '已取消', timeout: '超时', skipped: '已跳过',
}

interface DeviceChoice {
  material: MaterialRecord
  runtime?: DeviceRuntimeRecord
  actions: ActionTemplateRecord[]
  simulator: boolean
}

interface DeviceCancelIntent {
  taskUuid: string
  idempotencyKey: string
  state: 'pending' | 'accepted' | 'unknown'
}

function readStoredValue<T>(key: string): T | undefined {
  try {
    const raw = window.sessionStorage.getItem(key)
    return raw ? JSON.parse(raw) as T : undefined
  } catch {
    return undefined
  }
}

function commandUuid(prefix: string): string {
  return globalThis.crypto?.randomUUID?.()
    || `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function runEnded(status?: DeviceActionRunStatus): boolean {
  return Boolean(status && terminalTaskStatuses.has(status.taskStatus) && terminalJobStatuses.has(status.jobStatus))
}

function displayTime(value?: string): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function jsonBlock(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2)
}

function DeviceRunPanel({ reference, status, loading, error, canceling, cancelState, writable, onRefresh, onCancel, onOpenTask }: {
  reference?: DeviceActionRunReference
  status?: DeviceActionRunStatus
  loading: boolean
  error?: Error | null
  canceling: boolean
  cancelState?: DeviceCancelIntent['state']
  writable: boolean
  onRefresh: () => void
  onCancel: () => void
  onOpenTask: (taskUuid: string) => void
}) {
  if (!reference) {
    return <Panel className="device-run-panel"><PanelHeader title="本次运行" description="执行后在这里显示 Task、Job、反馈与结果。" /><EmptyState title="尚未执行动作" description="先选择设备和动作，确认参数后提交。" /></Panel>
  }
  const taskStatus = status?.taskStatus || reference.taskStatus
  const jobStatus = status?.jobStatus || reference.jobStatus
  const ended = runEnded(status)
  return <Panel className="device-run-panel">
    <PanelHeader title="本次运行" description="状态来自持久 WorkflowTask 和 WorkflowNodeJob。" action={<button className="device-icon-action" aria-label="刷新单点动作状态" onClick={onRefresh}><RefreshCw size={15} className={loading ? 'spin' : ''} /></button>} />
    <div className="device-run-state">
      <span className="device-run-state-icon" data-status={jobStatus}>{ended ? <CheckCircle2 size={20} /> : <Activity size={20} />}</span>
      <div><strong>{status?.actionName || '设备单点动作'}</strong><small>{ended ? '已取得明确终态' : '动作仍可能占用设备资源'}</small></div>
      <span className="device-status-pill" data-status={jobStatus}>{jobStatusLabels[jobStatus] || jobStatus}</span>
    </div>
    {error ? <div className="device-inline-error" role="alert">状态读取失败：{error.message}</div> : null}
    <dl className="device-run-facts">
      <div><dt>Task 状态</dt><dd>{taskStatusLabels[taskStatus] || taskStatus}</dd></div>
      <div><dt>Job 状态</dt><dd>{jobStatusLabels[jobStatus] || jobStatus}</dd></div>
      <div><dt>开始时间</dt><dd>{displayTime(status?.startedAt)}</dd></div>
      <div><dt>结束时间</dt><dd>{displayTime(status?.finishedAt)}</dd></div>
    </dl>
    <div className="device-run-identities"><span>Task <code>{reference.taskUuid}</code></span><span>Job <code>{reference.jobUuid}</code></span></div>
    {status?.waitReason ? <div className="device-wait-reason"><strong>等待原因</strong><pre>{jsonBlock(status.waitReason)}</pre></div> : null}
    <details className="device-run-details">
      <summary>查看参数、反馈和执行结果</summary>
      <strong>参数</strong><pre>{jsonBlock(status?.param)}</pre>
      <strong>反馈</strong><pre>{jsonBlock(status?.feedback)}</pre>
      <strong>结果</strong><pre>{jsonBlock(status?.result)}</pre>
      {status?.errors.length ? <><strong>错误</strong><pre>{jsonBlock(status.errors)}</pre></> : null}
    </details>
    <div className="device-run-actions">
      <Button tone="ghost" onClick={() => onOpenTask(reference.taskUuid)}>在任务页查看</Button>
      {!ended ? <Button tone="danger" icon={<Square size={13} />} disabled={canceling || cancelState === 'accepted' || !writable} onClick={onCancel}>{canceling ? '正在请求停止' : cancelState === 'accepted' ? '停止请求已受理' : cancelState === 'pending' || cancelState === 'unknown' ? '确认原停止请求' : '停止任务'}</Button> : null}
    </div>
    {!ended ? <p className="device-run-footnote">停止命令受理后仍需等待 Job 进入明确终态；在此之前设备资源可能继续被占用。</p> : null}
  </Panel>
}

export function DevicesPage({ materials, connected, capabilities, targetDeviceUuid, onSelectDevice, onOpenTask, onRefresh, onNotify }: {
  materials: MaterialRecord[]
  connected: boolean
  capabilities?: RuntimeCapabilities
  targetDeviceUuid?: string
  onSelectDevice: (materialUuid: string) => void
  onOpenTask: (taskUuid: string) => void
  onRefresh: () => void
  onNotify: (message: string) => void
}) {
  capabilities = capabilities || DEFAULT_RUNTIME_CAPABILITIES
  const queryClient = useQueryClient()
  const submitLock = useRef(false)
  const cancelLock = useRef(false)
  const [selectedDeviceUuid, setSelectedDeviceUuid] = useState(targetDeviceUuid || '')
  const [selectedActionUuid, setSelectedActionUuid] = useState('')
  const [actionFilter, setActionFilter] = useState('')
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [raw, setRaw] = useState('{}')
  const [asJson, setAsJson] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [timeoutSeconds, setTimeoutSeconds] = useState(120)
  const [inputError, setInputError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [canceling, setCanceling] = useState(false)
  const [runReference, setRunReference] = useState<DeviceActionRunReference | undefined>(() => readStoredValue<DeviceActionRunReference>(runStorageKey))
  const [uncertainIntent, setUncertainIntent] = useState<DeviceActionRunIntent | undefined>(() => readStoredValue<DeviceActionRunIntent>(uncertainIntentStorageKey))
  const [cancelIntent, setCancelIntent] = useState<DeviceCancelIntent | undefined>(() => readStoredValue<DeviceCancelIntent>(cancelIntentStorageKey))

  const storeUncertainIntent = (value?: DeviceActionRunIntent) => {
    setUncertainIntent(value)
    try {
      if (value) window.sessionStorage.setItem(uncertainIntentStorageKey, JSON.stringify(value))
      else window.sessionStorage.removeItem(uncertainIntentStorageKey)
    } catch { /* sessionStorage 不可用时仍保留当前页状态。 */ }
  }

  const storeCancelIntent = (value?: DeviceCancelIntent) => {
    setCancelIntent(value)
    try {
      if (value) window.sessionStorage.setItem(cancelIntentStorageKey, JSON.stringify(value))
      else window.sessionStorage.removeItem(cancelIntentStorageKey)
    } catch { /* sessionStorage 不可用时仍保留当前页状态。 */ }
  }

  const templatesQuery = useQuery({
    queryKey: ['device-action-templates'],
    queryFn: ({ signal }) => loadActionTemplates(signal),
    enabled: connected,
    retry: 1,
  })
  const runtimeQuery = useQuery({
    queryKey: ['device-runtime-catalog'],
    queryFn: ({ signal }) => loadDeviceRuntimeCatalog(signal),
    enabled: connected,
    retry: 1,
    refetchInterval: 10_000,
  })

  const devices = useMemo<DeviceChoice[]>(() => {
    const templates = templatesQuery.data || []
    const byResource = new Map<string, ActionTemplateRecord[]>()
    templates.forEach((template) => {
      const key = template.resourceTemplate.uuid
      if (!key) return
      byResource.set(key, [...(byResource.get(key) || []), template])
    })
    const runtimes = runtimeQuery.data || []
    const runtimeByMaterial = new Map(runtimes.flatMap((runtime) => runtime.materialUuid ? [[runtime.materialUuid, runtime] as const] : []))
    const localRuntimeByLocalId = new Map(runtimes.filter((runtime) => runtime.source === 'local_runtime').map((runtime) => [runtime.localId, runtime]))
    return materials.flatMap((material) => {
      if (!material.resourceTemplateUuid || material.config?.category === 'platform-executor') return []
      const available = byResource.get(material.resourceTemplateUuid) || []
      if (!available.length) return []
      const runtime = runtimeByMaterial.get(material.uuid)
        || (material.sourceNodeId ? localRuntimeByLocalId.get(material.sourceNodeId) : undefined)
      const runtimeActions = new Map(runtime?.actions.map((action) => [action.name, action]))
      const actions = runtime
        ? available.filter((template) => {
          const capability = runtimeActions.get(template.name)
          return Boolean(capability && (!capability.type || !template.type || capability.type === template.type))
        })
        : available
      if (!actions.length) return []
      return [{
        material,
        runtime,
        actions: [...actions].sort((left, right) => left.displayName.localeCompare(right.displayName, 'zh-CN')),
        simulator: material.config?.simulator === true,
      }]
    }).sort((left, right) => left.material.name.localeCompare(right.material.name, 'zh-CN'))
  }, [materials, runtimeQuery.data, templatesQuery.data])

  useEffect(() => {
    if (!devices.length) return
    const requestedUuid = uncertainIntent?.materialUuid || targetDeviceUuid
    const requested = requestedUuid && devices.some((device) => device.material.uuid === requestedUuid)
      ? requestedUuid
      : undefined
    const nextDeviceUuid = requested && requested !== selectedDeviceUuid
      ? requested
      : !devices.some((device) => device.material.uuid === selectedDeviceUuid)
        ? requested || devices[0].material.uuid
        : undefined
    if (!nextDeviceUuid) return
    setSelectedDeviceUuid(nextDeviceUuid)
    setSelectedActionUuid('')
    setActionFilter('')
    setDrafts({})
    setRaw('{}')
    setAsJson(false)
    setConfirmed(false)
    setInputError('')
  }, [devices, selectedDeviceUuid, targetDeviceUuid, uncertainIntent?.materialUuid])

  useEffect(() => {
    if (!uncertainIntent) return
    const requestedDevice = devices.find((item) => item.material.uuid === uncertainIntent.materialUuid)
    if (!requestedDevice) return
    if (targetDeviceUuid !== uncertainIntent.materialUuid) onSelectDevice(uncertainIntent.materialUuid)
    if (selectedDeviceUuid !== uncertainIntent.materialUuid) return
    if (requestedDevice.actions.some((action) => action.uuid === uncertainIntent.workflowNodeTemplateUuid)
      && selectedActionUuid !== uncertainIntent.workflowNodeTemplateUuid) {
      setSelectedActionUuid(uncertainIntent.workflowNodeTemplateUuid)
    }
  }, [devices, onSelectDevice, selectedActionUuid, selectedDeviceUuid, targetDeviceUuid, uncertainIntent])

  const device = devices.find((item) => item.material.uuid === selectedDeviceUuid)
  const selectedAction = device?.actions.find((action) => action.uuid === selectedActionUuid)
  const actionDefinitionQuery = useQuery({
    queryKey: ['device-action-definition', selectedActionUuid, device?.material.uuid, device?.runtime?.localId],
    queryFn: ({ signal }) => loadDeviceActionDefinition(selectedActionUuid, {
      materialUuid: device!.material.uuid,
      localId: device!.runtime?.localId || device!.material.sourceNodeId || '',
    }, signal),
    enabled: connected && Boolean(selectedActionUuid && device),
    retry: false,
  })
  const definition = actionDefinitionQuery.data

  useEffect(() => {
    if (!definition || definition.action_id !== selectedActionUuid) return
    setDrafts(parameterDefaults(definition))
    setRaw(JSON.stringify(definition.defaults, null, 2))
    setAsJson(false)
    setConfirmed(false)
    setInputError('')
  }, [definition?.action_id, selectedActionUuid])

  const statusQuery = useQuery({
    queryKey: ['device-action-run-status', runReference?.taskUuid, runReference?.jobUuid],
    queryFn: ({ signal }) => loadDeviceActionRunStatus(runReference!.taskUuid, runReference!.jobUuid, signal),
    enabled: connected && Boolean(runReference),
    retry: 1,
    refetchInterval: (query) => runEnded(query.state.data) ? false : 1_500,
  })

  useEffect(() => {
    if (runReference) window.sessionStorage.setItem(runStorageKey, JSON.stringify(runReference))
    else window.sessionStorage.removeItem(runStorageKey)
  }, [runReference])

  useEffect(() => {
    if (uncertainIntent) window.sessionStorage.setItem(uncertainIntentStorageKey, JSON.stringify(uncertainIntent))
    else window.sessionStorage.removeItem(uncertainIntentStorageKey)
  }, [uncertainIntent])

  useEffect(() => {
    if (cancelIntent) window.sessionStorage.setItem(cancelIntentStorageKey, JSON.stringify(cancelIntent))
    else window.sessionStorage.removeItem(cancelIntentStorageKey)
  }, [cancelIntent])

  useEffect(() => {
    if (statusQuery.data && runEnded(statusQuery.data)) storeCancelIntent(undefined)
  }, [statusQuery.data?.taskStatus, statusQuery.data?.jobStatus])

  const chooseDevice = (materialUuid: string) => {
    if (submitting || uncertainIntent) return
    setSelectedDeviceUuid(materialUuid)
    setSelectedActionUuid('')
    setActionFilter('')
    setDrafts({})
    setRaw('{}')
    setAsJson(false)
    setConfirmed(false)
    setInputError('')
    onSelectDevice(materialUuid)
  }

  const chooseAction = (templateUuid: string) => {
    if (submitting || uncertainIntent) return
    setSelectedActionUuid(templateUuid)
    setDrafts({})
    setRaw('{}')
    setAsJson(false)
    setConfirmed(false)
    setInputError('')
  }

  const performRun = async (retryIntent?: DeviceActionRunIntent) => {
    if (submitLock.current) return
    if (!retryIntent && !writable) return
    let intent = retryIntent
    if (!intent) {
      if (!definition || !device) return
      try {
        const param = parseManualParameters(definition, drafts, raw, asJson)
        if (!Number.isInteger(timeoutSeconds) || timeoutSeconds < 0) throw new Error('执行超时必须是非负整数秒')
        intent = {
          materialUuid: device.material.uuid,
          workflowNodeTemplateUuid: definition.action_id,
          param,
          executionTimeoutSeconds: timeoutSeconds,
          idempotencyKey: commandUuid('device-action'),
          description: `${device.material.name} · ${definition.display_name}`,
        }
      } catch (error) {
        setInputError(error instanceof Error ? error.message : '请检查动作参数')
        return
      }
    }
    submitLock.current = true
    setSubmitting(true)
    setInputError('')
    // 请求到达服务端后页面可能随时刷新；必须先同步保存原始幂等意图。
    storeUncertainIntent(intent)
    try {
      const created = await createDeviceActionRun(intent)
      setRunReference(created)
      storeUncertainIntent(undefined)
      storeCancelIntent(undefined)
      setConfirmed(false)
      await queryClient.invalidateQueries({ queryKey: ['edge-tasks'] })
      onNotify(created.created ? '单点动作已提交调度' : '已恢复同一次单点动作请求')
    } catch (error) {
      const unknown = error instanceof DeviceActionRunRequestError && error.outcomeUnknown
      if (!unknown) storeUncertainIntent(undefined)
      setInputError(error instanceof Error ? error.message : '设备动作提交失败')
    } finally {
      submitLock.current = false
      setSubmitting(false)
    }
  }

  const cancelRun = async () => {
    if (!connected || !runReference || cancelLock.current) return
    const intent = cancelIntent?.taskUuid === runReference.taskUuid
      ? { ...cancelIntent, state: cancelIntent.state || 'unknown' as const }
      : { taskUuid: runReference.taskUuid, idempotencyKey: commandUuid('device-cancel'), state: 'pending' as const }
    // 与创建动作相同，停止命令也要在发出前同步保存幂等键。
    storeCancelIntent({ ...intent, state: 'pending' })
    cancelLock.current = true
    setCanceling(true)
    try {
      const command = await commandWorkflowTask(runReference.taskUuid, 'cancel', undefined, intent.idempotencyKey)
      const state = command.status === 'succeeded' ? 'accepted' : 'pending'
      storeCancelIntent({ ...intent, state })
      try { await statusQuery.refetch() } catch { /* 状态轮询会继续重试。 */ }
      void queryClient.invalidateQueries({ queryKey: ['edge-tasks'] })
      onNotify(state === 'accepted'
        ? '停止命令已受理，正在等待设备动作终止'
        : '停止命令已记录；请确认原停止请求，或等待状态更新')
    } catch (error) {
      const unknown = error instanceof TaskCommandRequestError && error.outcomeUnknown
      if (unknown) storeCancelIntent({ ...intent, state: 'unknown' })
      else storeCancelIntent(undefined)
      onNotify(unknown
        ? '停止命令结果未知，请使用同一请求确认，设备仍可能运行'
        : error instanceof Error ? error.message : '停止命令提交失败')
    } finally {
      cancelLock.current = false
      setCanceling(false)
    }
  }

  const refreshAll = () => {
    void templatesQuery.refetch()
    void runtimeQuery.refetch()
    if (selectedActionUuid) void actionDefinitionQuery.refetch()
    if (runReference) void statusQuery.refetch()
    onRefresh()
  }

  const filteredActions = device?.actions.filter((action) => {
    const keyword = actionFilter.trim().toLowerCase()
    return !keyword || `${action.displayName} ${action.name} ${action.description}`.toLowerCase().includes(keyword)
  }) || []
  const actionRuntime = device?.runtime?.actions.find((action) => action.name === selectedAction?.name)
  const catalogAuthoritative = runtimeQuery.dataUpdatedAt > 0 && !runtimeQuery.error && !templatesQuery.error
  const writable = connected && capabilities.canExecuteDeviceActions && catalogAuthoritative && !actionDefinitionQuery.error
    && device?.runtime?.online === true && device.runtime.dispatchable && !actionRuntime?.busy
  const formFrozen = submitting || Boolean(uncertainIntent)
  const materialOptions = materials.filter((material) => material.uuid !== device?.material.uuid && material.config?.category !== 'platform-executor')
  const onlineCount = devices.filter((item) => item.runtime?.online).length
  const uncertainDeviceName = uncertainIntent
    ? materials.find((item) => item.uuid === uncertainIntent.materialUuid)?.name || uncertainIntent.materialUuid
    : ''
  const uncertainActionName = uncertainIntent
    ? templatesQuery.data?.find((item) => item.uuid === uncertainIntent.workflowNodeTemplateUuid)?.displayName || uncertainIntent.workflowNodeTemplateUuid
    : ''
  const activeCancelState = cancelIntent && cancelIntent.taskUuid === runReference?.taskUuid
    ? cancelIntent.state || 'unknown'
    : undefined

  return <div className="devices-page" data-writable={writable}>
    <PageHeader
      eyebrow="Device control"
      title="设备单点动作"
      description="选择设备与已发布动作，校验参数后通过统一调度器执行；每次操作都会生成可追踪的 Task 和 Job。"
      actions={<Button icon={<RefreshCw size={15} />} onClick={refreshAll}>刷新设备状态</Button>}
    />

    {uncertainIntent ? <div className="device-uncertain-notice" role="alert"><AlertTriangle size={19} /><div><strong>上一条动作请求结果未知</strong><span>原目标：{uncertainDeviceName} · {uncertainActionName}。设备、动作和参数已冻结；请使用同一幂等键确认，避免重复动作。</span><code>{uncertainIntent.idempotencyKey}</code></div><Button tone="danger" disabled={submitting || !connected} onClick={() => { void performRun(uncertainIntent) }}>{submitting ? '正在确认' : '重试原请求'}</Button></div> : null}

    <section className="device-overview-strip" aria-label="设备控制概览">
      <div><span><Cpu size={17} /></span><p><small>可控设备</small><strong>{devices.length}</strong></p></div>
      <div><span><Wifi size={17} /></span><p><small>在线设备</small><strong>{onlineCount}</strong></p></div>
      <div><span><Activity size={17} /></span><p><small>当前动作</small><strong>{selectedAction ? selectedAction.displayName : '未选择'}</strong></p></div>
    </section>

    <div className="device-control-grid">
      <Panel className="device-picker-panel">
        <PanelHeader title="1. 选择设备" description="设备身份来自库存物料，在线状态来自 Edge 运行时。" />
        {templatesQuery.isLoading || runtimeQuery.isLoading ? <div className="device-loading">正在读取设备与动作目录…</div> : null}
        {templatesQuery.isError || runtimeQuery.isError ? <div className="device-inline-error" role="alert">设备目录读取失败：{templatesQuery.error?.message || runtimeQuery.error?.message}</div> : null}
        {!templatesQuery.isLoading && !runtimeQuery.isLoading && !devices.length ? <EmptyState title="没有可控设备" description="请确认 Workspace 已加载设备资源图、动作模板，并且设备已注册到 Edge。" /> : <div className="device-picker-list">
          {devices.map((item) => <button type="button" key={item.material.uuid} disabled={formFrozen} className={selectedDeviceUuid === item.material.uuid ? 'selected' : ''} aria-pressed={selectedDeviceUuid === item.material.uuid} onClick={() => chooseDevice(item.material.uuid)}>
            <span className="device-picker-icon"><Cpu size={18} /></span>
            <span className="device-picker-copy"><strong>{item.material.name}</strong><small>{item.material.sourceNodeId || item.material.className}</small><em>{item.actions.length} 个动作 · {item.simulator ? '仿真' : '实体'}</em></span>
            <span className="device-connectivity" data-online={item.runtime?.online === true}>{item.runtime?.online ? <Wifi size={14} /> : <WifiOff size={14} />}{item.runtime?.online ? item.runtime.dispatchable ? '在线' : '不可派发' : '离线'}</span>
          </button>)}
        </div>}
      </Panel>

      <Panel className="device-action-panel">
        <PanelHeader title="2. 选择动作" description={device ? `${device.material.name} · ${device.actions.length} 个已发布动作` : '请先选择设备'} />
        {device ? <label className="device-action-search"><Search size={15} /><input aria-label="搜索设备动作" value={actionFilter} onChange={(event) => setActionFilter(event.target.value)} placeholder="搜索动作名称" /></label> : null}
        {!device ? <EmptyState title="等待选择设备" description="选择左侧设备后，这里会显示与其资源模板匹配的动作。" /> : filteredActions.length ? <div className="device-action-list">
          {filteredActions.map((action) => {
            const runtime = device.runtime?.actions.find((item) => item.name === action.name)
            return <button type="button" key={action.uuid} disabled={formFrozen} className={selectedActionUuid === action.uuid ? 'selected' : ''} aria-pressed={selectedActionUuid === action.uuid} onClick={() => chooseAction(action.uuid)}>
              <span><strong>{action.description || action.displayName}</strong><code>{action.name}</code></span>
              <em data-busy={runtime?.busy === true}>{runtime?.busy ? '忙碌' : action.type || 'Action'}</em>
            </button>
          })}
        </div> : <EmptyState title="没有匹配动作" description="清除搜索条件，或检查设备注册能力与动作模板是否一致。" />}
      </Panel>

      <Panel className="device-parameter-panel">
        <PanelHeader title="3. 参数与执行" description={selectedAction?.description || definition?.description || '动作参数由已发布的冻结 Schema 生成。'} />
        {!selectedAction ? <EmptyState title="等待选择动作" description="选择动作后填写参数并确认现场安全。" /> : actionDefinitionQuery.isLoading ? <div className="device-loading">正在读取动作 Schema…</div> : actionDefinitionQuery.isError ? <div className="device-inline-error" role="alert"><span>动作参数读取失败：{actionDefinitionQuery.error.message}</span><Button tone="ghost" icon={<RefreshCw size={13} />} disabled={actionDefinitionQuery.isFetching} onClick={() => { void actionDefinitionQuery.refetch() }}>{actionDefinitionQuery.isFetching ? '正在重新读取' : '重新读取动作参数'}</Button></div> : definition ? <form className="device-action-form" onSubmit={(event) => { event.preventDefault(); void performRun() }}>
          <div className="device-action-summary"><span><FlaskConical size={16} /></span><div><strong>{definition.display_name}</strong><code>{definition.name}</code></div></div>
          <label className="device-json-toggle"><input type="checkbox" checked={asJson} disabled={formFrozen} onChange={(event) => {
            if (event.target.checked) {
              try { setRaw(JSON.stringify(parseManualParameters(definition, drafts, raw), null, 2)) } catch { setRaw(JSON.stringify(definition.defaults, null, 2)) }
            } else {
              try { setDrafts(parameterDefaults({ ...definition, defaults: parseManualParameters(definition, {}, raw, true) })) } catch { setDrafts(parameterDefaults(definition)) }
            }
            setAsJson(event.target.checked)
            setConfirmed(false)
            setInputError('')
          }} />使用完整 JSON 参数</label>
          <ManualActionParameters
            action={definition}
            drafts={drafts}
            raw={raw}
            asJson={asJson}
            disabled={formFrozen}
            materials={materialOptions}
            onDraft={(key, value) => { setDrafts((current) => ({ ...current, [key]: value })); setConfirmed(false); setInputError('') }}
            onRaw={(value) => { setRaw(value); setConfirmed(false); setInputError('') }}
          />
          <label className="device-timeout-field"><span>执行超时（秒）</span><input aria-label="执行超时（秒）" type="number" min={0} step={1} value={timeoutSeconds} disabled={formFrozen} onChange={(event) => { setTimeoutSeconds(Number(event.target.value)); setConfirmed(false) }} /><small>超时会进入终止流程；设为 0 表示不设置动作级超时。</small></label>
          <label className="device-safety-check"><input type="checkbox" checked={confirmed} disabled={formFrozen || !writable} onChange={(event) => setConfirmed(event.target.checked)} /><span><strong>我已核对设备现场和动作参数</strong><small>确认运动范围内无人、夹具与耗材状态正确，并理解该动作会真实下发到 {device?.simulator ? '仿真设备' : '设备'}。</small></span></label>
          {inputError ? <div className="device-inline-error" role="alert">{inputError}</div> : null}
          {!device?.runtime?.online ? <p className="device-form-gate">设备当前不在线，不能执行动作。</p> : !device.runtime.dispatchable ? <p className="device-form-gate">Edge 已识别设备，但当前不允许派发动作。</p> : actionRuntime?.busy ? <p className="device-form-gate">设备正在执行其他运行时动作，请等待结束后刷新。</p> : null}
          <Button type="submit" tone="primary" icon={<Play size={14} />} disabled={!writable || !confirmed || submitting || Boolean(uncertainIntent)}>{submitting ? '正在提交' : '执行单点动作'}</Button>
        </form> : null}
      </Panel>

      <DeviceRunPanel
        reference={runReference}
        status={statusQuery.data}
        loading={statusQuery.isFetching}
        error={statusQuery.error}
        canceling={canceling}
        cancelState={activeCancelState}
        writable={connected}
        onRefresh={() => { void statusQuery.refetch() }}
        onCancel={() => { void cancelRun() }}
        onOpenTask={onOpenTask}
      />
    </div>
  </div>
}
