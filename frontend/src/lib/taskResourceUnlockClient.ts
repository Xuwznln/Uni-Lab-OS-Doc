import { EDGE_API_BASE } from './edgeClient'

export interface TaskResourceUnlockIntent {
  taskUuid: string
  body: {
    type: 'unlock_resources'
    idempotency_key: string
    description: string
    meta_data: { source: 'unilabos-frontend'; confirmed_physical_safe: true }
  }
}

export interface TaskResourceUnlockCommand {
  uuid: string
  workflow_task_uuid: string
  type: 'unlock_resources'
  idempotency_key: string
  status: 'succeeded' | 'rejected' | 'pending'
  result?: { reason?: string; cleanup_status?: string; released?: Record<string, number> }
}

export class TaskResourceUnlockError extends Error {
  readonly definitive: boolean
  constructor(message: string, definitive = false) { super(message); this.definitive = definitive }
}

/** 提交或确认同一任务级解锁命令；HTTP 成功与业务命令成功分别处理。 */
export async function submitTaskResourceUnlock(intent: TaskResourceUnlockIntent): Promise<TaskResourceUnlockCommand> {
  const response = await fetch(`${EDGE_API_BASE}/workflow-tasks/${encodeURIComponent(intent.taskUuid)}/commands`, {
    method: 'POST', headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(intent.body), signal: AbortSignal.timeout(20_000),
  })
  const body = await response.json().catch(() => null)
  if (!response.ok || body?.code !== 0) {
    const message = body?.error?.msg || (typeof body?.detail === 'string' ? body.detail : '') || `任务解锁请求失败（HTTP ${response.status}）`
    // 服务端异常或无法解析的响应可能发生在命令受理后，必须保留原请求身份。
    const definitive = [400, 401, 403, 404, 405, 409, 422].includes(response.status)
    throw new TaskResourceUnlockError(message, definitive)
  }
  const command = body.data
  if (!command?.uuid || command.workflow_task_uuid !== intent.taskUuid || command.type !== 'unlock_resources'
    || command.idempotency_key !== intent.body.idempotency_key || !['succeeded', 'rejected', 'pending'].includes(command.status)) {
    throw new TaskResourceUnlockError('服务端未返回可确认的解锁命令状态')
  }
  return command
}

const reasonLabels: Record<string, string> = {
  task_is_not_abnormal_terminal: '任务尚未失败、取消或超时，不能解除任务资源。',
  scheduler_unavailable: '调度服务暂不可用，请恢复服务后再确认现场状态。',
}
export function taskResourceUnlockReason(reason?: string) { return reason ? reasonLabels[reason] || reason : '后端未提供拒绝原因，请刷新任务和锁状态。' }

const storageKey = (taskUuid: string) => `unilab:task-resource-unlock:${EDGE_API_BASE}:${taskUuid}`

export function loadTaskResourceUnlockIntent(taskUuid: string): TaskResourceUnlockIntent | null {
  try {
    const intent = JSON.parse(sessionStorage.getItem(storageKey(taskUuid)) || 'null')
    if (intent?.taskUuid === taskUuid && intent.body?.type === 'unlock_resources'
      && typeof intent.body.idempotency_key === 'string' && intent.body.idempotency_key.trim()
      && typeof intent.body.description === 'string' && intent.body.description.trim()
      && intent.body.meta_data?.confirmed_physical_safe === true && intent.body.meta_data.source === 'unilabos-frontend') return intent
  } catch { /* 不恢复损坏或属于其他任务的请求。 */ }
  return null
}

export function saveTaskResourceUnlockIntent(taskUuid: string, intent: TaskResourceUnlockIntent | null) {
  if (intent) sessionStorage.setItem(storageKey(taskUuid), JSON.stringify(intent))
  else sessionStorage.removeItem(storageKey(taskUuid))
}
