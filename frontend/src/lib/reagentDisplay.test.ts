import { afterEach, describe, expect, it, vi } from 'vitest'
import { loadReagentInfos, loadReagents } from './edgeClient'

afterEach(() => vi.unstubAllGlobals())

function respondWith(items: Record<string, unknown>[]) {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true, status: 200,
    json: async () => ({ code: 0, data: { items, total: items.length } }),
  } as Response)))
}

describe('试剂详情字段映射', () => {
  it('目录保留完整日期、化学字段和结构化扩展值', async () => {
    const metadata = {
      supplier: { name: '供应商', verified: false },
      custom_parameters: [{ name: '复核次数', value: 0, unit: '次' }],
      imported_at: '2026-09-01T01:02:03.456Z', tags: ['分析纯'], note: null,
    }
    respondWith([{
      uuid: 'info-1', name: '二氯乙烷', name_en: '1,2-Dichloroethane',
      aliases: ['1,2-二氯乙烷', 'DCE'], cas: '107-06-2', molecular_formula: 'C2H4Cl2',
      smiles: 'ClCCCl', inchi_key: 'WSLDOOZREJYCGB-UHFFFAOYSA-N', molecular_weight: 98.96,
      density_g_per_ml: 1.25, physical_state: 'liquid', description: '已复核的参考数据',
      meta_data: metadata, create_time: '2026-09-01T01:02:03.456Z',
      update_time: '2026-09-08T09:10:11.123+08:00',
    }])

    const [item] = await loadReagentInfos()
    expect(item).toMatchObject({
      nameEn: '1,2-Dichloroethane', aliases: ['1,2-二氯乙烷', 'DCE'],
      molecularFormula: 'C2H4Cl2', smiles: 'ClCCCl', inchiKey: 'WSLDOOZREJYCGB-UHFFFAOYSA-N',
      molecularWeight: 98.96, densityGPerMl: 1.25, physicalState: 'liquid',
      description: '已复核的参考数据', metadata,
      createdAt: '2026-09-01T01:02:03.456Z', updatedAt: '2026-09-08T09:10:11.123+08:00',
    })
  })

  it('库存保留后端返回的化学信息、瓶级密度及零余量和零浓度', async () => {
    const metaData = {
      source: '手工核验', source_reagent_uuid: 'source-1', dispense_command_id: 'dispense-1',
      batch: 'B-001', inspected: false, count: 0,
    }
    respondWith([{
      uuid: 'reagent-1', material_uuid: 'material-1', reagent_info_uuid: 'info-1',
      name: '乙醇', name_en: 'Ethanol', aliases: ['酒精', 'EtOH'], cas: '64-17-5',
      molecular_formula: 'C2H6O', smiles: 'CCO', inchi_key: 'LFQSCWFLJHTTHZ-UHFFFAOYSA-N',
      molecular_weight: 46.07, physical_state: 'liquid', density_g_per_ml: 0.789,
      density_source: 'dictionary', quantity: 0, quantity_unit: 'mL',
      concentration_value: 0, concentration_unit: '%', active_workflow_reserved_quantity: 0,
      container_name: 'R1C1', container_barcode: 'BOTTLE-01', description: '已耗尽，保留记录',
      meta_data: metaData, revision: 3, create_time: '2026-09-02T01:02:03.456Z',
      update_time: '2026-09-08T02:03:04.567Z',
    }])

    const [item] = await loadReagents()
    expect(item).toMatchObject({
      nameEn: 'Ethanol', aliases: ['酒精', 'EtOH'], molecularFormula: 'C2H6O',
      smiles: 'CCO', inchiKey: 'LFQSCWFLJHTTHZ-UHFFFAOYSA-N', molecularWeight: 46.07,
      physicalState: 'liquid', densityGPerMl: 0.789, densitySource: 'dictionary',
      quantity: 0, concentrationValue: 0, concentrationUnit: '%', activeWorkflowReservedQuantity: 0,
      source: '手工核验', sourceReagentUuid: 'source-1', dispenseCommandId: 'dispense-1',
      description: '已耗尽，保留记录', metaData, revision: 3,
      createdAt: '2026-09-02T01:02:03.456Z', updatedAt: '2026-09-08T02:03:04.567Z',
    })
  })

  it('缺少可选字段时不捏造化学信息、日期或密度来源', async () => {
    respondWith([{
      uuid: 'record-1', material_uuid: 'material-1', reagent_info_uuid: 'info-1', name: '自配物质',
      aliases: null, molecular_weight: null, density_g_per_ml: null, physical_state: null,
      meta_data: { source: null, custom_parameters: [] }, create_time: null, update_time: null,
    }])
    const [catalog] = await loadReagentInfos()
    const [inventory] = await loadReagents()

    for (const item of [catalog, inventory]) {
      expect(item).toMatchObject({
        aliases: [], nameEn: undefined, molecularFormula: undefined, smiles: undefined,
        inchiKey: undefined, molecularWeight: undefined, densityGPerMl: undefined,
        physicalState: 'unknown', createdAt: undefined, updatedAt: '',
      })
    }
    expect(inventory).toMatchObject({ source: undefined, densitySource: undefined })
    expect(catalog.metadata).toEqual({ source: null, custom_parameters: [] })
  })
})
