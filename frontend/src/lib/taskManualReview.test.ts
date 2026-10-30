import { describe, expect, it } from 'vitest'
import { adaptTask } from './edgeClient'

const rawTask = { uuid: 'task-1', status: 'canceled', control_status: 'waiting_reconciliation', cleanup_status: 'none', execution_plan: { nodes: [{ uuid: 'node-1', kind: 'device_action', action_name: 'run_stirring' }] } }
const reviewedJob = {
  uuid: 'manual-job', workflow_node_uuid: 'node-1', status: 'canceled', execution_source: 'error_manual', uncertainty_reason: 'local_cancel_acceptance_unavailable',
  review_version: 'v1', result_reviewed: true, manual_review: { review_version: 'v1', reason: '现场已确认', reviewed_at: '2026-09-11T05:00:00Z', operator_id: 'user-1' },
}

describe('人工核对后的任务与节点展示', () => {
  it('保留真实取消结果，只有后端确认无其他待处理事项才收起任务级提示', () => {
    const stillBlocked = adaptTask(rawTask, [reviewedJob])
    expect(stillBlocked.status).toBe('execution_unknown')
    expect(stillBlocked.nodes[0]).toMatchObject({ status: 'canceled', job: { executionStatus: 'canceled', resultReviewed: true, manualReview: { reason: '现场已确认' } } })
    const resolved = adaptTask({ ...rawTask, manual_review_attention_resolved: true }, [reviewedJob])
    expect(resolved).toMatchObject({ status: 'canceled', executionStatus: 'canceled', controlStatus: 'waiting_reconciliation' })
    expect(resolved.nodes[0].job?.uncertaintyReason).toBe('local_cancel_acceptance_unavailable')
  })

  it('新证据与旧核对版本不一致时重新显示待核对', () => {
    const task = adaptTask({ ...rawTask, manual_review_attention_resolved: true }, [{ ...reviewedJob, review_version: 'v2' }])
    expect(task.status).toBe('execution_unknown')
    expect(task.nodes[0]).toMatchObject({ status: 'attention', job: { resultReviewed: false } })
  })

  it.each(['running', 'dispatched', 'cancel_requested'])('核对字段不覆盖仍然 %s 的实际动作状态', (status) => {
    const task = adaptTask({ ...rawTask, manual_review_attention_resolved: true }, [{ ...reviewedJob, status }])
    expect(task.status).toBe('execution_unknown')
    expect(task.nodes[0].status).toBe(status === 'cancel_requested' ? 'canceling' : 'running')
    expect(task.nodes[0].job?.resultReviewed).toBe(false)
  })

  it('普通工作流动作不能凭人工核对字段隐藏不确定性', () => {
    const task = adaptTask({ ...rawTask, manual_review_attention_resolved: true }, [{ ...reviewedJob, execution_source: 'workflow' }])
    expect(task.status).toBe('execution_unknown')
    expect(task.nodes[0].status).toBe('attention')
    expect(task.nodes[0].manualReviewJobs).toEqual([])
  })

  it('独立的资源清理和人工干预提示继续保留', () => {
    for (const extra of [{ cleanup_status: 'requires_attention' }, { attention_reason: 'resource_lock_pending' }, { control_status: 'waiting_intervention' }]) {
      expect(adaptTask({ ...rawTask, ...extra }, [reviewedJob]).status).toBe('intervention_required')
    }
  })

  it('其他Job未核对或执行前确认待批准时不清除任务级待处理状态', () => {
    const task = { ...rawTask, manual_review_attention_resolved: true, execution_plan: { nodes: [...rawTask.execution_plan.nodes, { uuid: 'node-2', kind: 'device_action', action_name: 'other_action' }] } }
    const unknown = adaptTask(task, [reviewedJob, { ...reviewedJob, uuid: 'other-job', workflow_node_uuid: 'node-2', result_reviewed: false }])
    expect(unknown.status).toBe('execution_unknown')
    expect(unknown.nodes[1].status).toBe('attention')
    const confirming = adaptTask(task, [reviewedJob, { uuid: 'confirmation-job', workflow_node_uuid: 'node-2', status: 'pending', manual_confirmation: { status: 'pending' } }])
    expect(confirming.status).toBe('execution_unknown')
    expect(confirming.nodes[1].status).toBe('attention')
  })

  it('后续普通重试不覆盖历史manual attempt的核对入口', () => {
    const old = { ...reviewedJob, result_reviewed: false, attempt: 1 }
    const latest = { uuid: 'latest-job', workflow_node_uuid: 'node-1', execution_source: 'workflow', status: 'succeeded', attempt: 2 }
    const task = adaptTask(rawTask, [old, latest])
    expect(task.nodes[0].job?.uuid).toBe('latest-job')
    expect(task.nodes[0].manualReviewJobs?.map((job) => job.uuid)).toEqual(['manual-job'])
    expect(task.status).toBe('execution_unknown')
  })
})
