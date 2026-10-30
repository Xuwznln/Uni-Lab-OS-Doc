import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { demoMaterials } from '../data/demo'
import type { MaterialRecord } from '../types'
import { ReagentsPage } from './ReagentsPage'

vi.mock('../components/ChemicalStructurePreview', () => ({
  ChemicalStructurePreview: ({ smiles }: { smiles: string }) => <div data-testid="structure-preview">{smiles}</div>,
}))

const catalogItem = {
  uuid: 'info-ethanol', name: '乙醇', name_en: 'Ethanol', aliases: ['酒精', 'EtOH'], cas: '64-17-5',
  molecular_formula: 'C2H6O', smiles: 'CCO', inchi_key: 'LFQSCWFLJHTTHZ-UHFFFAOYSA-N',
  molecular_weight: 46.07, density_g_per_ml: 0.789, physical_state: 'liquid', description: '目录理化信息说明',
  meta_data: { supplier: { name: '试剂供应商', checks: { verified: false, retry_count: 0 } }, custom_parameters: [{ name: '纯度', value: '99.9%', method: '滴定', verified: false }] },
  create_time: '2026-09-01T01:02:03Z', update_time: '2026-09-07T04:05:06Z',
}

const inventoryItem = {
  ...catalogItem, uuid: 'reagent-1', material_uuid: 'bottle-1', reagent_info_uuid: catalogItem.uuid,
  quantity: 50, quantity_unit: 'mL', concentration_value: 95, concentration_unit: '%',
  density_g_per_ml: 0.791, density_source: 'dictionary', container_name: 'R1C1 试剂瓶', container_barcode: 'LOT-2026-001',
  active_workflow_reserved_quantity: 5, maximum_capacity: { max_volume_ul: 80000 }, rated_capacity: { max_volume_ul: 100000 },
  material_revision: 4, revision: 3, description: '本瓶入库盘点说明',
  meta_data: { source: 'manual-registration', source_reagent_uuid: 'source-reagent', dispense_command_id: 'dispense-command', custom_parameters: [{ name: '批次', value: 'batch-01' }], certificate: 'COA-123' },
}

const material: MaterialRecord = {
  ...demoMaterials[0], uuid: 'bottle-1', name: 'R1C1 试剂瓶', barcode: 'LOT-2026-001',
  currentLocation: { kind: 'site', label: '试剂瓶堆栈 / R1C1', siteUuid: 'site-1', ownerMaterialUuid: 'rack-1' },
}

function response(data: unknown) {
  return { ok: true, status: 200, json: async () => ({ code: 0, data }) } as Response
}

function installApi(writeResponse?: Promise<Response>) {
  let catalog: Array<Record<string, unknown>> = [structuredClone(catalogItem)]
  let inventory = [structuredClone(inventoryItem)]
  const calls: Array<{ url: string; method: string }> = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    calls.push({ url, method })
    if (url.includes('/reagent-infos?')) return response({ items: catalog, total: catalog.length })
    if (url.includes('/reagents?')) return response({ items: inventory, total: inventory.length })
    if (url.includes('/resource-templates')) return response({ items: [], total: 0 })
    if (url.endsWith('/reagent-infos/info-ethanol') && method === 'PUT' && writeResponse) return writeResponse
    throw new Error(`Unexpected request: ${method} ${url}`)
  }))
  return {
    calls,
    replaceCatalog: (items: Array<Record<string, unknown>>) => { catalog = items.map((item) => structuredClone(item)) },
    updateCatalog: (patch: Partial<typeof catalogItem>) => { catalog = catalog.map((item) => ({ ...item, ...patch })) },
    updateInventory: (patch: Partial<typeof inventoryItem>) => { inventory = inventory.map((item) => ({ ...item, ...patch })) },
  }
}

function mountPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onNotify = vi.fn()
  const page = (connected: boolean) => <QueryClientProvider client={client}><ReagentsPage materials={[material]} connected={connected} onNotify={onNotify} /></QueryClientProvider>
  const result = render(page(true))
  return { client, onNotify, disconnect: () => result.rerender(page(false)) }
}

function fieldValue(dialog: HTMLElement, label: string) {
  return within(dialog).getByText(label, { selector: 'dt' }).nextElementSibling
}

function catalogCell(row: HTMLTableRowElement, label: string | RegExp) {
  const table = row.closest('table')!
  const headers = within(table).getAllByRole('columnheader')
  const header = within(table).getByRole('columnheader', { name: label })
  return row.cells[headers.indexOf(header)]
}

function expectFullTimestamps(dialog: HTMLElement) {
  expect(Array.from(dialog.querySelectorAll('time'), (time) => time.getAttribute('datetime'))).toEqual([
    catalogItem.create_time, catalogItem.update_time,
  ])
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('试剂只读详情', () => {
  it('目录共用一个表头，各行保留同列位置，并随筛选后的数据决定可见列', async () => {
    const api = installApi()
    api.replaceCatalog([catalogItem, { uuid: 'info-powder', name: '测试粉体', physical_state: 'solid', aliases: [] }])
    mountPage()
    fireEvent.click(screen.getByRole('button', { name: '目录' }))
    const richRow = (await screen.findByRole('button', { name: '查看试剂目录 乙醇' })).closest('tr')!
    const sparseRow = screen.getByRole('button', { name: '查看试剂目录 测试粉体' }).closest('tr')!
    const table = richRow.closest('table')!
    expect(table.tHead?.rows).toHaveLength(1)
    const headers = within(table).getAllByRole('columnheader')
    for (const name of ['CAS', '分子式', /^分子量/, '物态', /^密度/, 'SMILES', '说明', '纯度']) {
      expect(within(table).getAllByRole('columnheader', { name })).toHaveLength(1)
    }
    expect(richRow.cells).toHaveLength(headers.length)
    expect(sparseRow.cells).toHaveLength(headers.length)
    expect(catalogCell(richRow, 'CAS')).toHaveTextContent('64-17-5')
    expect(catalogCell(richRow, '分子式')).toHaveTextContent('C2H6O')
    expect(catalogCell(richRow, /^密度/)).toHaveTextContent('0.789')
    expect(catalogCell(sparseRow, '物态')).toHaveTextContent('固体')
    for (const name of ['CAS', '分子式', /^分子量/, /^密度/, 'SMILES', '说明', '纯度']) {
      expect(catalogCell(sparseRow, name).textContent?.trim()).toBe('')
    }
    expect(sparseRow).not.toHaveTextContent(/无别名|未知|—|info-powder/)

    fireEvent.change(screen.getByPlaceholderText('搜索名称、别名、CAS 或分子式'), { target: { value: '测试粉体' } })
    expect(within(table).queryByRole('columnheader', { name: 'CAS' })).not.toBeInTheDocument()
    expect(within(table).queryByRole('columnheader', { name: /^密度/ })).not.toBeInTheDocument()
    expect(within(table).queryByRole('columnheader', { name: '纯度' })).not.toBeInTheDocument()
    expect(sparseRow.cells).toHaveLength(within(table).getAllByRole('columnheader').length)
    expect(api.calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('不同稀疏记录的已填字段组成共同列，所有记录都缺失的密度不生成表头', async () => {
    const api = installApi()
    api.replaceCatalog([
      { uuid: 'info-cas', name: '已填 CAS', cas: '64-17-5', aliases: [] },
      { uuid: 'info-formula', name: '已填分子式', molecular_formula: 'H2O', aliases: [] },
    ])
    mountPage()
    fireEvent.click(screen.getByRole('button', { name: '目录' }))
    const casRow = (await screen.findByRole('button', { name: '查看试剂目录 已填 CAS' })).closest('tr')!
    const formulaRow = screen.getByRole('button', { name: '查看试剂目录 已填分子式' }).closest('tr')!
    const table = casRow.closest('table')!
    expect(within(table).queryByRole('columnheader', { name: /^密度/ })).not.toBeInTheDocument()
    expect(catalogCell(casRow, 'CAS')).toHaveTextContent('64-17-5')
    expect(catalogCell(casRow, '分子式').textContent?.trim()).toBe('')
    expect(catalogCell(formulaRow, 'CAS').textContent?.trim()).toBe('')
    expect(catalogCell(formulaRow, '分子式')).toHaveTextContent('H2O')
    const count = within(table).getAllByRole('columnheader').length
    expect(casRow.cells).toHaveLength(count)
    expect(formulaRow.cells).toHaveLength(count)
    expect(api.calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('自定义参数与内置列同名时仍有唯一表头和独立单元格', async () => {
    const api = installApi()
    api.replaceCatalog([{
      ...catalogItem,
      meta_data: { custom_parameters: [
        { name: 'CAS', value: '内部索引' },
        { name: '更新时间', value: '人工复核日期' },
        { name: '操作', value: '避光保存' },
      ] },
    }])
    mountPage()
    fireEvent.click(screen.getByRole('button', { name: '目录' }))
    const row = (await screen.findByRole('button', { name: '查看试剂目录 乙醇' })).closest('tr')!
    const table = row.closest('table')!
    for (const name of ['CAS', '更新时间', '操作', 'CAS（自定义）', '更新时间（自定义）', '操作（自定义）']) {
      expect(within(table).getAllByRole('columnheader', { name })).toHaveLength(1)
    }
    expect(catalogCell(row, 'CAS')).toHaveTextContent('64-17-5')
    expect(catalogCell(row, 'CAS（自定义）')).toHaveTextContent('内部索引')
    expect(catalogCell(row, '更新时间（自定义）')).toHaveTextContent('人工复核日期')
    expect(catalogCell(row, '操作（自定义）')).toHaveTextContent('避光保存')
    expect(within(catalogCell(row, '操作')).getByRole('button', { name: '编辑试剂目录 乙醇' })).toBeEnabled()
  })

  it('目录列表同时展示英文名、别名和理化摘要，名称及详情按钮均可打开完整资料', async () => {
    const { calls } = installApi()
    const { onNotify } = mountPage()
    fireEvent.click(screen.getByRole('button', { name: '目录' }))
    const open = await screen.findByRole('button', { name: '查看试剂目录 乙醇' })
    const row = open.closest('tr')!
    for (const value of ['Ethanol', '酒精', 'EtOH', '64-17-5', 'C2H6O', '46.07', '液体', '0.789', 'CCO', '目录理化信息说明', '99.9%']) {
      expect(row).toHaveTextContent(value)
    }
    expect(within(row.closest('table')!).queryByRole('columnheader', { name: 'InChIKey' })).not.toBeInTheDocument()
    expect(row).not.toHaveTextContent(catalogItem.inchi_key)
    expect(catalogCell(row, '纯度')).toHaveTextContent('99.9%')
    expect(row).not.toHaveTextContent(catalogItem.uuid)
    expect(row).not.toHaveTextContent(/无别名|未知|—/)
    expect(within(row!).getByRole('button', { name: '编辑试剂目录 乙醇' })).toBeEnabled()
    expect(within(row!).getByRole('button', { name: '删除试剂目录 乙醇' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: '乙醇' }))
    const dialog = screen.getByRole('dialog', { name: '乙醇 目录详情' })
    for (const value of ['乙醇', 'Ethanol', '酒精', 'EtOH', '64-17-5', 'C2H6O', 'CCO', catalogItem.inchi_key, '46.07', '0.789', '液体', '目录理化信息说明', '纯度', '99.9%', 'info-ethanol']) {
      expect(dialog).toHaveTextContent(value)
    }
    expect(within(dialog).getByTestId('structure-preview')).toHaveTextContent('CCO')
    expect(fieldValue(dialog, 'supplier')).toHaveTextContent('试剂供应商')
    expect(fieldValue(dialog, '纯度')).toHaveTextContent('滴定')
    expect(fieldValue(dialog, '纯度')).toHaveTextContent('否')
    expect(fieldValue(dialog, 'supplier')).toHaveTextContent('否')
    expect(fieldValue(dialog, 'retry_count')).toHaveTextContent('0')
    expectFullTimestamps(dialog)
    expect(within(dialog).queryByRole('textbox')).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('spinbutton')).not.toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: /关闭/ }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    fireEvent.click(open)
    expect(screen.getByRole('dialog')).toHaveTextContent('目录理化信息说明')
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
    expect(calls.some((call) => call.url.includes('/compounds/'))).toBe(false)
    expect(onNotify).not.toHaveBeenCalled()
  })

  it.each([
    { physicalState: 'solid', stateText: '固体' },
    { physicalState: 'unknown', stateText: undefined },
  ])('精简目录行只展示已填写字段，$physicalState 的空值及标识仍可在详情查看', async ({ physicalState, stateText }) => {
    const api = installApi()
    api.replaceCatalog([{ uuid: 'info-sparse', name: '自配样品', physical_state: physicalState, aliases: [] }])
    mountPage()
    fireEvent.click(screen.getByRole('button', { name: '目录' }))
    const open = await screen.findByRole('button', { name: '查看试剂目录 自配样品' })
    const row = open.closest('tr')!
    expect(row).toHaveTextContent('自配样品')
    if (stateText) expect(row).toHaveTextContent(stateText)
    expect(row).not.toHaveTextContent(/无别名|未知|未填写|—|info-sparse|UUID|CAS|分子式|分子量|SMILES|InChIKey|密度/)
    expect(within(row).getByRole('button', { name: '自配样品' })).toBeEnabled()
    expect(within(row).getByRole('button', { name: '编辑试剂目录 自配样品' })).toBeEnabled()
    expect(within(row).getByRole('button', { name: '删除试剂目录 自配样品' })).toBeEnabled()

    fireEvent.click(open)
    const dialog = screen.getByRole('dialog', { name: '自配样品 目录详情' })
    for (const label of ['英文名称', '别名', 'CAS 号', '分子式', '分子量', 'SMILES', 'InChIKey', '参考密度', '说明']) {
      expect(fieldValue(dialog, label)).toHaveTextContent('未填写')
    }
    expect(fieldValue(dialog, '常温物态')).toHaveTextContent(stateText || '未知')
    expect(fieldValue(dialog, '目录 UUID')).toHaveTextContent('info-sparse')
    expect(fieldValue(dialog, '创建时间')).toHaveTextContent('未记录')
    expect(api.calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('目录列表保留已填写的零值，而空自定义参数只留在详情', async () => {
    const api = installApi()
    api.replaceCatalog([{
      uuid: 'info-zero', name: '零值示例', physical_state: 'unknown', aliases: [],
      molecular_weight: 0, density_g_per_ml: 0,
      meta_data: { custom_parameters: [{ name: '残留次数', value: 0 }, { name: '未设参数', value: '' }, { name: '空参数', value: null }] },
    }])
    mountPage()
    fireEvent.click(screen.getByRole('button', { name: '目录' }))
    const open = await screen.findByRole('button', { name: '查看试剂目录 零值示例' })
    const row = open.closest('tr')!
    expect(catalogCell(row, /^分子量/)).toHaveTextContent('0')
    expect(catalogCell(row, /^密度/)).toHaveTextContent('0')
    expect(catalogCell(row, '残留次数')).toHaveTextContent('0')
    expect(row).not.toHaveTextContent(/未设参数|空参数|无别名|未知|—|info-zero/)
    fireEvent.click(open)
    const dialog = screen.getByRole('dialog', { name: '零值示例 目录详情' })
    expect(fieldValue(dialog, '残留次数')).toHaveTextContent('0')
    expect(fieldValue(dialog, '未设参数')).toBeInTheDocument()
    expect(fieldValue(dialog, '空参数')).toHaveTextContent('未填写')
    expect(fieldValue(dialog, '目录 UUID')).toHaveTextContent('info-zero')
    expect(api.calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('库存详情显示本瓶数量、容量、浓度、位置和来源，并保留化学身份', async () => {
    const { calls } = installApi()
    mountPage()
    fireEvent.click(await screen.findByRole('button', { name: '查看试剂详情 乙醇 reagent-1' }))
    const dialog = screen.getByRole('dialog', { name: '乙醇 库存详情' })
    for (const value of ['乙醇', 'Ethanol', '酒精', 'EtOH', '64-17-5', 'C2H6O', 'CCO', catalogItem.inchi_key, '46.07', '0.791', '液体', '50', 'mL', '95', '%', '80', 'R1C1 试剂瓶', '试剂瓶堆栈 / R1C1', 'LOT-2026-001', '本瓶入库盘点说明', 'manual-registration', 'source-reagent', 'dispense-command', 'reagent-1', 'bottle-1', 'info-ethanol']) {
      expect(dialog).toHaveTextContent(value)
    }
    expect(within(dialog).getByTestId('structure-preview')).toHaveTextContent('CCO')
    expect(fieldValue(dialog, '当前余量')).toHaveTextContent('50 mL')
    expect(fieldValue(dialog, '任务预留量')).toHaveTextContent('5 mL')
    expect(fieldValue(dialog, '最大装料量')).toHaveTextContent('80 mL')
    expect(fieldValue(dialog, '容器额定容量')).toHaveTextContent('100 mL')
    expect(fieldValue(dialog, '库存密度')).toHaveTextContent('0.791 g/mL')
    expect(fieldValue(dialog, '密度来源')).toHaveTextContent('入库时的试剂目录')
    expect(fieldValue(dialog, '库存修订')).toHaveTextContent('3')
    expect(fieldValue(dialog, '容器修订')).toHaveTextContent('4')
    expect(fieldValue(dialog, '批次')).toHaveTextContent('batch-01')
    expect(dialog).not.toHaveTextContent('0.789')
    expectFullTimestamps(dialog)
    expect(within(dialog).queryByRole('textbox')).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('spinbutton')).not.toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: /关闭/ }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('零余量、零预留和零浓度保持显示为数值', async () => {
    const api = installApi()
    api.updateInventory({ quantity: 0, active_workflow_reserved_quantity: 0, concentration_value: 0 })
    mountPage()
    fireEvent.click(await screen.findByRole('button', { name: '查看试剂详情 乙醇 reagent-1' }))
    const dialog = screen.getByRole('dialog', { name: '乙醇 库存详情' })
    expect(fieldValue(dialog, '当前余量')).toHaveTextContent('0 mL')
    expect(fieldValue(dialog, '任务预留量')).toHaveTextContent('0 mL')
    expect(fieldValue(dialog, '浓度')).toHaveTextContent('0 %')
    expect(api.calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('断线后仍能从缓存打开两种详情且不触发网络或写请求', async () => {
    const { calls } = installApi()
    const { disconnect } = mountPage()
    await screen.findByRole('button', { name: '查看试剂详情 乙醇 reagent-1' })
    fireEvent.click(screen.getByRole('button', { name: '目录' }))
    await screen.findByRole('button', { name: '查看试剂目录 乙醇' })
    disconnect()
    const callCount = calls.length
    const catalogOpen = screen.getByRole('button', { name: '查看试剂目录 乙醇' })
    expect(catalogOpen).toBeEnabled()
    expect(screen.getByRole('button', { name: '编辑试剂目录 乙醇' })).toBeDisabled()
    fireEvent.click(catalogOpen)
    expect(screen.getByRole('dialog')).toHaveTextContent('目录理化信息说明')
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /关闭/ }))
    fireEvent.click(screen.getByRole('button', { name: '库存' }))
    const inventoryOpen = screen.getByRole('button', { name: '查看试剂详情 乙醇 reagent-1' })
    expect(inventoryOpen).toBeEnabled()
    fireEvent.click(inventoryOpen)
    expect(screen.getByRole('dialog')).toHaveTextContent('本瓶入库盘点说明')
    expect(calls).toHaveLength(callCount)
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('目录详情保持选中的 UUID，并随重新读取的数据更新', async () => {
    const api = installApi()
    const { client } = mountPage()
    fireEvent.click(screen.getByRole('button', { name: '目录' }))
    fireEvent.click(await screen.findByRole('button', { name: '查看试剂目录 乙醇' }))
    api.updateCatalog({ name: '无水乙醇', description: '目录已重新复核', density_g_per_ml: 0.79 })
    await act(async () => { await client.invalidateQueries({ queryKey: ['reagent-infos'] }) })
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent('目录已重新复核'))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('无水乙醇')
    expect(dialog).toHaveTextContent('info-ethanol')
    expect(dialog).not.toHaveTextContent('目录理化信息说明')
    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    expect(api.calls.filter((call) => call.url.includes('/reagent-infos?'))).toHaveLength(2)
    expect(api.calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('库存重新读取后在当前详情显示最新余量与说明', async () => {
    const api = installApi()
    const { client } = mountPage()
    fireEvent.click(await screen.findByRole('button', { name: '查看试剂详情 乙醇 reagent-1' }))
    api.updateInventory({ quantity: 32, description: '最新盘点结果', revision: 4 })
    await act(async () => { await client.invalidateQueries({ queryKey: ['reagents'] }) })
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent('最新盘点结果'))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('32')
    expect(dialog).toHaveTextContent('reagent-1')
    expect(dialog).not.toHaveTextContent('本瓶入库盘点说明')
    expect(api.calls.filter((call) => call.url.includes('/reagents?'))).toHaveLength(2)
    expect(api.calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('切换列表或打开编辑时关闭之前的详情，避免同时出现多个弹窗', async () => {
    const { calls } = installApi()
    mountPage()
    fireEvent.click(await screen.findByRole('button', { name: '查看试剂详情 乙醇 reagent-1' }))
    fireEvent.click(screen.getByRole('button', { name: '目录' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '查看试剂目录 乙醇' }))
    fireEvent.click(screen.getByRole('button', { name: '编辑试剂目录 乙醇' }))
    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    expect(within(screen.getByRole('dialog')).getByRole('heading', { name: '编辑试剂目录' })).toBeInTheDocument()
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('目录保存进行中仍能打开只读详情，且不会重复提交或叠加弹窗', async () => {
    let resolveWrite!: (value: Response) => void
    const writeResponse = new Promise<Response>((resolve) => { resolveWrite = resolve })
    const { calls } = installApi(writeResponse)
    mountPage()
    fireEvent.click(screen.getByRole('button', { name: '目录' }))
    fireEvent.click(await screen.findByRole('button', { name: '编辑试剂目录 乙醇' }))
    fireEvent.click(screen.getByRole('button', { name: '保存修改' }))
    expect(screen.getByRole('button', { name: '编辑试剂目录 乙醇' })).toBeDisabled()
    const open = screen.getByRole('button', { name: '查看试剂目录 乙醇' })
    expect(open).toBeEnabled()
    fireEvent.click(open)
    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    expect(screen.getByRole('dialog', { name: '乙醇 目录详情' })).toHaveTextContent('目录理化信息说明')
    expect(calls.filter((call) => call.method !== 'GET')).toEqual([{ url: '/api/v1/reagent-infos/info-ethanol', method: 'PUT' }])
    await act(async () => resolveWrite(response(catalogItem)))
    await waitFor(() => expect(screen.getByRole('button', { name: '编辑试剂目录 乙醇' })).toBeEnabled())
  })
})
