import { afterEach, describe, expect, it, vi } from 'vitest'
import { adaptTask } from './edgeClient'
import { buildResourceUsage, holdingLabel, loadResourceUsage } from './resourceUsage'
import { usageLock, usageSnapshot, usageTask } from '../test/resourceUsageFixtures'

afterEach(() => vi.unstubAllGlobals())

describe('资源使用事实', () => {
  it('separates stored material, site reservation and whole-device occupancy', () => {
    const model = buildResourceUsage(usageSnapshot([
      usageLock('/devices/robot-local'),
      usageLock('material/warehouse/site/site-b/exclusive', { state: 'reserved' }),
    ]), [usageTask()])
    const device = model.devices.find((entry) => entry.uuid === 'robot')!
    expect(device.activities[0].node.name).toBe('转运到目标库位')
    expect(device.sites.find((site) => site.uuid === 'site-a')).toMatchObject({ materialName: '测试烧杯', holdings: [] })
    expect(device.sites.find((site) => site.uuid === 'site-b')).toMatchObject({ materialUuid: undefined, holdings: [expect.objectContaining({ state: 'reserved' })] })
    expect(model.holdingCount).toBe(2)
    expect(model.unmatched).toEqual([])
  })

  it('shows whole-container occupation on its sites and retains canceled-task locks', () => {
    const canceled = adaptTask({ uuid: 'task-1', status: 'canceled', cleanup_status: 'requires_attention' })
    expect(canceled.status).toBe('intervention_required')
    const model = buildResourceUsage(usageSnapshot([usageLock('material/warehouse/exclusive')]), [canceled])
    const device = model.devices.find((entry) => entry.uuid === 'robot')!
    expect(device.sites.every((site) => site.holdings.length === 1)).toBe(true)
    expect(holdingLabel(device.holdings[0])).toBe('遗留占用')
  })

  it('does not drop generic, missing-site or unknown-device locks', () => {
    const model = buildResourceUsage(usageSnapshot([
      usageLock('resource:generic'), usageLock('/devices/missing'),
      usageLock('material/warehouse/site/missing/exclusive'),
      usageLock('/devices/robot', { state: 'released' }),
    ]), [])
    expect(model.unmatched).toHaveLength(3)
    expect(model.holdingCount).toBe(3)
  })

  it('counts a tenancy independently and never equates an in-flight unlocked action with a lock', () => {
    const model = buildResourceUsage(usageSnapshot([usageLock('/devices/camera', { occupancy_kind: 'device_tenancy', state: 'active', workflow_task_uuid: undefined, task_uuid: 'task-2' })]), [usageTask()])
    expect(model.devices.find((entry) => entry.uuid === 'robot')).toMatchObject({ holdings: [], activities: [expect.anything()] })
    const camera = model.devices.find((entry) => entry.uuid === 'camera')!
    expect(camera.holdings[0].taskUuid).toBe('task-2')
    expect(holdingLabel(camera.holdings[0])).toBe('持续占用')
  })

  it('removes released locks and updates actual material placement in a later snapshot', () => {
    const before = usageSnapshot([usageLock('material/warehouse/site/site-b/exclusive')])
    const after = structuredClone(before)
    after.occupancies[0].state = 'released'
    after.nodes[1].sites[0].occupied_material_uuid = null
    after.nodes[1].sites[1].occupied_material_uuid = 'sample'
    after.nodes[2].current_site_uuid = 'site-b'
    const result = buildResourceUsage(after, [])
    const sites = result.devices.find((device) => device.uuid === 'robot')!.sites
    expect(sites[0].materialUuid).toBeUndefined()
    expect(sites[1].materialName).toBe('测试烧杯')
    expect(result.holdingCount).toBe(0)
  })

  it('does not publish a partial empty result when the occupancy API fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: !url.includes('resource-usage'), status: 503, json: async () => ({ code: 0, data: { nodes: [] } }) })))
    await expect(loadResourceUsage()).rejects.toThrow()
  })

  it('rejects a graph observed halfway through a transfer instead of showing one beaker twice', async () => {
    const snapshot = structuredClone(usageSnapshot())
    snapshot.nodes[1].sites[1].occupied_material_uuid = 'sample'
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, status: 200, json: async () => ({ code: 0, data: url.endsWith('/materials/graph') ? { nodes: snapshot.nodes } : { occupancies: [], jobs: [], material_bindings: [] } }) })))
    await expect(loadResourceUsage()).rejects.toThrow('等待一致的库位快照')
  })
})
