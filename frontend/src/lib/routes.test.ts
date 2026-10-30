import { describe, expect, it } from 'vitest'
import { pageFromSearch, searchForWorkflow, searchWithPage, workflowTargetFromSearch } from './routes'

describe('pageFromSearch', () => {
  it('accepts the supported query routes, including device control', () => {
    expect(pageFromSearch('?page=overview')).toBe('overview')
    expect(pageFromSearch('?page=materials')).toBe('materials')
    expect(pageFromSearch('?page=workflows')).toBe('workflows')
    expect(pageFromSearch('?page=tasks&variant=C')).toBe('tasks')
    expect(pageFromSearch('?page=devices&device=device-1')).toBe('devices')
  })

  it('falls back to overview for an unknown route', () => {
    expect(pageFromSearch('?page=unknown')).toBe('overview')
    expect(pageFromSearch('')).toBe('overview')
  })
})

describe('searchWithPage', () => {
  it('changes only the page while retaining useful selection state', () => {
    expect(searchWithPage('?page=overview&task=task-1', 'tasks')).toBe('?page=tasks&task=task-1')
  })

  it('keeps a device deep link only on the device page', () => {
    expect(searchWithPage('?page=overview&device=device-1', 'devices')).toBe('?page=devices&device=device-1')
    expect(searchWithPage('?page=devices&device=device-1', 'materials')).toBe('?page=materials')
  })
})

describe('workflow task navigation', () => {
  it('stores the targeted workflow and revision in the same-page URL', () => {
    const search = searchForWorkflow('?page=tasks&deploy=latest', {
      workflowUuid: 'workflow-1',
      revision: 7,
      taskUuid: 'task-1',
    })
    expect(search).toBe('?page=workflows&deploy=latest&workflow=workflow-1&revision=7&task=task-1')
    expect(workflowTargetFromSearch(search)).toEqual({
      workflowUuid: 'workflow-1',
      revision: 7,
      taskUuid: 'task-1',
    })
  })

  it('ignores invalid or incomplete workflow targets', () => {
    expect(workflowTargetFromSearch('?page=workflows')).toBeUndefined()
    expect(workflowTargetFromSearch('?page=workflows&workflow=workflow-1&revision=nope')).toEqual({
      workflowUuid: 'workflow-1',
      revision: undefined,
      taskUuid: undefined,
    })
  })
})
