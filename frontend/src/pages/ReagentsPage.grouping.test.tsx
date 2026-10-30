import { describe, expect, it } from 'vitest'
import { displayBarcode, groupInventory, shortLocation } from './ReagentsPage'
import type { MaterialRecord, ReagentRecord } from '../types'

function reagent(overrides: Partial<ReagentRecord> & { uuid: string }): ReagentRecord {
  return {
    materialUuid: `m-${overrides.uuid}`, reagentInfoUuid: 'info-ethanol', name: '乙醇', cas: '64-17-5', physicalState: 'liquid',
    quantity: 100, quantityUnit: 'mL', containerName: `瓶 ${overrides.uuid}`, revision: 1, updatedAt: '2026-09-03T00:00:00Z',
    ...overrides,
  }
}
const materials = [{ uuid: 'm-src', currentLocation: { kind: 'site', label: '试剂瓶堆栈 / R3C2' } }] as unknown as MaterialRecord[]

describe('groupInventory', () => {
  it('groups bottles by reagent identity and puts dispensed bottles under their source', () => {
    const groups = groupInventory([
      reagent({ uuid: 'child-b', containerName: 'R4C4', sourceReagentUuid: 'src', quantity: 20 }),
      reagent({ uuid: 'src', containerName: 'R3C2', quantity: 60, activeWorkflowReservedQuantity: 10 }),
      reagent({ uuid: 'lonely', containerName: 'R1C1', quantity: 0 }),
      reagent({ uuid: 'child-a', containerName: 'R3C4', sourceReagentUuid: 'src', quantity: 20 }),
      reagent({ uuid: 'water', reagentInfoUuid: 'info-water', name: '水', cas: '7732-18-5', quantity: 500 }),
    ], materials)

    expect(groups.map((group) => group.name)).toEqual(['水', '乙醇'])
    const ethanol = groups[1]
    expect(ethanol.bottles.map((bottle) => [bottle.item.containerName, bottle.depth])).toEqual([
      ['R3C2', 0], ['R3C4', 1], ['R4C4', 1], ['R1C1', 0],
    ])
    expect(ethanol.bottles[1].source?.uuid).toBe('src')
    expect(ethanol.bottles[1].sourceLocation).toBe('试剂瓶堆栈 / R3C2')
    expect(ethanol.bottles[0].location).toBe('试剂瓶堆栈 / R3C2')
    expect(ethanol.totals).toEqual([{ unit: 'mL', available: 100, reserved: 10 }])
    expect(ethanol.emptyCount).toBe(1)
  })

  it('keeps a dispensed bottle whose source is gone as a standalone row and flags it', () => {
    const [group] = groupInventory([reagent({ uuid: 'orphan', sourceReagentUuid: 'deleted-source' })], [])
    expect(group.bottles).toHaveLength(1)
    expect(group.bottles[0].depth).toBe(0)
    expect(group.bottles[0].orphanSource).toBe(true)
  })

  it('sums totals per unit instead of mixing mL and g', () => {
    const [group] = groupInventory([
      reagent({ uuid: 'a', quantity: 30, quantityUnit: 'mL' }),
      reagent({ uuid: 'b', quantity: 5, quantityUnit: 'g' }),
    ], [])
    expect(group.totals).toEqual([{ unit: 'mL', available: 30, reserved: 0 }, { unit: 'g', available: 5, reserved: 0 }])
  })

  it('never drops a bottle even if lineage data forms a cycle', () => {
    const [group] = groupInventory([
      reagent({ uuid: 'x', sourceReagentUuid: 'y' }),
      reagent({ uuid: 'y', sourceReagentUuid: 'x' }),
    ], [])
    expect(group.bottles.map((bottle) => bottle.item.uuid).sort()).toEqual(['x', 'y'])
  })
})

describe('display helpers', () => {
  it('hides graph-generated system barcodes but keeps real ones', () => {
    expect(displayBarcode('UNILAB-GRAPH-s10_liquid_reagent__R3C2')).toBeUndefined()
    expect(displayBarcode('LOT-2026-001')).toBe('LOT-2026-001')
    expect(displayBarcode(undefined)).toBeUndefined()
  })

  it('shortens a location label to its last segment for lineage chips', () => {
    expect(shortLocation('试剂瓶堆栈 / R3C2')).toBe('R3C2')
    expect(shortLocation('R1C1')).toBe('R1C1')
    expect(shortLocation(undefined)).toBeUndefined()
  })
})
