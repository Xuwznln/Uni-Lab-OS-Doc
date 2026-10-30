import { EDGE_API_BASE } from './edgeClient'
import type { ManualActionJob } from './stationErrorClient'

const terminalStatuses = new Set(['succeeded', 'failed', 'canceled', 'timeout'])

export const manualActionEnded = (job: ManualActionJob) => terminalStatuses.has(job.status)
export const manualActionNeedsReview = (job: ManualActionJob) => manualActionEnded(job) && Boolean(job.uncertainty_reason) && !job.result_reviewed

export function manualSessionActions(jobs: ManualActionJob[], sessionId?: string) {
  const current = sessionId ? jobs.filter((job) => job.meta_data.session_id === sessionId) : []
  return {
    jobs: current,
    running: current.filter((job) => !manualActionEnded(job)).length,
    unresolved: current.filter(manualActionNeedsReview).length,
  }
}

export interface ManualHandlingDraft {
  deviceId: string
  actionId: string
  drafts: Record<string, string>
  raw: string
  asJson: boolean
  summary: string
}

export const manualDraftStorageKey = (sessionId: string, apiBase = EDGE_API_BASE) => `unilab:handling-draft:${apiBase}:local:${sessionId}`

export function loadManualHandlingDraft(sessionId: string, summary: string): ManualHandlingDraft {
  const fallback = { deviceId: '', actionId: '', drafts: {}, raw: '{}', asJson: false, summary }
  try {
    const value = JSON.parse(sessionStorage.getItem(manualDraftStorageKey(sessionId)) || 'null')
    if (!value || typeof value.deviceId !== 'string' || typeof value.actionId !== 'string'
      || typeof value.raw !== 'string' || typeof value.asJson !== 'boolean' || typeof value.summary !== 'string'
      || !value.drafts || typeof value.drafts !== 'object' || Array.isArray(value.drafts)
      || !Object.values(value.drafts).every((item) => typeof item === 'string')) return fallback
    // 只恢复输入草稿，不从浏览器存储恢复任何人工确认。
    return { deviceId: value.deviceId, actionId: value.actionId, drafts: value.drafts, raw: value.raw, asJson: value.asJson, summary: value.summary }
  } catch { return fallback }
}

export function saveManualHandlingDraft(sessionId: string, draft: ManualHandlingDraft) {
  try { sessionStorage.setItem(manualDraftStorageKey(sessionId), JSON.stringify(draft)) } catch { /* 存储不可用时仍保留本次打开期间的输入。 */ }
}
