import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertCircle, ExternalLink, Pause, RefreshCw, X } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { decisionIntent, loadHandlingCommand, sessionFields, versionFields } from '../lib/stationErrorClient'
import type { ControlCommandSummary, StationError, StationSnapshot } from '../lib/stationErrorClient'
import { manualSessionActions } from '../lib/manualHandlingState'
import type { useStationErrorHandling } from '../hooks/useStationErrorHandling'
import type { MaterialRecord, WorkflowTask } from '../types'
import { Button } from './ui'
import { ManualActionHistory, ManualHandlingPanel } from './ManualHandlingPanel'
import type { HandlingSubmit } from './HandlingInventoryPanel'
import './StationErrorHandling.css'

function PendingCommand({ command, writable, submit }: { command: ControlCommandSummary; writable: boolean; submit: HandlingSubmit }) {
  const query = useQuery({ queryKey: ['handling-command', command.command_uuid], queryFn: ({ signal }) => loadHandlingCommand(command.command_uuid, signal), refetchInterval: 2000, retry: false })
  return <div className="handling-pending-command"><div><strong>操作已受理，等待应用</strong><small>{query.data?.result.error || '尚未确认应用完成，可以继续应用同一条命令。'}</small><code>{command.command_uuid}</code>{query.isError ? <small role="alert">{query.error.message}</small> : null}</div><Button disabled={!writable} onClick={() => { void submit(`/control-commands/${command.command_uuid}/apply`, {}, '继续应用原操作') }}>继续应用</Button></div>
}

function DecisionPanel({ snapshot, error, writable, submit }: {
  snapshot: StationSnapshot; error?: StationError; writable: boolean; submit: HandlingSubmit
}) {
  const [confirmed, setConfirmed] = useState(false)
  const [reason, setReason] = useState('')
  const session = snapshot.active_session
  const anotherSession = Boolean(session && session.decision_id !== error?.decision_id)
  const deciding = error?.stage === 'DECISION_REQUIRED' && !anotherSession
  return <section className="handling-decision">
    {error?.stage === 'APPLYING' ? <p>当前选择已受理，正在应用。请等待处理结果。</p> : null}
    {anotherSession ? <p>另一条错误正在人工处置，请先完成该会话。</p> : null}
    {deciding || !error ? <>
      {session?.decision_id === error?.decision_id && session ? <p className="handling-note">现场处理已完成，调度继续暂停。请选择重试当前节点或取消当前任务。</p> : null}
      {error ? <label className="form-field"><span>处理说明（选填）</span><textarea rows={2} value={reason} disabled={!writable} onChange={(event) => setReason(event.target.value)} /></label> : <p>当前错误已处理。确认现场状态后，可恢复工作流调度。</p>}
      <label className="handling-check handling-resume-confirm"><input type="checkbox" checked={confirmed} disabled={!writable} onChange={(event) => setConfirmed(event.target.checked)} />我已核对现场，确认可以恢复工站工作流调度</label>
      <p className="handling-decision-hint">{confirmed ? '本次处理应用后恢复调度；其他待处理任务仍等待人工决定，新错误会再次暂停工站。' : snapshot.mode === 'PAUSED' ? '未勾选时，重试或取消的选择会保留，工站继续暂停，可稍后确认恢复。' : '工站当前正在调度，其他错误任务仍等待各自的人工决定。'}</p>
      <div className="handling-decision-buttons">
        {error ? <>
          <Button tone="danger" disabled={!writable} onClick={() => { const request = decisionIntent(snapshot, error, 'cancel_task', confirmed, reason); void submit(request.path, request.body, '取消当前任务') }}>取消当前任务</Button>
          <Button tone="primary" icon={<RefreshCw size={16} />} disabled={!writable} onClick={() => { const request = decisionIntent(snapshot, error, 'retry_current_node', confirmed, reason); void submit(request.path, request.body, '重试当前节点') }}>重试当前节点</Button>
          {session ? <Button disabled={!writable} onClick={() => { void submit(`/sessions/${session.session_id}/return`, sessionFields(snapshot, session), '返回手动处理') }}>返回手动处理</Button>
            : <Button disabled={!writable} onClick={() => { const request = decisionIntent(snapshot, error, 'enter_manual_handling', false, reason); void submit(request.path, request.body, '进入手动处理') }}>进入手动处理</Button>}
        </> : <Button tone="primary" disabled={!writable || !confirmed || snapshot.mode !== 'PAUSED'} onClick={() => { void submit('/resume', { ...versionFields(snapshot), observed_error_epoch: snapshot.error_epoch, operator_confirmed: true, confirm_snapshot_id: snapshot.snapshot_id }, '恢复工站调度') }}>恢复工站调度</Button>}
      </div>
    </> : null}
  </section>
}

export function StationErrorHandling({ controller, materials, tasks, openRequest }: {
  controller: ReturnType<typeof useStationErrorHandling>
  materials: MaterialRecord[]
  tasks: WorkflowTask[]
  openRequest?: { taskUuid: string; sequence: number }
}) {
  const { snapshot, writable, pending, submit, refresh, readError, error: commandError, unconfirmed } = controller
  const [open, setOpen] = useState(Boolean(unconfirmed))
  const [decisionId, setDecisionId] = useState('')
  const seen = useRef(new Set<string>())
  const dialogRef = useRef<HTMLElement>(null)
  const pendingRef = useRef(pending)
  pendingRef.current = pending
  const lastOpenRequest = useRef<typeof openRequest>(undefined)
  const close = useCallback(() => { if (!pendingRef.current) setOpen(false) }, [])

  useEffect(() => {
    if (!snapshot) return
    const fresh = snapshot.errors.filter((item) => !seen.current.has(`${item.decision_id}:${item.stage}`))
    snapshot.errors.forEach((item) => seen.current.add(`${item.decision_id}:${item.stage}`))
    if (fresh.length) {
      setDecisionId(snapshot.active_session?.decision_id || fresh[0].decision_id)
      setOpen(true)
    }
  }, [snapshot])
  useEffect(() => {
    if (!openRequest || lastOpenRequest.current === openRequest) return
    lastOpenRequest.current = openRequest
    setDecisionId(snapshot?.errors.find((item) => item.source_task_uuid === openRequest.taskUuid)?.decision_id || '')
    setOpen(true)
    // 点击请求变化时定位一次，轮询不能覆盖用户正在查看的错误。
  }, [openRequest, snapshot])
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    dialogRef.current?.focus()
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close()
      if (event.key !== 'Tab' || !dialogRef.current) return
      const elements = Array.from(dialogRef.current.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]'))
      const first = elements[0]; const last = elements[elements.length - 1]
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialogRef.current)) { event.preventDefault(); first?.focus() }
    }
    document.addEventListener('keydown', keydown)
    return () => { document.removeEventListener('keydown', keydown); previous?.focus() }
  }, [open, close])

  const errors = snapshot?.errors || []
  const selected = errors.find((item) => item.decision_id === decisionId) || errors.find((item) => item.decision_id === snapshot?.active_session?.decision_id) || errors[0]
  const sourceTask = tasks.find((task) => task.uuid === selected?.source_task_uuid)
  const sourceNode = sourceTask?.nodes.find((node) => node.job?.uuid === selected?.source_job_uuid || node.uuid === selected?.meta_data.source_node_id)
  const session = snapshot?.active_session
  const manualActions = manualSessionActions(snapshot?.manual_actions || [], session?.session_id)
  const applyingSession = session?.decision_id === selected?.decision_id && selected?.stage === 'APPLYING'
  const manual = session?.stage === 'MANUAL_HANDLING' && session.decision_id === selected?.decision_id && !applyingSession
  const reviewWhileApplying = applyingSession && (manualActions.unresolved > 0 || manualActions.running > 0)
  const commands = snapshot?.control_commands.filter((item) => item.needs_apply) || []
  const needsAttention = snapshot?.mode === 'PAUSED' || errors.length > 0 || Boolean(unconfirmed) || commands.length > 0
  const showReadError = Boolean(readError)
  return <>
    {needsAttention || showReadError ? <div className="station-handling-banner" role="status">
      <Pause size={20} /><div><strong>{showReadError ? '错误处置状态暂不可用' : snapshot?.mode === 'PAUSED' ? '工站调度已暂停' : '还有节点错误待处理'}</strong><span>{showReadError ? '请刷新状态后操作。' : snapshot?.mode === 'PAUSED' ? `${errors.length} 条错误待处理；等待节点暂停派发，已开始的动作继续运行。` : `${errors.length} 条错误待处理；工站已由人工恢复调度，错误任务仍等待决定。`}</span>{manualActions.running || manualActions.unresolved ? <span>本次人工处置：{manualActions.running} 个手动动作未结束，{manualActions.unresolved} 个结果待核对。</span> : null}</div>
      <Button tone="primary" onClick={() => setOpen(true)}>{session?.stage === 'MANUAL_HANDLING' ? '继续手动处理' : '处理节点错误'}</Button>
    </div> : null}
    {open ? createPortal(<div className="handling-backdrop">
      <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="station-handling-title" className="station-handling-dialog" tabIndex={-1}>
        <header><div><span>工站错误处置</span><h2 id="station-handling-title">{manual ? '人工处置' : errors.length ? '节点执行出错' : needsAttention ? '确认恢复调度' : '错误处置已完成'}</h2><p>{snapshot?.mode === 'RUNNING' && !errors.length ? '工站已恢复调度。' : '暂停范围：当前工站全部工作流 Task。'}</p></div><button type="button" aria-label="关闭错误处置弹窗" disabled={pending} onClick={close}><X size={20} /></button></header>
        <div className="handling-dialog-body">
          {readError ? <div className="handling-alert" role="alert"><AlertCircle size={18} />错误处置状态读取失败，写操作已暂停。{readError.message}<Button onClick={() => { void refresh() }}>重新读取状态</Button></div> : null}
          {commandError ? <div className="handling-alert" role="alert">{commandError}</div> : null}
          {unconfirmed && !pending ? <div className="handling-alert" role="alert"><div><strong>{unconfirmed.label}：结果待确认</strong><p>将按原请求身份继续确认，不会创建第二条操作。</p></div><Button disabled={!controller.canConfirmRequest} onClick={() => { void controller.retryUnconfirmed() }}>确认原请求结果</Button></div> : null}
          {!snapshot ? <p>正在读取工站状态…</p> : <>
            {errors.length > 1 ? <label className="form-field"><span>待处理错误（{errors.length}）</span><select value={selected?.decision_id || ''} onChange={(event) => setDecisionId(event.target.value)}>{errors.map((item) => <option key={item.decision_id} value={item.decision_id}>{tasks.find((task) => task.uuid === item.source_task_uuid)?.workflowName || '任务'} · {item.source_task_uuid.slice(0, 8)} · {item.source_job_uuid.slice(0, 8)}</option>)}</select></label> : null}
            {selected ? <div className="handling-error-context"><strong>{sourceTask?.workflowName || '工作流任务'} · {sourceNode?.name || '错误节点'}</strong><p>{selected.meta_data.message || '节点执行失败，请检查现场。'}</p><details><summary>任务与执行记录</summary><code>Task：{selected.source_task_uuid}</code><code>Job：{selected.source_job_uuid}</code>{sourceTask?.trace ? <a href={sourceTask.trace.url} target="_blank" rel="noreferrer"><ExternalLink size={14} /> 查看 Task Trace</a> : null}</details></div> : null}
            {commands.map((command) => <PendingCommand key={command.command_uuid} command={command} writable={writable} submit={submit} />)}
            {reviewWhileApplying && session ? <>
              <p className="handling-note">已受理的处理选择正在等待手动动作结束及结果核对。完成核对后，请点击“继续应用”完成原操作。</p>
              <ManualActionHistory key={session.session_id} snapshot={snapshot} session={session} tasks={tasks} writable={writable} submit={submit} />
            </> : null}
            {manual && session ? <ManualHandlingPanel key={session.session_id} snapshot={snapshot} session={session} materials={materials} tasks={tasks} writable={writable} submit={submit} />
              : needsAttention ? <DecisionPanel key={`${snapshot.snapshot_id}:${selected?.decision_id}:${selected?.decision_version}:${session?.version}:${Boolean(readError)}`} snapshot={snapshot} error={selected} writable={writable} submit={submit} />
                : <p className="handling-success">处理完成。可返回任务列表查看节点和任务的最新执行结果。</p>}
            {session && !manual && session.decision_id !== selected?.decision_id ? <Button onClick={() => setDecisionId(session.decision_id)}>打开当前人工处置会话</Button> : null}
          </>}
        </div>
        <footer><span>{pending ? '正在提交，请等待…' : snapshot?.mode === 'PAUSED' ? '关闭弹窗后工站仍保持暂停，可从页面顶部重新打开。' : '以 Edge 返回的最新执行状态为准。'}</span><Button disabled={pending} onClick={close}>{needsAttention ? '稍后处理' : '关闭'}</Button></footer>
      </section>
    </div>, document.body) : null}
  </>
}
