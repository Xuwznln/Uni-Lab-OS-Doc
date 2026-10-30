import { describe, expect, it } from 'vitest'
import { buildResourceUsage, materialsFromUsage } from './resourceUsage'
import { canvasBlockSize, deviceTaskIds, layoutMaterialCanvas, locateCanvasResource } from './materialCanvasLayout'
import { transferSnapshot, usageLock, usageSnapshot, usageTask } from '../test/resourceUsageFixtures'

function singleColumnLayers(layerCount = 2) {
  const snapshot = structuredClone(usageSnapshot())
  snapshot.nodes[1].material.name = 'S1 上料过渡仓'
  snapshot.nodes[1].sites = Array.from({ length: layerCount * 3 }, (_, index) => ({
    uuid: `s1-${index}`, name: `L${Math.floor(index / 3) + 1}C${index % 3 + 1}`,
    position_x: 6, position_y: 58 + index % 3 * 120, position_z: 70 + Math.floor(index / 3) * 190,
    occupied_material_uuid: index === 0 ? 'sample' : null,
  }))
  snapshot.nodes[2].current_site_uuid = 's1-0'
  return snapshot
}

describe('设备与库位画布布局', () => {
  it('does not select an arbitrary empty site before the user selects anything', () => {
    expect(locateCanvasResource(buildResourceUsage(usageSnapshot(), []))).toEqual({ device: undefined, site: undefined })
  })
  it('places the two real S1 layers beside each other while preserving Y order', () => {
    const snapshot = singleColumnLayers()
    snapshot.nodes.push({ material: { uuid: 'z-device', name: '后方设备', type: 'device' }, sites: [] })
    const blocks = layoutMaterialCanvas(buildResourceUsage(snapshot, []))
    const section = blocks.find((block) => block.uuid === 'robot')!.sections[0]
    expect(section).toMatchObject({ columns: 2, rows: 3, layerLabels: [{ name: 'L1', column: 0 }, { name: 'L2', column: 1 }] })
    expect(section.sites.map(({ site, column, row }) => ({ name: site.name, column, row }))).toEqual([
      { name: 'L1C1', column: 0, row: 2 }, { name: 'L1C2', column: 0, row: 1 }, { name: 'L1C3', column: 0, row: 0 },
      { name: 'L2C1', column: 1, row: 2 }, { name: 'L2C2', column: 1, row: 1 }, { name: 'L2C3', column: 1, row: 0 },
    ])
    // 部署没有设备坐标时按网格铺开：有库位的设备在首行，无库位设备另起一行，不再压成一列。
    const robot = blocks.find((block) => block.uuid === 'robot')!
    expect(robot).toMatchObject({ x: 40, y: 40 })
    const siteless = blocks.filter((block) => block.uuid !== 'robot')
    expect(siteless.map((block) => block.y)).toEqual([siteless[0].y, siteless[0].y])
    expect(siteless[0].y).toBeGreaterThan(robot.y + robot.height)
    expect(new Set(siteless.map((block) => block.x)).size).toBe(2)
  })
  it('keeps coordinate layout when the deployment provides device positions', () => {
    const snapshot = structuredClone(usageSnapshot())
    snapshot.nodes[0].relative_position = { position_x: 1480, position_y: 80 }
    const blocks = layoutMaterialCanvas(buildResourceUsage(snapshot, []))
    expect(blocks.find((block) => block.uuid === 'robot')!.x).toBeGreaterThan(blocks.find((block) => block.uuid === 'camera')!.x)
  })

  it.each(['missing', 'duplicate'])('uses measured heights instead of ambiguous layer labels when a prefix is %s', (kind) => {
    const snapshot = singleColumnLayers()
    if (kind === 'missing') snapshot.nodes[1].sites[1].name = '未编号库位'
    else snapshot.nodes[1].sites.slice(3).forEach((site) => { site.name = site.name.replace('L2', 'L1') })
    const section = layoutMaterialCanvas(buildResourceUsage(snapshot, [])).find((block) => block.uuid === 'robot')!.sections[0]
    expect(section).toMatchObject({ layerLabels: [{ name: '高度 70', column: 0 }, { name: '高度 260', column: 1 }] })
  })

  it.each([{ layers: 3, columns: 3, rows: 3 }, { layers: 4, columns: 1, rows: 12 }])('only places $layers single-column layers beside each other when they fit the existing frame width', ({ layers, columns, rows }) => {
    const blocks = layoutMaterialCanvas(buildResourceUsage(singleColumnLayers(layers), []))
    expect(blocks.find((block) => block.uuid === 'robot')!.sections[0]).toMatchObject({ columns, rows })
  })
  it('shrinks the initial frame while reserving room for larger sections in the grid', () => {
    const snapshot = structuredClone(usageSnapshot())
    snapshot.nodes[1].material.name = '01 转运仓'
    snapshot.nodes.push({
      material: { uuid: 'bulk-warehouse', name: '02 扩展仓', type: 'warehouse', parent_uuid: 'robot' },
      sites: Array.from({ length: 24 }, (_, index) => ({ uuid: `bulk-${index}`, name: `B${index + 1}`, position_x: index % 6, position_y: Math.floor(index / 6) })),
    }, { material: { uuid: 'z-device', name: '后方设备', type: 'device' }, sites: [] })
    const blocks = layoutMaterialCanvas(buildResourceUsage(snapshot, []))
    const robot = blocks.find((block) => block.uuid === 'robot')!
    const next = blocks.find((block) => block.uuid === 'z-device')!
    expect(robot.sections[0].uuid).toBe('warehouse')
    const expanded = canvasBlockSize(robot.sections.find((section) => section.uuid === 'bulk-warehouse'))
    expect(robot.width).toBeLessThan(expanded.width)
    expect(robot.height).toBeLessThan(expanded.height)
    // 网格按最大分组占位，切换到大分组也不会覆盖后方设备。
    expect(robot).toMatchObject({ x: 40, y: 40 })
    expect(robot.y + expanded.height).toBeLessThan(next.y)
    expect(robot.y + expanded.height).toBeLessThan(blocks.find((block) => block.uuid === 'camera')!.y)
  })
  it('keeps all blocks fixed as the beaker moves across devices and follows the new site', () => {
    const snapshot = transferSnapshot()
    const before = buildResourceUsage(snapshot, [usageTask()])
    const blocks = layoutMaterialCanvas(before)
    expect(before.sites.get('site-8')).toMatchObject({ materialUuid: undefined, transfers: [{ direction: 'in' }] })
    snapshot.nodes[1].sites[0].occupied_material_uuid = null
    snapshot.nodes[3].sites[0].occupied_material_uuid = 'sample'
    snapshot.nodes[2].current_site_uuid = 'site-8'
    snapshot.nodes[2].material.parent_uuid = 'camera'
    snapshot.jobs = []
    const after = buildResourceUsage(snapshot, [usageTask()])
    expect(after.sites.get('site-a')?.materialUuid).toBeUndefined()
    expect(after.sites.get('site-8')?.materialUuid).toBe('sample')
    expect(locateCanvasResource(after, 'sample').device?.uuid).toBe('camera')
    expect(materialsFromUsage(snapshot).find((material) => material.uuid === 'sample')?.currentLocation).toMatchObject({ siteUuid: 'site-8' })
    expect(layoutMaterialCanvas(after).map(({ uuid, x, y, width, height }) => ({ uuid, x, y, width, height }))).toEqual(blocks.map(({ uuid, x, y, width, height }) => ({ uuid, x, y, width, height })))
    expect(deviceTaskIds(after.devices.find((device) => device.uuid === 'robot')!)).toEqual([])
  })

  it('does not light idle devices from stock or site reservations', () => {
    const snapshot = transferSnapshot(); snapshot.jobs = []
    snapshot.occupancies = [usageLock('material/warehouse/site/site-b/exclusive', { state: 'reserved' })]
    const model = buildResourceUsage(snapshot, [])
    expect(model.sites.get('site-a')?.materialUuid).toBe('sample')
    expect(deviceTaskIds(model.devices.find((device) => device.uuid === 'robot')!)).toEqual([])
  })

  it('preserves coordinates and shows different heights together without overlapping equipment', () => {
    const snapshot = structuredClone(usageSnapshot())
    snapshot.nodes[0].relative_position = { position_x: 1480, position_y: 80 }
    snapshot.nodes[1].sites = [
      { uuid: 'a', name: '下层左', position_x: 10, position_y: 0, position_z: 0 },
      { uuid: 'b', name: '下层右', position_x: 90, position_y: 0, position_z: 0 },
      { uuid: 'c', name: '上层左', position_x: 10, position_y: 0, position_z: 100 },
    ]
    expect(materialsFromUsage(snapshot)[0].position).toEqual([1480, 80, 0])
    const layout = layoutMaterialCanvas(buildResourceUsage(snapshot, []))
    const robot = layout.find((block) => block.uuid === 'robot')!
    expect(robot.sections[0]).toMatchObject({ columns: 2, rows: 2 })
    expect(robot.sections[0].sites.find(({ site }) => site.uuid === 'a')).toMatchObject({ column: 0, row: 0 })
    expect(robot.sections[0].sites.find(({ site }) => site.uuid === 'b')).toMatchObject({ column: 1, row: 0 })
    expect(robot.sections[0].sites.find(({ site }) => site.uuid === 'c')).toMatchObject({ column: 0, row: 1 })
    for (const a of layout) for (const b of layout) if (a.uuid !== b.uuid) {
      expect(a.x >= b.x + b.width || b.x >= a.x + a.width || a.y >= b.y + b.height || b.y >= a.y + a.height).toBe(true)
    }
  })

  it('shows a failed transfer as uncertain without claiming that the target contains material', () => {
    const snapshot = transferSnapshot()
    snapshot.jobs![0].status = 'failed'
    snapshot.occupancies = [usageLock('material/warehouse/site/site-a/exclusive', { state: 'uncertain' })]
    const model = buildResourceUsage(snapshot, [])
    expect(model.sites.get('site-a')?.materialUuid).toBe('sample')
    expect(model.sites.get('site-8')).toMatchObject({ materialUuid: undefined, transfers: [{ uncertain: true, direction: 'in' }] })
  })
})
