import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { currentTimeCandidate, loadTimeSchedule, mergePartialTimeCandidate } from '../lib/timeScheduleClient'
import { formatTimeScheduleReason } from '../lib/timeScheduleFormatting'
import type { WorkflowTask } from '../types'
import { Button, Panel, PanelHeader } from './ui'
import './TimeSchedulePanel.css'

const statusLabels: Record<string, string> = {
  FEASIBLE: '可行候选（未证最优）', OPTIMAL: '当前模型求解完成',
  UNKNOWN: '预算内未得到可用解', INFEASIBLE: '当前模型无可行解',
  MODEL_INVALID: '模型无效', ERROR: '求解异常',
}

function plannedTime(origin: number, offset: number): string {
  const value = new Date(origin * 1000 + offset)
  return Number.isNaN(value.getTime()) ? '时间无效' : value.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function TimeSchedulePanel({ connected, tasks }: { connected: boolean; tasks: WorkflowTask[] }) {
  const query = useQuery({
    queryKey: ['scheduler-time-plan'], queryFn: ({ signal }) => loadTimeSchedule(signal),
    enabled: connected, refetchInterval: connected ? 2_000 : false, retry: false,
  })
  const snapshot = query.data
  const latest = snapshot?.latest
  const liveCandidate = currentTimeCandidate(snapshot, connected, query.isError)
  const partialTaskIds = snapshot?.input.partial_tasks || []
  const [lastCandidate, setLastCandidate] = useState<typeof liveCandidate>(null)
  useEffect(() => {
    if (!connected) setLastCandidate(null)
    else if (liveCandidate) setLastCandidate((previous) => mergePartialTimeCandidate(liveCandidate, previous, partialTaskIds))
  }, [connected, liveCandidate, partialTaskIds])
  const mergedLiveCandidate = mergePartialTimeCandidate(liveCandidate, lastCandidate, partialTaskIds)
  const candidate = mergedLiveCandidate || (connected && !query.isError && !snapshot?.input_error
    ? lastCandidate : null)
  const current = Boolean(liveCandidate)
  const candidateStale = Boolean(candidate && candidate !== liveCandidate)
  const taskName = (id: string): string => tasks.find((task) => task.uuid === id)?.workflowName || id
  const nodeName = (taskId: string, nodeId: string): string => tasks.find((task) => task.uuid === taskId)?.nodes.find((node) => node.uuid === nodeId)?.name || nodeId
  const completionKindLabel = (kind: string): string => kind === 'information' ? '信息前缀'
    : kind === 'partial_scope' ? '范围受限'
      : kind === 'task_completion' ? '任务完成' : kind
  const state = !connected ? '连接不可用'
    : query.isError ? '读取失败'
      : !snapshot ? '正在读取'
        : !snapshot.enabled ? '未启用时间规划'
          : snapshot.input_error ? '规划输入待处理'
            : !current ? snapshot.active_solves ? '正在重排' : '等待新版本候选'
              : latest && !latest.validated && ['FEASIBLE', 'OPTIMAL'].includes(latest.status) ? '候选验证未通过'
                : latest ? statusLabels[latest.status] || latest.status : '尚无候选'

  return <Panel className="time-schedule-panel">
    <PanelHeader title="时间排程候选" description="每 2 秒刷新 · 全局候选覆盖多个任务" action={
      <Button icon={<RefreshCw size={14} />} disabled={!connected || query.isFetching} onClick={() => { void query.refetch() }}>刷新计划</Button>
    } />
    <div className="time-schedule-summary" role="status">
      <strong>{snapshot?.algorithm === 'stable-ready-order' ? '稳定就绪排序' : snapshot?.algorithm || '算法待读取'}</strong>
      <span>{state}</span>
      {candidate ? <code title={candidate.revision}>版本 {candidate.revision.slice(0, 12)}{candidateStale ? '（上次候选）' : ''}</code> : null}
      {snapshot?.input.partial_tasks.length ? <span>{snapshot.input.partial_tasks.length} 个任务仅规划已知前缀</span> : null}
    </div>
    <p className="time-schedule-note">预计时间是当前候选，不是实际起止，也不是设备启动授权；实际执行以任务回报为准。当前不保证硬时限。</p>
    {query.isError ? <p className="time-schedule-error">{query.error.message}。未展示上次候选时间，避免误用。</p> : null}
    {connected && !query.isError && snapshot?.input_error ? <p className="time-schedule-error">{snapshot.input_error}</p> : null}
    {current && latest?.error ? <p className="time-schedule-error">{latest.error}</p> : null}
    {candidate?.completion_targets.length ? <details className="time-schedule-targets" open>
      <summary>候选完成语义（{candidate.completion_targets.length}）</summary>
      <ul>{candidate.completion_targets.map((target) => <li key={`${target.task_id}:${target.prefix_id}:${target.kind}`}>
        <span>{taskName(target.task_id)}</span><strong>{completionKindLabel(target.kind)}</strong>
        {target.node_keys.length ? <small>{target.node_keys.map(([taskId, nodeId]) => nodeName(taskId, nodeId)).join('、')}</small> : <small>当前没有可展示的完成节点</small>}
      </li>)}</ul>
    </details> : null}
    {candidate?.nodes.length ? <div className="time-schedule-table-wrap">
      <table className="time-schedule-table">
        <thead><tr><th>任务 / 节点</th><th>设备</th><th>预计开始</th><th>预计结束</th><th>区间含义</th></tr></thead>
        <tbody>{[...candidate.nodes].sort((left, right) => left.start_ms - right.start_ms).map((node) => <tr key={JSON.stringify(node.key)}>
          <td><strong title={node.key[0]}>{taskName(node.key[0])}</strong><span title={node.key[1]}>{nodeName(...node.key)}</span><code>{node.key[0].slice(0, 8)}</code></td>
          <td>{node.device_id}</td><td>{plannedTime(candidate.time_origin, node.start_ms)}</td><td>{plannedTime(candidate.time_origin, node.end_ms)}</td>
          <td>{node.parameters_final === false ? '参数待确认（仅展示）' : node.fixed ? '在途剩余区间' : '未来候选区间'}</td>
        </tr>)}</tbody>
      </table>
    </div> : connected && !query.isError && snapshot?.enabled ? <p className="time-schedule-empty">当前没有可展示的同版本已验证时间区间。</p> : null}
    {connected && !query.isError && !snapshot?.input_error && snapshot?.input.blocked.length ? <details className="time-schedule-blocked" open>
      <summary>待定原因（{snapshot.input.blocked.length}）</summary>
      <ul>{snapshot.input.blocked.map(([taskId, nodeId, reason], index) => <li key={`${taskId}:${nodeId}:${index}`}>
        <span title={`${taskId} / ${nodeId}`}>{taskName(taskId)} · {nodeName(taskId, nodeId)}</span><strong title={reason}>{formatTimeScheduleReason(reason)}</strong>
      </li>)}</ul>
    </details> : null}
  </Panel>
}
