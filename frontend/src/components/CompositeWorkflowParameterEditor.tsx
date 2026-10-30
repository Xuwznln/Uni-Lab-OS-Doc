import type { ContractField } from '../types'

/**
 * 把发布合同的输入参数转换为前端可编辑字段。
 * @param rawParameters `input_contract.parameters` 的原始数组。
 * @returns 只包含名称和 Schema 合法项的独立字段列表。
 */
export function compositeContractFields(rawParameters: unknown): ContractField[] {
  if (!Array.isArray(rawParameters)) return []
  return rawParameters.flatMap((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return []
    const record = item as Record<string, unknown>
    if (typeof record.name !== 'string' || !record.name.trim()) return []
    const schema = record.schema && typeof record.schema === 'object' && !Array.isArray(record.schema)
      ? { ...(record.schema as Record<string, unknown>) }
      : {}
    return [{
      name: record.name,
      type: compositeSchemaType(schema),
      required: record.required === true,
      defaultValue: Object.prototype.hasOwnProperty.call(record, 'default') ? record.default : undefined,
      title: typeof record.title === 'string' ? record.title : undefined,
      description: typeof record.description === 'string' ? record.description : undefined,
      schema,
    }]
  })
}

/**
 * 用已保存的组合调用参数初始化页面文本。
 * @param fields 发布合同输入字段。
 * @param param 调用节点当前保存的独立实参。
 * @returns 按参数名索引的页面文本；未显式填写的默认值保持为空。
 */
export function compositeParameterDrafts(
  fields: ContractField[],
  param: Record<string, unknown> = {},
): Record<string, string> {
  return Object.fromEntries(fields.flatMap((field) => (
    Object.prototype.hasOwnProperty.call(param, field.name)
      ? [[field.name, compositeValueText(param[field.name])]]
      : []
  )))
}

/**
 * 校验并序列化一次组合工作流调用的固定参数。
 * @param fields 发布合同输入字段。
 * @param drafts 当前调用自己的文本草稿。
 * @returns 仅包含用户显式填写值的参数对象；可选参数默认值由 OS 合同补齐。
 * @throws 必填值缺失、数值非法或 JSON 解析失败时抛出可直接展示的中文错误。
 */
export function serialiseCompositeParameters(
  fields: ContractField[],
  drafts: Record<string, string>,
): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  for (const field of fields) {
    const text = drafts[field.name] ?? ''
    if (!text.trim()) {
      if (field.required && field.defaultValue === undefined) {
        throw new Error(`子工作流必填参数 ${field.title || field.name} 尚未填写`)
      }
      continue
    }
    result[field.name] = parseCompositeValue(text, field.schema)
  }
  return result
}

/**
 * 返回一次组合调用参数的首个保存阻断原因。
 * @param fields 发布合同输入字段。
 * @param drafts 当前调用自己的文本草稿。
 * @returns 参数合法时返回空字符串，否则返回中文原因。
 */
export function compositeParameterProblem(
  fields: ContractField[],
  drafts: Record<string, string>,
): string {
  try {
    serialiseCompositeParameters(fields, drafts)
    return ''
  } catch (error) {
    return error instanceof Error ? error.message : '子工作流参数格式不正确'
  }
}

interface CompositeWorkflowParameterEditorProps {
  operationName: string
  invocationIndex: number
  fields: ContractField[]
  drafts: Record<string, string>
  onChange: (parameterName: string, text: string) => void
}

/**
 * 展示一次子工作流调用自己的固定参数表单。
 * @param props 当前实验操作名称、调用序号、合同字段、文本草稿和更新回调。
 * @returns 没有输入时返回空内容，否则返回与调用身份绑定的参数编辑区域。
 */
export function CompositeWorkflowParameterEditor({ operationName, invocationIndex, fields, drafts, onChange }: CompositeWorkflowParameterEditorProps): React.JSX.Element | null {
  if (!fields.length) return null
  return <div className="child-parameter-list">
    {fields.map((field) => {
      const type = compositeSchemaType(field.schema)
      const label = `子工作流参数 ${operationName} 调用 ${invocationIndex} ${field.name}`
      const placeholder = field.defaultValue === undefined
        ? `${field.required ? '必填' : '选填'} · ${type}`
        : `默认值：${compositeValueText(field.defaultValue)}`
      const value = drafts[field.name] ?? ''
      return <label className="child-parameter" key={field.name}>
        <span>{field.title || field.name}<em data-required={field.required}>{field.required ? '必填' : '可选'}</em></span>
        {type === 'boolean' ? <select aria-label={label} value={value} onChange={(event) => onChange(field.name, event.target.value)}>
          <option value="">{placeholder}</option>
          <option value="true">是</option>
          <option value="false">否</option>
        </select> : type === 'object' || type === 'array' || Boolean(field.schema.$slot) ? <textarea aria-label={label} value={value} onChange={(event) => onChange(field.name, event.target.value)} placeholder={placeholder} /> : <input aria-label={label} value={value} onChange={(event) => onChange(field.name, event.target.value)} placeholder={placeholder} />}
        {field.description ? <small>{field.description}</small> : null}
      </label>
    })}
  </div>
}

/**
 * 返回参数编辑器使用的简明类型名称。
 * @param schema 发布合同中的 JSON Schema。
 * @returns 优先返回槽位类型，其次返回去除 null 后的 JSON 类型。
 */
function compositeSchemaType(schema: Record<string, unknown>): string {
  if (schema.$slot) return String(schema.$slot)
  const rawType = schema.type
  if (Array.isArray(rawType)) return String(rawType.find((item) => item !== 'null') || 'string')
  return typeof rawType === 'string' ? rawType : 'string'
}

/**
 * 把已保存的 JSON 值还原成页面文本。
 * @param value 工作流调用节点中已经保存的参数值。
 * @returns 字符串保持原样，其他值返回 JSON 文本；无法编码的值返回空串。
 */
function compositeValueText(value: unknown): string {
  if (typeof value === 'string') return value
  const encoded = JSON.stringify(value)
  return encoded === undefined ? '' : encoded
}

/**
 * 按发布合同 Schema 把页面文本转成调用参数值。
 * @param text 用户为本次子工作流调用填写的原始文本。
 * @param schema 发布合同中该参数的 JSON Schema。
 * @returns 可提交给 OS 的 JSON 值。
 * @throws 文本不符合声明类型时抛出可直接展示的中文错误。
 */
function parseCompositeValue(text: string, schema: Record<string, unknown>): unknown {
  const type = compositeSchemaType(schema)
  if (schema.$slot || type === 'object' || type === 'array') {
    try {
      return JSON.parse(text)
    } catch {
      throw new Error('子工作流 JSON 参数格式不正确')
    }
  }
  if (type === 'integer') {
    const value = Number(text)
    if (!Number.isInteger(value)) throw new Error('子工作流整数参数格式不正确')
    return value
  }
  if (type === 'number') {
    const value = Number(text)
    if (!Number.isFinite(value)) throw new Error('子工作流数值参数格式不正确')
    return value
  }
  if (type === 'boolean') {
    if (text !== 'true' && text !== 'false') throw new Error('子工作流布尔参数格式不正确')
    return text === 'true'
  }
  return text
}
