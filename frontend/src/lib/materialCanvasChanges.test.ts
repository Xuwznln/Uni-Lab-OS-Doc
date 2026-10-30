import { describe, expect, it } from 'vitest'
import { buildResourceUsage } from './resourceUsage'
import { describeMaterialEvent, describeSite, diffSiteOccupancy, eventStepLabel, findEventSite } from './materialCanvasChanges'
import { usageSnapshot } from '../test/resourceUsageFixtures'

function moved() {
  const snapshot = structuredClone(usageSnapshot())
  snapshot.nodes[1].sites[0].occupied_material_uuid = null
  snapshot.nodes[1].sites[1].occupied_material_uuid = 'sample'
  snapshot.nodes[2].current_site_uuid = 'site-b'
  return snapshot
}

describe('库位占用变化识别', () => {
  it('reports nothing when occupancy is unchanged', () => {
    const model = buildResourceUsage(usageSnapshot(), [])
    expect(diffSiteOccupancy(model, buildResourceUsage(usageSnapshot(), []))).toEqual([])
  })
  it('pairs the vacated and the newly occupied site of the same material into one move', () => {
    const [change, ...rest] = diffSiteOccupancy(buildResourceUsage(usageSnapshot(), []), buildResourceUsage(moved(), []), 1000)
    expect(rest).toEqual([])
    expect(change).toMatchObject({ materialUuid: 'sample', materialName: '测试烧杯', at: 1000,
      from: { siteUuid: 'site-a', siteName: 'A01', ownerName: '转运仓' }, to: { siteUuid: 'site-b', siteName: 'A02', ownerName: '转运仓' } })
    expect(describeSite(change.from)).toBe('转运仓 / A01')
  })
  it('reports a removal without inventing a destination', () => {
    const snapshot = structuredClone(usageSnapshot())
    snapshot.nodes[1].sites[0].occupied_material_uuid = null
    snapshot.nodes[2].current_site_uuid = undefined
    const [change] = diffSiteOccupancy(buildResourceUsage(usageSnapshot(), []), buildResourceUsage(snapshot, []))
    expect(change.from?.siteUuid).toBe('site-a')
    expect(change.to).toBeUndefined()
    expect(describeSite(change.to)).toBe('画布外')
  })
})

describe('任务物料变化的展示', () => {
  const base = { uuid: 'e', occurredAt: '2026-09-25T04:54:34Z', materialUuid: 'sample', materialName: '测试烧杯', stepPath: [], taskUuid: 't' }
  it('describes moves, consumption and used marks in one line', () => {
    expect(describeMaterialEvent({ ...base, kind: 'moved', fromSite: '上料仓 / L03', toSite: 'sampling / spotting' })).toBe('上料仓 / L03 → sampling / spotting')
    expect(describeMaterialEvent({ ...base, kind: 'consumed', quantity: 0.10000000000002274, quantityUnit: 'mL', balanceAfter: 999.9 })).toBe('扣 0.1 mL，余 999.9 mL')
    expect(describeMaterialEvent({ ...base, kind: 'used', reason: '已装洗脱样品' })).toBe('标为已使用（已装洗脱样品）')
  })
  it('keeps the last two parent steps plus the node name and the full path for the tooltip', () => {
    expect(eventStepLabel({ ...base, kind: 'used', stepPath: ['8 粉末收集', '收集-周期', '收集-下料'], nodeName: 'mark_used' }))
      .toEqual({ short: '收集-周期 · 收集-下料 · mark_used', full: '8 粉末收集 / 收集-周期 / 收集-下料 / mark_used' })
    expect(eventStepLabel({ ...base, kind: 'used' }).short).toBe('未关联步骤')
  })
  it('finds the canvas site by owner identity first and falls back to the owner label', () => {
    const model = buildResourceUsage(usageSnapshot(), [])
    expect(findEventSite(model, '任意名称 / A02', 'warehouse')?.siteUuid).toBe('site-b')
    expect(findEventSite(model, '转运仓 / A01')?.siteUuid).toBe('site-a')
    expect(findEventSite(model, '转运仓 / Z99')).toBeUndefined()
  })
})
