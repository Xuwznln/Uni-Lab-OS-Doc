import type { ActionOutputRecord, ActionParameterRecord } from '../types'
import { EmptyState } from './ui'

interface NodeOutputBinding {
  kind: 'node_output'
  sourceActionId: string
  sourceHandleUuid: string
}

export type DraftInputBinding = { parameter: string } | NodeOutputBinding

export interface DraftAction {
  id: string
  nodeUuid?: string
  templateUuid: string
  /** 模板原始节点类型；人工确认关闭时用于恢复底层设备动作类型。 */
  nodeType?: string
  materialUuid: string
  deviceId: string
  name: string
  description?: string
  fields: ActionParameterRecord[]
  outputs: ActionOutputRecord[]
  param: Record<string, unknown>
  inputBindings: Record<string, DraftInputBinding>
  /** 非空表示当前设备动作被调度器包装为人工确认节点。 */
  manualConfirmation?: { timeoutSeconds: number }
}

interface UpstreamOutputOption {
  value: string
  label: string
}

/**
 * 把用户输入的文本转换为模板 Schema 声明的值。
 * @param raw 用户输入的原始文本。
 * @param schema 目标输入句柄的值 Schema。
 * @returns 转换后的参数值；空文本返回 undefined。
 */
function parseParameterValue(raw: string, schema: Record<string, unknown>): unknown {
  if (!raw.trim()) return undefined
  const rawType = schema.type
  const type = Array.isArray(rawType) ? rawType.find((item) => item !== 'null') : rawType
  if (type === 'integer') return Number.parseInt(raw, 10)
  if (type === 'number') return Number(raw)
  if (type === 'boolean') return raw === 'true'
  if (type === 'object' || type === 'array' || schema.$slot) return JSON.parse(raw)
  return raw
}

/**
 * 把参数值转换为输入框使用的文本。
 * @param value 当前参数值。
 * @returns 可显示和继续编辑的文本。
 */
function actionValue(value: unknown): string {
  if (value === undefined || value === null) return ''
  return typeof value === 'string' ? value : JSON.stringify(value)
}

/**
 * 返回输入当前采用的固定值、工作流参数或上游节点输出来源。
 * @param binding 当前输入句柄的绑定；未绑定时按固定值处理。
 * @returns 参数编辑器使用的来源分类。
 */
function inputBindingKind(binding: DraftInputBinding | undefined): 'literal' | 'workflow' | 'upstream' {
  if (binding && 'kind' in binding && binding.kind === 'node_output') return 'upstream'
  return binding && 'parameter' in binding && binding.parameter ? 'workflow' : 'literal'
}

/**
 * 判断必填输入的绑定是否已经包含可保存的参数名或真实上游身份。
 * @param binding 当前输入句柄的绑定。
 * @returns 工作流参数名或上游节点与句柄身份完整时返回 true。
 */
export function hasUsableInputBinding(binding: DraftInputBinding | undefined): boolean {
  return Boolean(binding && ('kind' in binding
    ? binding.kind === 'node_output' && binding.sourceActionId && binding.sourceHandleUuid
    : binding.parameter?.trim()))
}

/**
 * 拆出可空 Schema 中的实际类型，并记录该 Schema 是否允许空值。
 * @param schema 模板句柄声明的值 Schema。
 * @returns 实际类型 Schema 与可空标记。
 */
function unwrapNullableSchema(schema: Record<string, unknown>): { base: Record<string, unknown>; nullable: boolean } {
  const rawType = schema.type
  if (Array.isArray(rawType)) {
    const nonNullTypes = rawType.filter((item): item is string => typeof item === 'string' && item !== 'null')
    if (nonNullTypes.length === 1 && rawType.includes('null')) {
      return { base: { ...schema, type: nonNullTypes[0] }, nullable: true }
    }
  }
  const members = Array.isArray(schema.anyOf) ? schema.anyOf.filter((member): member is Record<string, unknown> => Boolean(member) && typeof member === 'object' && !Array.isArray(member)) : []
  if (!members.length) return { base: schema, nullable: false }
  const base = members.find((member) => member.type !== 'null')
  const nullable = members.some((member) => member.type === 'null')
  return { base: base || schema, nullable }
}

/**
 * 判断生产端约束是否完全落在消费端允许范围内。
 * @param producer 生产端 Schema。
 * @param consumer 消费端 Schema。
 * @param minimum 最小值约束字段。
 * @param maximum 最大值约束字段。
 * @returns 生产端范围不宽于消费端时返回 true。
 */
function schemaBoundsAreAssignable(producer: Record<string, unknown>, consumer: Record<string, unknown>, minimum: string, maximum: string): boolean {
  const consumerMinimum = typeof consumer[minimum] === 'number' ? consumer[minimum] : undefined
  const producerMinimum = typeof producer[minimum] === 'number' ? producer[minimum] : undefined
  if (consumerMinimum !== undefined && (producerMinimum === undefined || producerMinimum < consumerMinimum)) return false
  const consumerMaximum = typeof consumer[maximum] === 'number' ? consumer[maximum] : undefined
  const producerMaximum = typeof producer[maximum] === 'number' ? producer[maximum] : undefined
  return consumerMaximum === undefined || (producerMaximum !== undefined && producerMaximum <= consumerMaximum)
}

/**
 * 判断上游输出 Schema 是否可以安全赋给目标输入 Schema。
 * @param source 上游输出句柄的值 Schema。
 * @param target 目标输入句柄的值 Schema。
 * @returns 来源值集合完全包含在目标允许范围内时返回 true。
 */
function schemasCanConnect(source: Record<string, unknown>, target: Record<string, unknown>): boolean {
  const producer = unwrapNullableSchema(source)
  const consumer = unwrapNullableSchema(target)
  if (producer.nullable && !consumer.nullable) return false
  if (producer.base.$slot || consumer.base.$slot) {
    if (producer.base.$slot !== 'ResourceSlot' || consumer.base.$slot !== 'ResourceSlot') return false
    const producerAllowed = Array.isArray(producer.base.allowed_resource_template_uuids) ? producer.base.allowed_resource_template_uuids.map(String) : undefined
    const consumerAllowed = Array.isArray(consumer.base.allowed_resource_template_uuids) ? consumer.base.allowed_resource_template_uuids.map(String) : undefined
    if (!consumerAllowed) return true
    return Boolean(producerAllowed && producerAllowed.every((uuid) => consumerAllowed.includes(uuid)))
  }
  const producerType = producer.base.type
  const consumerType = consumer.base.type
  if (producerType !== consumerType && !(producerType === 'integer' && consumerType === 'number')) return false
  if (producerType === 'array') {
    const producerItems = producer.base.items
    const consumerItems = consumer.base.items
    if (!producerItems || typeof producerItems !== 'object' || Array.isArray(producerItems) || !consumerItems || typeof consumerItems !== 'object' || Array.isArray(consumerItems)) return false
    return schemasCanConnect(producerItems as Record<string, unknown>, consumerItems as Record<string, unknown>)
      && schemaBoundsAreAssignable(producer.base, consumer.base, 'minItems', 'maxItems')
  }
  if (producerType === 'object') return true
  const producerEnum = Array.isArray(producer.base.enum) ? producer.base.enum : undefined
  const consumerEnum = Array.isArray(consumer.base.enum) ? consumer.base.enum : undefined
  if (consumerEnum) return Boolean(producerEnum && producerEnum.every((value) => consumerEnum.some((candidate) => JSON.stringify(candidate) === JSON.stringify(value))))
  if (producerType === 'integer' || producerType === 'number') return schemaBoundsAreAssignable(producer.base, consumer.base, 'minimum', 'maximum')
  if (producerType === 'string') return schemaBoundsAreAssignable(producer.base, consumer.base, 'minLength', 'maxLength')
  return producerType === consumerType
}

/**
 * 收集目标设备动作（Action）之前且 Schema 兼容的输出。
 * @param actions 画布上按执行顺序排列的设备动作（Action）。
 * @param targetActionId 当前目标设备动作（Action）的草稿身份。
 * @param field 当前目标输入句柄。
 * @returns 可选的来源设备动作（Action）、真实来源句柄和显示名称。
 */
function upstreamOutputsFor(actions: DraftAction[], targetActionId: string, field: ActionParameterRecord): UpstreamOutputOption[] {
  const targetIndex = actions.findIndex((action) => action.id === targetActionId)
  if (targetIndex <= 0) return []
  return actions.slice(0, targetIndex).flatMap((action) => action.outputs
    .filter((output) => schemasCanConnect(output.schema, field.schema))
    .map((output) => ({
      value: `${action.id}|${output.handleUuid}`,
      label: `${action.name} · ${output.displayName}`,
    })))
}

/**
 * 清除节点重排后不再指向上游的输出绑定。
 * @param actions 已完成重排的设备动作（Action）草稿。
 * @returns 保留工作流参数和仍然有效的上游输出绑定的新草稿。
 */
export function removeInvalidUpstreamBindings(actions: DraftAction[]): DraftAction[] {
  const actionIndex = new Map(actions.map((action, index) => [action.id, index]))
  return actions.map((action, targetIndex) => ({
    ...action,
    inputBindings: Object.fromEntries(Object.entries(action.inputBindings).filter(([, binding]) => (
      'parameter' in binding || (actionIndex.get(binding.sourceActionId) ?? Number.MAX_SAFE_INTEGER) < targetIndex
    ))),
  }))
}

interface ActionParameterEditorProps {
  action: DraftAction
  actions: DraftAction[]
  paramDrafts: Record<string, string>
  onClose: () => void
  onBindingChange: (field: ActionParameterRecord, binding: DraftInputBinding | undefined) => void
  onLiteralChange: (field: ActionParameterRecord, value: unknown) => void
  onLiteralDraftChange: (key: string, value: string) => void
  onManualConfirmationChange: (config: DraftAction['manualConfirmation']) => void
  onNotify: (message: string) => void
  workflowParameters: string[]
}

/**
 * 编辑一个设备动作（Action）的输入来源；上游来源最终由工作流数据边保存。
 * @param props 参数面板所需的设备动作、页面状态与更新回调。
 * @param props.action 当前正在配置的设备动作草稿。
 * @param props.actions 画布中按执行顺序排列的全部设备动作草稿。
 * @param props.paramDrafts 尚未完成 JSON 解析的参数文本。
 * @param props.onClose 关闭参数面板的回调。
 * @param props.onBindingChange 更新输入来源的回调。
 * @param props.onLiteralChange 更新固定值的回调。
 * @param props.onLiteralDraftChange 暂存对象或数组输入文本的回调。
 * @param props.onNotify 展示输入错误的回调。
 * @returns 当前设备动作（Action）的参数来源编辑区域。
 */
export function ActionParameterEditor({ action, actions, paramDrafts, onClose, onBindingChange, onLiteralChange, onLiteralDraftChange, onManualConfirmationChange, onNotify, workflowParameters }: ActionParameterEditorProps): React.JSX.Element {
  const manualConfirmation = action.manualConfirmation
  const hasFixedDevice = Boolean(action.materialUuid && action.deviceId)
  return <div className="node-parameter-editor">
    <header><div><strong>{action.name} · 节点参数</strong><span>固定值、工作流参数或上游节点输出</span></div><button aria-label="关闭节点参数" onClick={onClose}>×</button></header>
    <section className="manual-confirmation-editor">
      <label className="manual-confirmation-toggle">
        <input
          type="checkbox"
          checked={Boolean(manualConfirmation)}
          disabled={!hasFixedDevice && !manualConfirmation}
          onChange={(event) => onManualConfirmationChange(
            event.target.checked
              ? { timeoutSeconds: manualConfirmation?.timeoutSeconds || 3600 }
              : undefined,
          )}
        />
        <span><strong>执行前需要人工确认</strong><small>批准后才会下发这个设备动作；拒绝或超时会取消任务。</small></span>
      </label>
      {!hasFixedDevice ? <small className="manual-confirmation-notice">请先在节点卡片选择设备实例，才能启用人工确认。</small> : null}
      {manualConfirmation ? (
        <label className="manual-confirmation-timeout">
          <span>确认超时（秒）</span>
          <input
            type="number"
            min={1}
            max={86400}
            step={1}
            value={manualConfirmation.timeoutSeconds}
            aria-label={`人工确认超时 ${action.name}`}
            onChange={(event) => onManualConfirmationChange({
              timeoutSeconds: Number(event.target.value),
            })}
          />
        </label>
      ) : null}
    </section>
    {action.fields.length ? action.fields.map((field) => {
      const binding = action.inputBindings[field.handleUuid]
      const source = inputBindingKind(binding)
      const upstreamOptions = upstreamOutputsFor(actions, action.id, field)
      const selectedUpstream = binding && 'kind' in binding && binding.kind === 'node_output'
        ? `${binding.sourceActionId}|${binding.sourceHandleUuid}`
        : ''
      const draftKey = `${action.id}:${field.key}`
      const rawType = field.schema.type
      const fieldType = Array.isArray(rawType) ? rawType.find((item) => item !== 'null') : rawType
      const valueEditor = fieldType === 'object' || fieldType === 'array' || field.schema.$slot
        ? <textarea aria-label={`节点参数 ${field.key}`} value={paramDrafts[draftKey] ?? actionValue(action.param[field.key])} onChange={(event) => onLiteralDraftChange(draftKey, event.target.value)} onBlur={(event) => {
          try { onLiteralChange(field, parseParameterValue(event.target.value, field.schema)) } catch { onNotify(`参数 ${field.key} 不是有效 JSON`) }
        }} placeholder={`${field.required ? '必填' : '选填'} · JSON`} />
        : <input aria-label={`节点参数 ${field.key}`} value={actionValue(action.param[field.key])} onChange={(event) => {
          try { onLiteralChange(field, parseParameterValue(event.target.value, field.schema)) } catch { /* 保留上一个有效值 */ }
        }} placeholder={`${field.required ? '必填' : '选填'} · ${String(fieldType || 'string')}`} />
      return <div className="node-parameter-row" key={field.handleUuid}>
        <label><span>{field.displayName}<em data-required={field.required}>{field.required ? '必填' : '选填'}</em></span><code>{field.key}</code></label>
        <select aria-label={`参数来源 ${action.name} ${field.key}`} value={source} onChange={(event) => {
          if (event.target.value === 'workflow') {
            onLiteralChange(field, undefined)
            onBindingChange(field, { parameter: field.key })
          } else if (event.target.value === 'upstream') {
            onLiteralChange(field, undefined)
            onBindingChange(field, { kind: 'node_output', sourceActionId: '', sourceHandleUuid: '' })
          } else {
            onBindingChange(field, undefined)
          }
        }}>
          <option value="literal">固定值</option>
          <option value="workflow">工作流参数</option>
          <option value="upstream" disabled={!upstreamOptions.length}>上游节点输出</option>
        </select>
        {source === 'workflow' ? <select aria-label={`工作流参数 ${action.name} ${field.key}`} value={binding && 'parameter' in binding ? binding.parameter : ''} onChange={(event) => onBindingChange(field, event.target.value ? { parameter: event.target.value } : undefined)}><option value="">选择已声明的工作流参数</option>{workflowParameters.map((parameter) => <option value={parameter} key={parameter}>{parameter}</option>)}</select>
          : source === 'upstream' ? <select aria-label={`上游输出 ${action.name} ${field.key}`} value={selectedUpstream} onChange={(event) => {
            const [sourceActionId, sourceHandleUuid] = event.target.value.split('|')
            onBindingChange(field, { kind: 'node_output', sourceActionId: sourceActionId || '', sourceHandleUuid: sourceHandleUuid || '' })
          }}>
            <option value="">选择上游节点输出</option>
            {upstreamOptions.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
          </select>
          : valueEditor}
      </div>
    }) : <EmptyState title="该设备动作没有输入参数" description="输出端口和 ready 控制依赖由模板定义。" />}
  </div>
}
