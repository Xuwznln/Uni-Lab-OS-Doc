import type { PageId, WorkflowTarget } from '../types'

const supportedPages = new Set<PageId>(['overview', 'materials', 'devices', 'reagents', 'operations', 'workflows', 'tasks'])

export function pageFromSearch(search: string): PageId {
  const page = new URLSearchParams(search).get('page')
  return page && supportedPages.has(page as PageId) ? (page as PageId) : 'overview'
}

export function searchWithPage(search: string, page: PageId): string {
  const params = new URLSearchParams(search)
  params.set('page', page)
  if (page !== 'materials') params.delete('usageTask')
  if (page !== 'devices') params.delete('device')
  if (page !== 'workflows') {
    params.delete('workflow')
    params.delete('revision')
  }
  return `?${params.toString()}`
}

export function searchForWorkflow(search: string, target: WorkflowTarget): string {
  const params = new URLSearchParams(search)
  params.set('page', 'workflows')
  params.set('workflow', target.workflowUuid)
  if (target.revision === undefined) params.delete('revision')
  else params.set('revision', String(target.revision))
  if (target.taskUuid) params.set('task', target.taskUuid)
  else params.delete('task')
  return `?${params.toString()}`
}

export function workflowTargetFromSearch(search: string): WorkflowTarget | undefined {
  const params = new URLSearchParams(search)
  const workflowUuid = params.get('workflow')?.trim()
  if (!workflowUuid) return undefined
  const rawRevision = params.get('revision')
  const parsedRevision = rawRevision === null ? undefined : Number(rawRevision)
  return {
    workflowUuid,
    revision: parsedRevision !== undefined && Number.isInteger(parsedRevision) && parsedRevision > 0
      ? parsedRevision
      : undefined,
    taskUuid: params.get('task')?.trim() || undefined,
  }
}
