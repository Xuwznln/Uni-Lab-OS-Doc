import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { EDGE_API_BASE } from '../lib/edgeClient'
import {
  HandlingApiError, handlingErrorMessage, loadHistoricalManualReview, STATION_QUERY_KEY, submitHistoricalManualReview,
} from '../lib/stationErrorClient'
import type { HistoricalManualReviewIntent } from '../lib/stationErrorClient'
import type { TaskNodeJobEvidence } from '../types'
import { Button } from './ui'

const executionLabels: Record<string, string> = { succeeded: '已完成', failed: '失败', canceled: '已取消', timeout: '超时', running: '运行中', dispatched: '已派发', cancel_requested: '停止请求中' }
const terminal = new Set(['succeeded', 'failed', 'canceled', 'timeout'])
const storageKey = (jobUuid: string) => `unilab:manual-job-review:${EDGE_API_BASE}:${jobUuid}`

function restoreIntent(jobUuid: string): HistoricalManualReviewIntent | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey(jobUuid)) || 'null')
    if (value && typeof value.key === 'string' && typeof value.body?.expected_review_version === 'string'
      && typeof value.body.reason === 'string' && value.body.operator_confirmed === true) return value
  } catch { /* 不恢复损坏的请求。 */ }
  return null
}

function ReviewForm({ writable, onSubmit }: { writable: boolean; onSubmit: (reason: string) => void }) {
  const [reason, setReason] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  return <form onSubmit={(event) => {
    event.preventDefault()
    if (writable && confirmed && reason.trim()) onSubmit(reason.trim())
  }}>
    <label className="form-field"><span>核对说明</span><textarea required rows={3} maxLength={20000} disabled={!writable} value={reason} onChange={(event) => { setReason(event.target.value); setConfirmed(false) }} /></label>
    <label className="historical-review-confirm"><input type="checkbox" checked={confirmed} disabled={!writable} onChange={(event) => setConfirmed(event.target.checked)} /><span>我已现场核对，确认动作已停止，并已检查执行结果及物料状态</span></label>
    <Button type="submit" tone="primary" disabled={!writable || !confirmed || !reason.trim()}>保存核对记录</Button>
  </form>
}

export function HistoricalManualActionReview({ job, taskUuid, taskNeedsAttention, connected, onNotify }: {
  job: TaskNodeJobEvidence; taskUuid: string; taskNeedsAttention: boolean; connected: boolean; onNotify: (message: string) => void
}) {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['historical-manual-review', EDGE_API_BASE, job.uuid],
    queryFn: ({ signal }) => loadHistoricalManualReview(job.uuid, signal),
    enabled: connected && terminal.has(job.executionStatus || ''), retry: false, refetchInterval: 3000,
  })
  const [open, setOpen] = useState(false)
  const [intent, setIntent] = useState<HistoricalManualReviewIntent | null>(() => restoreIntent(job.uuid))
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [formRevision, setFormRevision] = useState(0)
  const inFlight = useRef(false)
  const context = query.data
  const versionMismatch = Boolean(context && job.reviewVersion && context.review_version !== job.reviewVersion)
  const refreshReview = query.refetch
  useEffect(() => {
    if (!connected || !versionMismatch) return
    // 任务详情和核对上下文任一先收到迟到结果，都先同步双方证据再允许确认。
    void refreshReview({ cancelRefetch: false })
    void queryClient.invalidateQueries({ queryKey: ['workflow-task-detail', taskUuid] }, { cancelRefetch: false })
    void queryClient.invalidateQueries({ queryKey: ['edge-tasks'] }, { cancelRefetch: false })
  }, [connected, versionMismatch, job.reviewVersion, context?.review_version, queryClient, taskUuid, refreshReview])
  const ended = terminal.has(job.executionStatus || '') && terminal.has(context?.job.status || job.executionStatus || '')
  const reviewed = ended && !versionMismatch && Boolean(context?.result_reviewed && context.manual_review?.review_version === context.review_version)
  const audit = context?.manual_review
  const executionStatus = terminal.has(job.executionStatus || '') ? context?.job.status || job.executionStatus || '' : job.executionStatus || ''
  const canWrite = connected && !versionMismatch && !query.isError && !query.isFetching && Boolean(context) && ended && !pending

  const execute = async (request: HistoricalManualReviewIntent) => {
    if (!canWrite || inFlight.current) return
    inFlight.current = true; setPending(true); setError('')
    try {
      // 保存的是用户已经提交的请求身份，不会恢复表单的人工确认勾选。
      sessionStorage.setItem(storageKey(job.uuid), JSON.stringify(request))
    } catch {
      setError('浏览器无法保存本次请求，请启用会话存储后再提交。')
      inFlight.current = false; setPending(false); return
    }
    setIntent(request)
    try {
      const result = await submitHistoricalManualReview(job.uuid, request)
      if (result.status !== 'APPLIED') throw new Error('核对请求尚未确认应用')
      sessionStorage.removeItem(storageKey(job.uuid)); setIntent(null)
      // 幂等重放可能返回旧的已应用记录；重新读取当前证据，不据此宣布新结果已核对。
      await Promise.all([
        query.refetch(),
        queryClient.invalidateQueries({ queryKey: ['edge-tasks'] }, { cancelRefetch: false }),
        queryClient.invalidateQueries({ queryKey: ['workflow-task-detail', taskUuid] }),
        queryClient.invalidateQueries({ queryKey: STATION_QUERY_KEY }, { cancelRefetch: false }),
      ])
      onNotify('核对请求已应用，当前核对状态以最新执行结果为准。')
    } catch (cause) {
      if (cause instanceof HandlingApiError && cause.definitive) {
        sessionStorage.removeItem(storageKey(job.uuid)); setIntent(null)
        setError(handlingErrorMessage(cause)); await query.refetch()
      } else setError('提交结果尚未确认，请使用“确认原核对请求结果”继续确认，避免重复记录。')
    } finally {
      setFormRevision((value) => value + 1)
      inFlight.current = false; setPending(false)
    }
  }

  return <section className="historical-manual-review" aria-label={`手动动作结果核对 ${job.uuid}`}>
    <header><div><strong>{!ended ? '动作尚未结束' : versionMismatch ? '核对状态更新中' : reviewed ? '结果已核对' : '结果待核对'}</strong><small>手动动作 · 第 {job.attempt || 1} 次尝试 · Job {job.uuid}</small></div><span>执行结果：{executionLabels[executionStatus] || executionStatus || '未知'}</span></header>
    <p>核对仅记录现场检查结论，保留原执行结果；资源占用需在执行锁面板单独处理。</p>
    {reviewed && taskNeedsAttention ? <p>该动作结果已核对；任务仍显示待处理，请检查任务状态和执行锁。</p> : null}
    {!connected ? <p role="alert">Edge 未连接，当前不可提交核对。</p> : null}
    {query.isError ? <p role="alert">核对状态读取失败：{handlingErrorMessage(query.error)} 以下如有记录，仅代表上次读取结果。</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {versionMismatch ? <p role="status">动作结果版本已变化，正在同步最新记录；请核对更新后的结果。</p> : null}
    {context ? <details className="historical-review-evidence"><summary>查看本次动作执行记录</summary><small>Job {context.job.uuid} · {context.job.started_at || '未开始'} → {context.job.finished_at || '未结束'}</small>
      {([['实际参数', context.job.param], ['运行结果', context.job.return_info], ['实时反馈', context.job.feedback_data], ['错误信息', context.job.error_info]] as const).map(([label, value]) => <div key={label}><strong>{label}</strong><pre>{JSON.stringify(value ?? {}, null, 2)}</pre></div>)}
    </details> : null}
    {audit ? <div className="historical-review-audit"><strong>{reviewed ? '核对记录' : '上次核对记录（当前结果需重新核对）'}</strong><p>{audit.reason}</p><small>核对时间：{audit.reviewed_at} · 操作人：{audit.operator_id}</small></div> : null}
    {intent ? <div className="historical-review-pending"><p>原核对请求结果待确认：{intent.body.reason}</p><Button disabled={!canWrite} onClick={() => { void execute(intent) }}>确认原核对请求结果</Button></div> : null}
    {ended ? <>
      {!reviewed ? <Button disabled={!canWrite && !open} onClick={() => setOpen((value) => !value)}>{open ? '收起核对' : '核对结果'}</Button> : null}
      {open && !reviewed ? <ReviewForm key={`${job.reviewVersion}:${context?.review_version}:${formRevision}:${connected}:${query.isError}`} writable={canWrite && !intent} onSubmit={(reason) => {
        if (!context || intent) return
        void execute({ key: crypto.randomUUID(), body: { expected_review_version: context.review_version, reason, operator_confirmed: true } })
      }} /> : null}
    </> : <p>动作尚未结束，请等待动作结束后再核对结果。</p>}
    <Button tone="ghost" disabled={!connected || query.isFetching || pending} onClick={() => { setFormRevision((value) => value + 1); void query.refetch() }}>刷新核对状态</Button>
  </section>
}
