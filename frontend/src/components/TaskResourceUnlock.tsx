import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { AlertCircle, LoaderCircle, ShieldCheck, X } from 'lucide-react'
import {
  loadTaskResourceUnlockIntent, saveTaskResourceUnlockIntent, submitTaskResourceUnlock,
  TaskResourceUnlockError, taskResourceUnlockReason,
} from '../lib/taskResourceUnlockClient'
import type { TaskResourceUnlockIntent } from '../lib/taskResourceUnlockClient'
import type { WorkflowTaskExecutionLockSnapshot } from '../types'
import { Button } from './ui'

const abnormalTerminal = new Set(['failed', 'canceled', 'timeout'])

/** 按整个 Task 处置资源；原请求的确认入口独立于单 Job 锁的释放资格。 */
export function TaskResourceUnlock({ taskUuid, snapshot, connected, snapshotUnavailable, busy, onBusyChange, onNotify, cleanupStatus, manualReviewAttentionResolved }: {
  taskUuid: string; snapshot?: WorkflowTaskExecutionLockSnapshot; connected: boolean;
  snapshotUnavailable: boolean; busy: boolean; onBusyChange: (busy: boolean) => void; onNotify: (message: string) => void;
  cleanupStatus?: string; manualReviewAttentionResolved?: boolean
}) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [intent, setIntent] = useState<TaskResourceUnlockIntent | null>(() => loadTaskResourceUnlockIntent(taskUuid))
  const [pending, setPending] = useState(false)
  const [message, setMessage] = useState('')
  const [succeeded, setSucceeded] = useState(false)
  const inFlight = useRef(false)
  const terminal = abnormalTerminal.has(snapshot?.taskStatus || '')
  const writable = connected && !snapshotUnavailable && snapshot?.workflowTaskUuid === taskUuid && !busy && !pending
  const hasResources = Boolean(snapshot && (snapshot.locks.length > 0 || snapshot.activeDeviceTenancyCount > 0))
  // 清理交接残留可能已经没有活动锁；仍须走同一人工确认和后端任务级命令。
  const cleanupOnly = Boolean(snapshot && !hasResources && cleanupStatus === 'requires_attention' && !manualReviewAttentionResolved)
  const canStart = writable && terminal && (hasResources || cleanupOnly) && !intent && !succeeded
  const actionLabel = cleanupOnly ? '清理交接残留' : '解除任务全部锁'
  const dialogTitle = cleanupOnly ? '清理任务交接残留' : '解除任务全部锁'

  useEffect(() => { setConfirmed(false) }, [connected, snapshot?.taskStatus, snapshotUnavailable])
  useEffect(() => { onBusyChange(open || pending || Boolean(intent)) }, [open, pending, intent, onBusyChange])

  const refresh = () => Promise.allSettled([
    queryClient.invalidateQueries({ queryKey: ['workflow-task-execution-locks', taskUuid] }, { cancelRefetch: false }),
    queryClient.invalidateQueries({ queryKey: ['workflow-task-detail', taskUuid] }, { cancelRefetch: false }),
    queryClient.invalidateQueries({ queryKey: ['edge-tasks'] }, { cancelRefetch: false }),
    queryClient.invalidateQueries({ queryKey: ['edge-snapshot'] }, { cancelRefetch: false }),
  ])

  const execute = async (request: TaskResourceUnlockIntent) => {
    if (!connected || busy || inFlight.current || request.taskUuid !== taskUuid) return
    inFlight.current = true; setPending(true); setConfirmed(false); setMessage(''); setSucceeded(false)
    try {
      // 持久化已经提交的意图，以便网络中断后查询原命令；不恢复人工确认表单。
      saveTaskResourceUnlockIntent(taskUuid, request)
    } catch {
      setMessage('浏览器无法保存本次请求，请启用会话存储后再提交。')
      inFlight.current = false; setPending(false); return
    }
    setIntent(request)
    try {
      const command = await submitTaskResourceUnlock(request)
      if (command.status === 'pending') {
        const notice = '解锁命令仍在处理中，尚未确认资源释放；请稍后确认原解锁请求结果。'
        setMessage(notice); onNotify(notice)
      } else {
        saveTaskResourceUnlockIntent(taskUuid, null); setIntent(null)
        if (command.status === 'succeeded') {
          const count = command.result?.released?.execution_locks
          const notice = cleanupOnly
            ? '任务交接残留已清理，清理状态已更新。'
            : `任务资源解锁已完成${typeof count === 'number' ? `，已释放 ${count} 把执行锁` : ''}。锁状态以刷新后的列表为准。`
          setSucceeded(true); setReason(''); setMessage(notice); onNotify(notice)
        } else {
          const notice = `任务解锁被拒绝：${taskResourceUnlockReason(command.result?.reason)}`
          setMessage(notice); onNotify(notice)
        }
      }
    } catch (cause) {
      const definitive = cause instanceof TaskResourceUnlockError && cause.definitive
      if (definitive) { saveTaskResourceUnlockIntent(taskUuid, null); setIntent(null) }
      const detail = cause instanceof Error ? cause.message : '未知错误'
      const notice = definitive ? `任务解锁请求失败：${detail}` : `提交结果尚未确认：${detail}。请确认原解锁请求结果，避免重复操作。`
      setMessage(notice); onNotify(notice)
    } finally {
      await refresh()
      inFlight.current = false; setPending(false)
    }
  }

  const close = () => { setOpen(false); setConfirmed(false); setReason('') }
  const result = message ? <div className={succeeded ? 'task-unlock-result' : 'task-lock-dialog-error'} role={succeeded ? 'status' : 'alert'}>{message}</div> : null
  const confirmOriginal = intent ? <div className="task-unlock-pending">
    <p>原解锁请求结果待确认。说明：{intent.body.description}</p>
    <Button disabled={!connected || busy || pending} onClick={() => { void execute(intent) }}>确认原解锁请求结果</Button>
  </div> : null

  return <section className="task-resource-unlock" aria-label="任务级资源解锁">
    <div className="task-resource-unlock-header"><div><strong>任务级解锁</strong><p>{cleanupOnly ? '执行锁已释放，但任务交接清理仍需现场确认。' : '连续区间锁需解除整个 Task 的资源占用；后端会重新检查是否可安全释放。'}</p></div>
      <Button tone="danger" icon={<ShieldCheck size={13} />} disabled={!canStart} onClick={() => { setReason(''); setConfirmed(false); setMessage(''); setSucceeded(false); setOpen(true) }}>{actionLabel}</Button>
    </div>
    {snapshot && !terminal ? <p>仅失败、已取消或超时的任务可发起任务级解锁；当前任务执行状态：{snapshot.taskStatus}。</p> : null}
    {!open ? <>{result}{confirmOriginal}</> : null}
    {open ? <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close() }}>
      <section className="task-lock-dialog" role="dialog" aria-modal="true" aria-labelledby="task-resource-unlock-title">
        <header><div><span>OPERATOR ACTION</span><h2 id="task-resource-unlock-title">{dialogTitle}</h2><p>Task {taskUuid}</p></div><button type="button" aria-label="关闭任务解锁对话框" onClick={close}><X size={18} /></button></header>
        <form onSubmit={(event) => {
          event.preventDefault()
          if (!canStart || !confirmed || !reason.trim()) return
          void execute({ taskUuid, body: { type: 'unlock_resources', idempotency_key: crypto.randomUUID(), description: reason.trim(), meta_data: { source: 'unilabos-frontend', confirmed_physical_safe: true } } })
        }}>
          <div className="task-lock-dialog-content">
            <div className="task-lock-dialog-warning"><AlertCircle size={16} /><span>{cleanupOnly ? '将清理此 Task 未完成的资源交接状态，不会重新执行机器人动作；请先确认整个任务涉及的设备均已停止、现场物料状态已核实。' : '将解除此 Task 的全部执行锁及相关资源占用，包含所有 Job 和连续区间。释放后等待任务可能继续运行，请先确认整个任务涉及的设备均已停止、现场物料状态已核实。'}</span></div>
            {!connected ? <p role="alert">Edge 未连接，当前不可提交任务解锁。</p> : null}
            {!terminal ? <p role="alert">任务状态已变化，当前不允许发起新的任务解锁。</p> : null}
            {result}{confirmOriginal}
            {!intent && !succeeded ? <>
              <label className="form-field"><span>任务解锁说明<em>必填</em></span><textarea required maxLength={2000} rows={4} disabled={!writable} value={reason} onChange={(event) => { setReason(event.target.value); setConfirmed(false) }} placeholder="说明设备停止、无在途动作和现场物料核验情况。" /></label>
              <label className="task-lock-confirmation"><input type="checkbox" checked={confirmed} disabled={!canStart} onChange={(event) => setConfirmed(event.target.checked)} /><span>我已现场确认此 Task 涉及的设备均已停止，物理现场安全，物料状态已核实，且不存在未上报的在途动作。</span></label>
            </> : null}
          </div>
          <footer><Button type="button" onClick={close}>关闭</Button>{!intent && !succeeded ? <Button type="submit" tone="danger" icon={pending ? <LoaderCircle className="spin" size={14} /> : <ShieldCheck size={14} />} disabled={!canStart || !reason.trim() || !confirmed}>确认{cleanupOnly ? '清理交接残留' : '解除任务全部锁'}</Button> : null}</footer>
        </form>
      </section>
    </div> : null}
  </section>
}
