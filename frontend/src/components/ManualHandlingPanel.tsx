import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { loadActionTemplates } from '../lib/edgeClient'
import { loadManualActionDetail, loadManualDeviceActions, sessionFields } from '../lib/stationErrorClient'
import type { HandlingSession, ManualActionJob, StationSnapshot } from '../lib/stationErrorClient'
import { loadManualHandlingDraft, manualActionEnded, manualActionNeedsReview, manualSessionActions, saveManualHandlingDraft } from '../lib/manualHandlingState'
import type { MaterialRecord, WorkflowTask } from '../types'
import { HandlingInventoryPanel, HandlingOccupanciesPanel } from './HandlingInventoryPanel'
import type { HandlingSubmit } from './HandlingInventoryPanel'
import { ManualActionParameters, parameterDefaults, parseManualParameters } from './ManualActionParameters'
import { Button } from './ui'

const statusNames: Record<string, string> = {
  pending: '待派发', dispatched: '已派发', running: '运行中', succeeded: '成功', failed: '失败',
  cancel_requested: '请求停止中', canceled: '已取消', timeout: '超时',
}

function ManualResultReview({ job, snapshot, session, writable, submit }: { job: ManualActionJob; snapshot: StationSnapshot; session: HandlingSession; writable: boolean; submit: HandlingSubmit }) {
  const [open, setOpen] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [reason, setReason] = useState('')
  return <div>
    <Button disabled={!writable} onClick={() => setOpen((value) => !value)}>{open ? '收起核对' : '核对结果'}</Button>
    {open ? <form onSubmit={(event) => {
      event.preventDefault()
      if (!writable || !confirmed || !reason.trim() || !job.review_version) return
      void submit(`/manual-actions/${job.edge_command_uuid}/review`, {
        ...sessionFields(snapshot, session), session_id: session.session_id, expected_review_version: job.review_version, operator_confirmed: true, reason: reason.trim(),
      }, '核对手动动作结果')
    }}>
      <p>动作结果尚不能自动确认，请先检查现场。核对记录不会更改动作的实际执行结果。</p>
      <label className="form-field"><span>核对说明</span><textarea required maxLength={20000} disabled={!writable} value={reason} onChange={(event) => { setReason(event.target.value); setConfirmed(false) }} /></label>
      <label className="handling-check"><input type="checkbox" checked={confirmed} disabled={!writable} onChange={(event) => setConfirmed(event.target.checked)} />我已现场核对，确认动作已停止，并已检查执行结果及物料状态</label>
      <Button type="submit" disabled={!writable || !confirmed || !reason.trim() || !job.review_version}>保存核对记录</Button>
    </form> : null}
  </div>
}

function ManualJobRecord({ job, snapshot, session, writable, submit, task }: { job: ManualActionJob; snapshot: StationSnapshot; session: HandlingSession; writable: boolean; submit: HandlingSubmit; task?: WorkflowTask }) {
  const [details, setDetails] = useState(false)
  const query = useQuery({ queryKey: ['handling-manual-detail', job.edge_command_uuid, job.status], queryFn: ({ signal }) => loadManualActionDetail(job.edge_command_uuid, signal), enabled: details, retry: false })
  return <article className="handling-job">
    <div><strong>{job.action_name || job.control_data?.dispatch_payload?.action || '手动设备动作'}</strong><span className={`handling-job-status ${job.status}`}>{statusNames[job.status] || job.status}{job.result_reviewed ? ' · 结果已人工核对' : job.uncertainty_reason ? ' · 结果待人工核对' : ''}</span></div>
    <small>{job.device_id || job.control_data?.dispatch_payload?.device_id || '设备动作'} · Job {job.uuid.slice(0, 8)}</small>
    <div className="handling-inline-actions">
      <Button tone="ghost" onClick={() => setDetails((value) => !value)}>{details ? '收起记录' : '查看执行记录'}</Button>
      {task?.trace ? <a href={task.trace.url} target="_blank" rel="noreferrer">查看关联 Task Trace</a> : null}
      {!manualActionEnded(job) ? <Button tone="danger" disabled={!writable || job.status === 'cancel_requested'} onClick={() => { void submit(`/manual-actions/${job.edge_command_uuid}/stop`, { reason: '用户从人工处置面板请求停止动作' }, '停止手动动作') }}>停止动作</Button> : null}
    </div>
    {details ? <div className="handling-job-details"><p>关联 Task：<code>{job.meta_data.source_task_uuid}</code><br />来源 Job：<code>{job.meta_data.source_job_uuid}</code></p>{query.isError ? <p role="alert">{query.error.message}</p> : <pre>{query.data ? JSON.stringify({ parameters: query.data.execution_job.param, result: query.data.execution_job.return_info, error: query.data.execution_job.error_info, trace: query.data.execution_task.trace_context }, null, 2) : '正在读取执行记录…'}</pre>}</div> : null}
    {manualActionNeedsReview(job) ? <ManualResultReview key={job.review_version || job.status} job={job} snapshot={snapshot} session={session} writable={writable} submit={submit} /> : null}
  </article>
}

export function ManualActionHistory({ snapshot, session, tasks, writable, submit }: {
  snapshot: StationSnapshot; session: HandlingSession; tasks: WorkflowTask[]; writable: boolean; submit: HandlingSubmit
}) {
  const { jobs } = manualSessionActions(snapshot.manual_actions, session.session_id)
  return <section className="handling-history"><h3>本次手动动作记录 <small>{jobs.length}</small></h3>{jobs.length ? jobs.map((job) => <ManualJobRecord key={job.uuid} job={job} snapshot={snapshot} session={session} writable={writable} submit={submit} task={tasks.find((item) => item.uuid === job.meta_data.source_task_uuid)} />) : <p>尚未执行手动动作。执行记录会关联当前错误所属的 Task 和 Trace。</p>}</section>
}

export function ManualHandlingPanel({ snapshot, session, materials, tasks, writable, submit }: {
  snapshot: StationSnapshot; session: HandlingSession; materials: MaterialRecord[]; tasks: WorkflowTask[]; writable: boolean; submit: HandlingSubmit
}) {
  const [tab, setTab] = useState<'actions' | 'inventory' | 'occupancies'>('actions')
  const [savedDraft] = useState(() => loadManualHandlingDraft(session.session_id, session.summary || ''))
  const [deviceId, setDeviceId] = useState(savedDraft.deviceId)
  const [actionId, setActionId] = useState(savedDraft.actionId)
  const [drafts, setDrafts] = useState<Record<string, string>>(savedDraft.drafts)
  const [raw, setRaw] = useState(savedDraft.raw)
  const [asJson, setAsJson] = useState(savedDraft.asJson)
  const [inputError, setInputError] = useState('')
  const [summary, setSummary] = useState(savedDraft.summary)
  useEffect(() => {
    saveManualHandlingDraft(session.session_id, { deviceId, actionId, drafts, raw, asJson, summary })
  }, [session.session_id, deviceId, actionId, drafts, raw, asJson, summary])
  const templatesQuery = useQuery({ queryKey: ['action-templates'], queryFn: ({ signal }) => loadActionTemplates(signal), retry: false })
  const deviceTypes = new Set(templatesQuery.data?.map((item) => item.resourceTemplate.uuid))
  const devices = materials.filter((item) => item.sourceNodeId && item.resourceTemplateUuid && deviceTypes.has(item.resourceTemplateUuid))
  const actionsQuery = useQuery({ queryKey: ['handling-actions', session.session_id, deviceId], queryFn: ({ signal }) => loadManualDeviceActions(session.session_id, deviceId, signal), enabled: Boolean(deviceId), retry: false })
  const action = actionsQuery.data?.find((item) => item.action_id === actionId)
  const selectAction = (id: string) => {
    const selected = actionsQuery.data?.find((item) => item.action_id === id)
    setActionId(id); setInputError(''); setAsJson(false)
    setDrafts(selected ? parameterDefaults(selected) : {}); setRaw(JSON.stringify(selected?.defaults || {}, null, 2))
  }
  const execute = async () => {
    if (!action) return
    setInputError('')
    try {
      const parameters = parseManualParameters(action, drafts, raw, asJson)
      await submit(`/sessions/${session.session_id}/manual-actions`, {
        ...sessionFields(snapshot, session), device_id: deviceId, action_id: action.action_id,
        action_version: action.action_version, parameters,
      }, '手动动作')
    } catch (error) { setInputError(error instanceof Error ? error.message : '请检查动作参数') }
  }
  const { running, unresolved } = manualSessionActions(snapshot.manual_actions, session.session_id)
  const canComplete = writable && running === 0 && unresolved === 0
  return <div className="handling-manual">
    <p className="handling-note">普通工作流调度保持暂停。可选择工站内设备的全部动作，手动执行不检查逻辑锁条件。</p>
    <div className="handling-tabs" role="tablist" aria-label="人工处置操作">
      {([['actions', '设备单点动作'], ['inventory', '上下料登记'], ['occupancies', '逻辑占用']] as const).map(([id, label]) => <button type="button" role="tab" aria-selected={tab === id} key={id} onClick={() => setTab(id)}>{label}</button>)}
    </div>
    <div role="tabpanel" className="handling-tab-content">
      {tab === 'actions' ? <>
        <div className="handling-device-selects">
          <label className="form-field"><span>选择设备</span><select aria-label="选择设备" value={deviceId} disabled={!writable} onChange={(event) => { setDeviceId(event.target.value); setActionId(''); setInputError(''); setDrafts({}); setRaw('{}'); setAsJson(false) }}><option value="">请选择设备</option>{devices.map((item) => <option key={item.uuid} value={item.uuid}>{item.name} · {item.sourceNodeId}</option>)}</select></label>
          <label className="form-field"><span>选择动作</span><select aria-label="选择动作" value={actionId} disabled={!writable || !deviceId || actionsQuery.isFetching || actionsQuery.isError} onChange={(event) => selectAction(event.target.value)}><option value="">{actionsQuery.isFetching ? '正在读取动作…' : '请选择动作'}</option>{actionsQuery.data?.map((item) => <option key={item.action_id} value={item.action_id}>{item.display_name} · {item.name}</option>)}</select></label>
        </div>
        {templatesQuery.isError || actionsQuery.isError ? <p role="alert">动作目录读取失败：{templatesQuery.error?.message || actionsQuery.error?.message}<Button onClick={() => { void templatesQuery.refetch(); if (deviceId) void actionsQuery.refetch() }}>重新读取动作</Button></p> : null}
        {templatesQuery.isSuccess && !devices.length ? <p>当前没有已绑定动作模板的设备。</p> : null}
        {action ? <form onSubmit={(event) => { event.preventDefault(); void execute() }}>
          <label className="handling-check"><input type="checkbox" checked={asJson} disabled={!writable} onChange={(event) => {
            if (event.target.checked) {
              try { setRaw(JSON.stringify(parseManualParameters(action, drafts, raw), null, 2)) } catch { setRaw(JSON.stringify(action.defaults, null, 2)) }
            } else {
              try { setDrafts(parameterDefaults({ ...action, defaults: parseManualParameters(action, {}, raw, true) })) } catch { setDrafts(parameterDefaults(action)) }
            }
            setAsJson(event.target.checked)
          }} />使用完整 JSON 参数</label>
          <ManualActionParameters action={action} drafts={drafts} raw={raw} asJson={asJson} disabled={!writable} onDraft={(key, value) => setDrafts((current) => ({ ...current, [key]: value }))} onRaw={setRaw} />
          {inputError ? <p role="alert">{inputError}</p> : null}
          <Button type="submit" tone="primary" disabled={!writable || actionsQuery.isError || actionsQuery.isFetching}>执行单点动作</Button>
        </form> : null}
        <ManualActionHistory snapshot={snapshot} session={session} tasks={tasks} writable={writable} submit={submit} />
      </> : tab === 'inventory' ? <HandlingInventoryPanel snapshot={snapshot} session={session} writable={writable} submit={submit} />
        : <HandlingOccupanciesPanel snapshot={snapshot} session={session} materials={materials} writable={writable} submit={submit} />}
    </div>
    <section className="handling-complete">
      <label className="form-field"><span>现场处理说明</span><textarea rows={2} maxLength={20000} disabled={!writable} value={summary} onChange={(event) => setSummary(event.target.value)} placeholder="记录本次设备调整、上下料和现场检查情况" /></label>
      {running > 0 ? <p>还有 {running} 个手动动作未结束，请等待动作结束，或停止动作并确认结果后继续。</p> : null}
      {unresolved > 0 ? <p>还有 {unresolved} 个动作结果待人工核对，请在“设备单点动作”记录中完成现场核对后继续。</p> : null}
      <Button tone="primary" disabled={!canComplete} onClick={() => { if (canComplete) void submit(`/sessions/${session.session_id}/complete`, { ...sessionFields(snapshot, session), summary }, '完成手动处理') }}>处理完成，返回重试／取消选择</Button>
    </section>
  </div>
}
