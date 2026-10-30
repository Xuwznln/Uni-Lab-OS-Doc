import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MaterialUsagePanel, SelectedSiteUsage } from './MaterialUsagePanel'
import type { MaterialUsageContext } from './MaterialUsagePanel'
import { buildResourceUsage, materialsFromUsage, taskColor } from '../lib/resourceUsage'
import { usageLock, usageSnapshot, usageTask } from '../test/resourceUsageFixtures'

function context(): MaterialUsageContext {
  const snapshot = usageSnapshot([usageLock('material/warehouse/site/site-b/exclusive', { state: 'reserved' })])
  snapshot.material_bindings = [{ uuid: 'binding-1', material_uuid: 'sample', workflow_task_uuid: 'task-1', workflow_node_job_uuid: 'job-1', status: 'active' }]
  snapshot.jobs = [{ uuid: 'job-1', task_uuid: 'task-1', task_name: '物料转运', task_status: 'running', node_uuid: 'node-1', node_name: '转运', status: 'running', expected_change_set: { kind: 'material_transfer', material_uuid: 'sample', source_site_uuid: 'site-a', target_site_uuid: 'site-b' } }]
  return { model: buildResourceUsage(snapshot, [usageTask()]), tasks: [usageTask()], stale: false, loading: false, updatedAt: 0, refresh: vi.fn(), onOpenTask: vi.fn() }
}

describe('物料页实时占用', () => {
  it('shows actual source material and planned target without moving it prematurely', () => {
    const ctx = context()
    expect(ctx.model!.sites.get('site-a')?.bindings[0].workflow_task_uuid).toBe('task-1')
    render(<SelectedSiteUsage site={ctx.model!.sites.get('site-b')} context={ctx} />)
    expect(screen.getByRole('heading')).toHaveTextContent('当前存放：空库位')
    expect(screen.getByText('计划放入：测试烧杯')).toBeInTheDocument()
    expect(screen.getByText('已预留')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '查看任务与执行锁' }))
    expect(ctx.onOpenTask).toHaveBeenCalledWith('task-1')
  })

  it('keeps initial inventory unbound and colors neutral without any task', () => {
    const snapshot = usageSnapshot()
    const ctx = { ...context(), model: buildResourceUsage(snapshot, []) }
    render(<MaterialUsagePanel materialUuid="sample" context={ctx} onSelectSite={vi.fn()} />)
    expect(screen.getByText('未绑定 Task，可作为初始库存存放。')).toBeInTheDocument()
    expect(within(screen.getByLabelText('当前资源使用情况')).queryByRole('button', { name: /物料转运/ })).not.toBeInTheDocument()
    expect(materialsFromUsage(snapshot).find((material) => material.uuid === 'sample')?.currentLocation).toMatchObject({ kind: 'site', siteUuid: 'site-a' })
    expect(taskColor('')).toBe('#94a0b2')
    expect(taskColor('task-1')).toBe(taskColor('task-1'))
  })

  it('navigates from a device descendant site to its owner and exact site', () => {
    const onSelectSite = vi.fn()
    render(<MaterialUsagePanel materialUuid="robot" context={context()} onSelectSite={onSelectSite} />)
    fireEvent.click(screen.getByRole('button', { name: /转运仓 \/ A02/ }))
    expect(onSelectSite).toHaveBeenCalledWith('warehouse', 'site-b')
  })
})
