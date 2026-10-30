import { useMemo } from 'react'
import { Plus, Trash2, Workflow } from 'lucide-react'

interface EditableControlNode {
  id: string
  nodeUuid?: string
  name: string
  templateUuid: string
  nodeType?: string
  param: Record<string, unknown>
  parameterSchema: Record<string, unknown>
}

interface BuilderNode {
  id: string
  nodeUuid?: string
  name: string
  kind: 'action' | 'control'
  nodeType?: string
}

interface ControlParameterEditorProps {
  controls: EditableControlNode[]
  nodes: BuilderNode[]
  workflowParameters?: string[]
  expandedId: string
  onToggle: (id: string) => void
  onParamChange: (id: string, param: Record<string, unknown>) => void
}

type Branch = {
  label?: string
  condition?: Record<string, unknown> | null
  node_uuids?: string[]
  entry_node_uuids?: string[]
  exit_node_uuids?: string[]
}

/** 返回节点在当前编辑会话中稳定可引用的身份；新节点先使用前端草稿 ID。 */
function nodeRef(node: { id: string; nodeUuid?: string }): string {
  return node.nodeUuid || node.id
}

/** 从控制节点参数中读取字符串数组，非法值统一按空数组处理。 */
function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

/** 将表单中的字面量解析成条件表达式需要的 JSON 值；普通文本保留为字符串。 */
function parseLiteral(raw: string): unknown {
  const trimmed = raw.trim()
  if (!trimmed) return ''
  try { return JSON.parse(trimmed) } catch { return raw }
}

/** 返回条件分支在 Python 源码中的固定顺序标签；展示名称不参与执行语义。 */
function branchLabel(index: number, total: number): string {
  if (index === 0) return 'if'
  if (index === total - 1) return 'else'
  return `elif${index - 1}`
}

/** 从已保存的表达式提取表单需要的变量、运算符和比较值。 */
function conditionForm(condition: unknown): { mode: 'always' | 'compare'; variable: string; operator: string; value: string } {
  if (!condition || typeof condition !== 'object' || Array.isArray(condition)) return { mode: 'always', variable: '', operator: '==', value: '' }
  const value = condition as Record<string, unknown>
  if (value.lit === true && Object.keys(value).length === 1) return { mode: 'compare', variable: '', operator: 'truthy', value: '' }
  if (typeof value.var === 'string') return { mode: 'compare', variable: value.var, operator: 'truthy', value: '' }
  if (typeof value.binop === 'string' && value.left && typeof value.left === 'object' && !Array.isArray(value.left)) {
    const left = value.left as Record<string, unknown>
    const right = value.right && typeof value.right === 'object' && !Array.isArray(value.right) ? value.right as Record<string, unknown> : {}
    if (typeof left.var === 'string' && Object.prototype.hasOwnProperty.call(right, 'lit')) {
      return { mode: 'compare', variable: left.var, operator: value.binop, value: JSON.stringify(right.lit) }
    }
  }
  return { mode: 'compare', variable: '', operator: '==', value: '' }
}

/** 把比较表单转换成闭合的结构化条件表达式。 */
function buildCondition(variable: string, operator: string, rawValue: string): Record<string, unknown> {
  if (operator === 'truthy') return variable ? { var: variable } : { lit: true }
  return {
    binop: operator,
    left: { var: variable || 'value' },
    right: { lit: parseLiteral(rawValue) },
  }
}

/** 返回循环条件绑定的一个可编辑变量；当前产品约束每个控制节点用一个判断变量。 */
function firstBinding(param: Record<string, unknown>): { name: string; binding: Record<string, unknown> | null } {
  const bindings = param.bindings && typeof param.bindings === 'object' && !Array.isArray(param.bindings)
    ? param.bindings as Record<string, unknown>
    : {}
  const entry = Object.entries(bindings).find(([, value]) => value && typeof value === 'object' && !Array.isArray(value))
  return entry ? { name: entry[0], binding: entry[1] as Record<string, unknown> } : { name: '', binding: null }
}

/** 根据用户选中的节点生成控制区域边界，入口和出口分别取首尾节点。 */
function withBoundary(nodeRefs: string[]): Pick<Branch, 'node_uuids' | 'entry_node_uuids' | 'exit_node_uuids'> {
  return {
    node_uuids: nodeRefs,
    entry_node_uuids: nodeRefs.length ? [nodeRefs[0]] : [],
    exit_node_uuids: nodeRefs.length ? [nodeRefs[nodeRefs.length - 1]] : [],
  }
}

/** 把普通节点选择器渲染为多选列表，值使用草稿身份并在提交时由 OS 适配为 UUID。 */
function NodePicker({
  label,
  nodes,
  value,
  onChange,
  exclude,
}: {
  label: string
  nodes: BuilderNode[]
  value: string[]
  onChange: (value: string[]) => void
  exclude?: Set<string>
}) {
  const options = useMemo(() => nodes.filter((node) => !exclude?.has(nodeRef(node))), [nodes, exclude])
  return <label className="control-node-picker">
    <span>{label}</span>
    <select multiple size={Math.min(5, Math.max(2, options.length))} value={value} onChange={(event) => onChange([...event.target.selectedOptions].map((option) => option.value))}>
      {options.map((node) => <option key={nodeRef(node)} value={nodeRef(node)}>{node.name}{node.kind === 'control' ? '（控制节点）' : ''}</option>)}
    </select>
    <small>按住 Ctrl/⌘ 可多选；顺序按选择后的列表保存。</small>
  </label>
}

/** 展示并编辑条件分支与循环体；不要求用户直接编辑整段控制节点 JSON。 */
export function ControlParameterEditor({ controls, nodes, workflowParameters = [], expandedId, onToggle, onParamChange }: ControlParameterEditorProps) {
  if (!controls.length) return null

  /** 更新一个控制节点参数，同时保留其它已经配置的字段。 */
  function update(control: EditableControlNode, patch: Record<string, unknown>) {
    onParamChange(control.id, { ...control.param, ...patch })
  }

  /** 创建一个空分支；最后一个分支作为兜底，其余分支默认判断真值。 */
  function addBranch(control: EditableControlNode) {
    const branches = Array.isArray(control.param.branches) ? [...control.param.branches as Branch[]] : []
    const previousLast = branches[branches.length - 1]
    if (previousLast && previousLast.condition == null) previousLast.condition = { lit: true }
    branches.push({ condition: null, ...withBoundary([]) })
    update(control, { branches: branches.map((branch, index) => ({ ...branch, label: branchLabel(index, branches.length) })) })
  }

  /** 删除一个条件分支，至少保留一个分支避免生成无法执行的控制区域。 */
  function removeBranch(control: EditableControlNode, index: number) {
    const branches = Array.isArray(control.param.branches) ? [...control.param.branches as Branch[]] : []
    if (branches.length <= 1) return
    branches.splice(index, 1)
    update(control, { branches: branches.map((branch, branchIndex) => ({ ...branch, label: branchLabel(branchIndex, branches.length) })) })
  }

  /** 更新分支条件或成员节点；分支执行标签始终由顺序推导，避免源码合同被误填。 */
  function updateBranch(control: EditableControlNode, index: number, patch: Partial<Branch>) {
    const branches = Array.isArray(control.param.branches) ? [...control.param.branches as Branch[]] : []
    branches[index] = { ...branches[index], ...patch }
    update(control, { branches: branches.map((branch, branchIndex) => ({ ...branch, label: branchLabel(branchIndex, branches.length) })) })
  }

  /** 更新条件分支的表单表达式，并同步记录工作流输入绑定。 */
  function updateBranchCondition(control: EditableControlNode, index: number, variable: string, operator: string, value: string, mode: 'always' | 'compare') {
    const branches = Array.isArray(control.param.branches) ? [...control.param.branches as Branch[]] : []
    const bindings = control.param.bindings && typeof control.param.bindings === 'object' && !Array.isArray(control.param.bindings)
      ? { ...control.param.bindings as Record<string, unknown> }
      : {}
    if (mode === 'always') {
      branches[index] = { ...branches[index], condition: null }
    } else {
      const name = variable.trim()
      branches[index] = { ...branches[index], condition: buildCondition(name, operator, value) }
      if (name) bindings[name] = { kind: 'workflow_input', parameter: name }
    }
    update(control, {
      bindings,
      branches: branches.map((branch, branchIndex) => ({ ...branch, label: branchLabel(branchIndex, branches.length) })),
    })
  }

  /** 更新循环退出条件来源；退出条件表单替代整段 until/bindings JSON。 */
  function updateLoopCondition(control: EditableControlNode, mode: 'always' | 'workflow_input' | 'node_result', variable: string, nodeUuid: string) {
    if (mode === 'always') return update(control, { until: { lit: true }, bindings: {} })
    const name = variable.trim() || 'done'
    const binding = mode === 'workflow_input'
      ? { kind: 'workflow_input', parameter: name }
      : { kind: 'node_result', node_uuid: nodeUuid }
    update(control, { until: { var: name }, bindings: { [name]: binding } })
  }

  return <section className="node-parameter-editor control-parameter-editor" aria-label="工作流控制节点参数">
    <header><div><strong>控制节点配置</strong><span>这里配置“什么时候执行、执行哪些节点”，不需要手写整段 JSON</span></div></header>
    {controls.map((control) => {
      const controlType = control.nodeType || String(control.parameterSchema.node_type || '')
      const controlRef = nodeRef(control)
      // 当前调度器把条件/循环作为区域协调器，区域成员只能是设备 Action；
      // 不把另一个控制节点放进选择器，避免产生未定义的嵌套区域语义。
      const availableNodes = nodes.filter((node) => nodeRef(node) !== controlRef && node.kind === 'action')
      const exclude = new Set([controlRef])
      const predecessors = stringList(control.param.predecessor_node_uuids)
      const successors = stringList(control.param.successor_node_uuids)
      const branches = Array.isArray(control.param.branches) ? control.param.branches as Branch[] : []
      const bodyNodes = stringList(control.param.node_uuids)
      return <article key={control.id} className={expandedId === control.id ? 'selected' : ''}>
        <button type="button" className="control-parameter-heading" onClick={() => onToggle(control.id)}>
          <Workflow size={15} /><strong>{control.name}</strong><code>{controlType || control.templateUuid}</code>
          <span>{controlType === 'condition' ? `${branches.length} 个分支` : `${bodyNodes.length} 个循环节点`}</span>
        </button>
        {expandedId === control.id ? <div className="control-parameter-body">
          <p className="control-help">控制节点由调度器在本地执行，不会下发到设备。下面的选择会自动生成分支/循环体成员、入口和出口。</p>
          <NodePicker label="前置节点（可选）" nodes={availableNodes} value={predecessors} exclude={exclude} onChange={(value) => update(control, { predecessor_node_uuids: value })} />
          {controlType === 'condition' ? <div className="control-branches">
            <div className="control-section-heading"><strong>条件分支</strong><button type="button" onClick={() => addBranch(control)}><Plus size={13} /> 添加分支</button></div>
            {!branches.length ? <p className="control-empty">还没有分支，点击“添加分支”。</p> : null}
            {branches.map((branch, index) => {
              const members = stringList(branch.node_uuids)
              return <div className="control-branch" key={`${control.id}-branch-${index}`}>
                <div className="control-branch-heading"><span>{index + 1}</span><strong>{branchLabel(index, branches.length)} · {index === branches.length - 1 ? '兜底分支' : '判断分支'}</strong><button type="button" aria-label={`删除分支 ${index + 1}`} disabled={branches.length <= 1} onClick={() => removeBranch(control, index)}><Trash2 size={13} /></button></div>
                {(() => {
                  const form = conditionForm(branch.condition)
                  const currentVariable = form.variable || String(Object.keys(control.param.bindings && typeof control.param.bindings === 'object' && !Array.isArray(control.param.bindings) ? control.param.bindings as Record<string, unknown> : {})[0] || '')
                  return <div className="control-condition-form">
                    <label><span>条件类型</span><select aria-label={`分支 ${index + 1} 条件类型`} value={form.mode === 'always' ? 'always' : 'compare'} disabled={index === branches.length - 1} onChange={(event) => updateBranchCondition(control, index, currentVariable, form.operator, form.value, event.target.value as 'always' | 'compare')}><option value="compare">按参数判断</option><option value="always">始终执行</option></select></label>
                    {index === branches.length - 1 && form.mode === 'always' ? <small>最后一个分支没有条件；前面的分支都不满足时执行。</small> : <><label><span>参数名</span><input aria-label={`分支 ${index + 1} 参数名`} list="workflow-parameter-options" value={currentVariable} onChange={(event) => updateBranchCondition(control, index, event.target.value, form.operator, form.value, 'compare')} placeholder="例如 qualified" /></label><label><span>判断方式</span><select aria-label={`分支 ${index + 1} 判断方式`} value={form.operator} onChange={(event) => updateBranchCondition(control, index, currentVariable, event.target.value, form.value, 'compare')}><option value="truthy">为真</option><option value="==">等于</option><option value="!=">不等于</option><option value=">">大于</option><option value=">=">大于等于</option><option value="<">小于</option><option value="<=">小于等于</option></select></label>{form.operator !== 'truthy' ? <label><span>比较值</span><input aria-label={`分支 ${index + 1} 比较值`} value={form.value} onChange={(event) => updateBranchCondition(control, index, currentVariable, form.operator, event.target.value, 'compare')} placeholder="布尔值填 true 或 false，文本直接填写" /></label> : null}</>}
                  </div>
                })()}
                <NodePicker label="这个分支执行哪些节点" nodes={availableNodes} value={members} exclude={exclude} onChange={(value) => updateBranch(control, index, withBoundary(value))} />
              </div>
            })}
            <small className="control-form-note">参数名会自动绑定到工作流输入；先在上方“输入参数”添加同名参数，运行时前端会按这个名称收集值。</small>
          </div> : <div className="control-loop">
            <label><span>循环变量名</span><input aria-label={`${control.name} 循环变量名`} value={String(control.param.loop_variable || 'loop')} onChange={(event) => update(control, { loop_variable: event.target.value.trim() || 'loop' })} placeholder="例如 loop" /><small>只用于区分循环区域；循环体参数通过下方的循环变量绑定。</small></label>
            <label className="control-number-field"><span>最多循环次数</span><input type="number" min={1} value={Number(control.param.max_iterations || 1)} onChange={(event) => update(control, { max_iterations: Math.max(1, Number(event.target.value) || 1) })} /><small>达到次数仍未满足退出条件时，任务会失败并停止继续派发。</small></label>
            <NodePicker label="循环体执行哪些节点" nodes={availableNodes} value={bodyNodes} exclude={exclude} onChange={(value) => update(control, { ...withBoundary(value) })} />
            <NodePicker label="循环结束后的后置节点（可选）" nodes={availableNodes} value={successors} exclude={exclude} onChange={(value) => update(control, { successor_node_uuids: value })} />
            {(() => {
              const current = firstBinding(control.param)
              const mode = current.binding?.kind === 'node_result' ? 'node_result' : current.binding?.kind === 'workflow_input' ? 'workflow_input' : 'always'
              const sourceNode = current.binding?.kind === 'node_result' ? String(current.binding.node_uuid || '') : ''
              const untilVariable = conditionForm(control.param.until).variable || current.name
              return <div className="control-condition-form control-loop-condition">
                <label><span>退出条件来源</span><select aria-label={`${control.name} 退出条件来源`} value={mode} onChange={(event) => updateLoopCondition(control, event.target.value as 'always' | 'workflow_input' | 'node_result', untilVariable, sourceNode)}><option value="always">执行一轮后退出</option><option value="workflow_input">按工作流参数</option><option value="node_result">按循环体动作结果</option></select></label>
                {mode !== 'always' ? <><label><span>判断变量名</span><input aria-label={`${control.name} 判断变量名`} list="workflow-parameter-options" value={untilVariable} onChange={(event) => updateLoopCondition(control, mode, event.target.value, sourceNode)} placeholder="例如 done" /></label>{mode === 'node_result' ? <NodePicker label="结果来自哪个循环动作" nodes={availableNodes.filter((node) => bodyNodes.includes(nodeRef(node)))} value={sourceNode ? [sourceNode] : []} exclude={exclude} onChange={(value) => updateLoopCondition(control, mode, untilVariable, value[0] || '')} /> : <small>参数名会自动绑定到工作流输入；请在上方输入参数中提供默认值或运行时值。</small>}</> : <small>循环体成功完成一轮后结束；如需继续循环，请选择工作流参数或动作结果。</small>}
              </div>
            })()}
            <div className="control-carry-form"><strong>循环变量（可选）</strong><small>用于记录每一轮的状态；默认变量 done 会在第一轮为 false，下一轮为 true。</small><div className="control-carry-grid"><label><span>变量名</span><input aria-label={`${control.name} 循环变量名`} value={Object.keys(control.param.initial_carry || {})[0] || 'done'} onChange={(event) => { const key = event.target.value.trim() || 'done'; const initial = control.param.initial_carry && typeof control.param.initial_carry === 'object' ? Object.values(control.param.initial_carry as Record<string, unknown>)[0] : { kind: 'literal', value: false }; const next = control.param.next_carry && typeof control.param.next_carry === 'object' ? Object.values(control.param.next_carry as Record<string, unknown>)[0] : { kind: 'literal', value: true }; update(control, { initial_carry: { [key]: initial }, next_carry: { [key]: next } }) }} /></label><label><span>第一轮初值</span><input aria-label={`${control.name} 第一轮初值`} value={JSON.stringify((Object.values(control.param.initial_carry || {})[0] as Record<string, unknown> | undefined)?.value ?? false)} onChange={(event) => update(control, { initial_carry: { [Object.keys(control.param.initial_carry || {})[0] || 'done']: { kind: 'literal', value: parseLiteral(event.target.value) } } })} /></label><label><span>下一轮值</span><input aria-label={`${control.name} 下一轮值`} value={JSON.stringify((Object.values(control.param.next_carry || {})[0] as Record<string, unknown> | undefined)?.value ?? true)} onChange={(event) => update(control, { next_carry: { [Object.keys(control.param.next_carry || {})[0] || 'done']: { kind: 'literal', value: parseLiteral(event.target.value) } } })} /></label></div></div>
          </div>}
          <small className="control-footnote">保存时，前端只提交节点关系；OS 会再次校验节点是否完整、是否属于当前控制区域，以及入口/出口是否有效。</small>
        </div> : null}
      </article>
    })}
    <datalist id="workflow-parameter-options">{workflowParameters.map((name) => <option key={name} value={name} />)}</datalist>
  </section>
}
