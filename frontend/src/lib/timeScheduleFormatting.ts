const reasonLabels: Record<string, string> = {
  condition_unresolved: '条件结果待定',
  loop_tail_unknown: '循环后续待定',
  human_confirmation_required: '等待人工确认',
  workflow_dispatch_paused: '任务派发已暂停',
  predecessor_not_successful: '等待上游节点完成',
  dependency_unavailable: '依赖尚未确定',
  task_control_blocked: '运行控制暂不允许派发',
  planning_scope_budget: '等待进入本轮规划范围',
  actual_completion_overdue_unknown: '已超过预计时长，实际完成待核实',
  resource_or_dependency_completion_unknown: '资源释放或依赖完成时间待定',
  new_open_scope_deferred: '先等待另一连续占用区间完成',
  'parent ret value missing': '等待上游节点返回结果',
}

export function formatTimeScheduleReason(reason: string): string {
  return reason.split(';').map((part) => {
    const value = part.trim()
    return reasonLabels[value.split(':')[0]] || value
  }).filter(Boolean).join('；')
}
