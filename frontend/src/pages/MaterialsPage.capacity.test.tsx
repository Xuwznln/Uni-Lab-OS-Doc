import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { demoMaterials } from '../data/demo'
import { loadMaterialDetail, loadReagents, loadResourceTemplates, updateMaterialCapacity } from '../lib/edgeClient'
import type { MaterialRecord, ReagentRecord } from '../types'
import { MaterialsPage } from './MaterialsPage'

vi.mock('../lib/edgeClient', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/edgeClient')>()
  return { ...actual, loadMaterialDetail: vi.fn(), loadReagents: vi.fn(), loadResourceTemplates: vi.fn(), updateMaterialCapacity: vi.fn() }
})

const bottle: MaterialRecord = {
  ...demoMaterials[0], uuid: 'bottle-1', name: '100 mL 容器', revision: 7,
  resourceTemplateUuid: 'container-template', isStructural: false, sites: [], siteCount: 0, parentUuid: undefined,
  currentLocation: { kind: 'unassigned', label: '待分配库存' },
  capacity: { max_volume_ul: 100000 }, ratedCapacity: { max_volume_ul: 100000 }, config: {},
}

const liquid: ReagentRecord = {
  uuid: 'reagent-1', materialUuid: bottle.uuid, reagentInfoUuid: 'info-1', name: '高密度液体',
  physicalState: 'liquid', quantity: 150, quantityUnit: 'g', densityGPerMl: 2, densitySource: 'dictionary',
  maximumCapacity: { max_volume_ul: 100000 }, ratedCapacity: bottle.ratedCapacity, materialRevision: bottle.revision,
  revision: 3, updatedAt: '',
}

function mountPage(material: MaterialRecord = bottle, reagents: ReagentRecord[] = []) {
  vi.mocked(loadMaterialDetail).mockResolvedValue(material)
  vi.mocked(loadReagents).mockResolvedValue(reagents)
  vi.mocked(loadResourceTemplates).mockResolvedValue([{
    uuid: 'container-template', name: 'container-template', displayName: '试剂容器',
    description: '', resourceType: 'resource', tags: ['container'], availableSites: [],
  }])
  vi.mocked(updateMaterialCapacity).mockResolvedValue({})
  const onNotify = vi.fn()
  const onRefresh = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MaterialsPage materials={[material]} total={1} connected onNotify={onNotify} onRefresh={onRefresh} /></QueryClientProvider>)
  return { onNotify, onRefresh }
}

async function openCapacity() {
  const open = await screen.findByRole('button', { name: '设置最大装料量' })
  await waitFor(() => expect(open).toBeEnabled())
  fireEvent.click(open)
  const dialog = within(screen.getByRole('dialog'))
  expect(dialog.getByRole('heading', { name: '设置最大装料量' })).toBeInTheDocument()
  return dialog
}

beforeEach(() => vi.resetAllMocks())

describe('物料最大装料量编辑', () => {
  it('有试剂时使用库存单位和密度，超额定或低于库存均阻止写入', async () => {
    const { onNotify, onRefresh } = mountPage(bottle, [liquid])
    const dialog = await openCapacity()
    const unit = dialog.getByRole('combobox', { name: '最大装料量单位' })
    expect(unit).toHaveValue('g')
    expect(unit).toBeDisabled()
    const rated = dialog.getByText('容器额定容量').nextElementSibling
    expect(rated).toHaveTextContent('100 mL')
    const maximum = dialog.getByRole('spinbutton', { name: '最大装料量（g）' })
    const save = dialog.getByRole('button', { name: '保存' })
    expect(maximum).toHaveValue(200)
    expect(save).toBeEnabled()
    fireEvent.change(maximum, { target: { value: '201' } })
    expect(save).toBeDisabled()
    expect(dialog.getByRole('alert')).toHaveTextContent(/额定/)
    fireEvent.click(save)
    expect(updateMaterialCapacity).not.toHaveBeenCalled()
    fireEvent.change(maximum, { target: { value: '149' } })
    expect(save).toBeDisabled()
    expect(dialog.getByRole('alert')).toHaveTextContent(/数量|库存|余量/)
    fireEvent.click(save)
    expect(updateMaterialCapacity).not.toHaveBeenCalled()
    fireEvent.change(maximum, { target: { value: '160' } })
    expect(save).toBeEnabled()
    expect(rated).toHaveTextContent('100 mL')
    fireEvent.click(save)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(updateMaterialCapacity).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ uuid: bottle.uuid, revision: 7 }), { max_mass_g: 160 })
    expect(loadMaterialDetail).toHaveBeenCalledTimes(2)
    expect(loadReagents).toHaveBeenCalledTimes(2)
    expect(onRefresh).toHaveBeenCalledOnce()
    expect(onNotify).toHaveBeenCalledWith(expect.stringContaining('已保存'))
  })

  it('300 mL 粉体容器可手填 50 g 上限，空值或低于 30 g 库存时禁止保存', async () => {
    const volume = { max_volume_ul: 300000 }
    const powder: ReagentRecord = { ...liquid, name: '粉体', physicalState: 'solid', quantity: 30, maximumCapacity: volume, ratedCapacity: volume }
    mountPage({ ...bottle, name: '300 mL 粉体容器', capacity: volume, ratedCapacity: volume }, [powder])
    const dialog = await openCapacity()
    expect(dialog.getByRole('combobox', { name: '最大装料量单位' })).toHaveValue('g')
    expect(dialog.getByRole('combobox', { name: '最大装料量单位' })).toBeDisabled()
    expect(dialog.getByText('容器容积').nextElementSibling).toHaveTextContent('300 mL')
    const maximum = dialog.getByRole('spinbutton', { name: '最大装料量（g）' })
    const save = dialog.getByRole('button', { name: '保存' })
    expect(maximum).toBeEnabled()
    expect(maximum).toHaveValue(null)
    expect(save).toBeDisabled()
    expect(dialog.getByRole('alert')).toHaveTextContent(/填写.*最大装料量/)
    expect(dialog.queryByText(/请先补充.*规格/)).not.toBeInTheDocument()
    fireEvent.change(maximum, { target: { value: '29' } })
    expect(save).toBeDisabled()
    expect(dialog.getByRole('alert')).toHaveTextContent(/数量|库存|余量/)
    fireEvent.click(save)
    expect(updateMaterialCapacity).not.toHaveBeenCalled()
    fireEvent.change(maximum, { target: { value: '50' } })
    expect(save).toBeEnabled()
    fireEvent.click(save)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(updateMaterialCapacity).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ uuid: bottle.uuid, revision: 7 }), { max_mass_g: 50 })
  })

  it('缺少液体密度时仍禁止质量计量，已有体积限制按原始规格展示', async () => {
    const current = { max_volume_ul: 50000 }
    mountPage({ ...bottle, capacity: current, config: { capacity: current } }, [{ ...liquid, densityGPerMl: undefined, maximumCapacity: current }])
    await screen.findByRole('button', { name: '设置最大装料量' })
    expect(screen.getByText(/最大装料量：50 mL/)).toBeInTheDocument()
    const dialog = await openCapacity()
    expect(dialog.getByRole('spinbutton', { name: '最大装料量（g）' })).toBeDisabled()
    expect(dialog.getByRole('alert')).toHaveTextContent(/密度/)
    expect(dialog.getByRole('button', { name: '保存' })).toBeDisabled()
    expect(updateMaterialCapacity).not.toHaveBeenCalled()
  })

  it.each([
    { name: '仅有额定容积', material: bottle, unit: 'mL', tooMuch: '101', valid: '80', expected: { max_volume_ul: 80000 } },
    { name: '仅有额定质量', material: { ...bottle, name: '粉体容器', capacity: { max_mass_g: 40 }, ratedCapacity: { max_mass_g: 40 } }, unit: 'g', tooMuch: '41', valid: '30', expected: { max_mass_g: 30 } },
  ])('空容器$name时可选两类单位，已知额定维度仍限制最大值', async ({ material, unit: expectedUnit, tooMuch, valid, expected }) => {
    mountPage(material)
    const dialog = await openCapacity()
    const unit = dialog.getByRole('combobox', { name: '最大装料量单位' })
    expect(unit).toHaveValue(expectedUnit)
    expect(within(unit).getByRole('option', { name: 'mL' })).toBeInTheDocument()
    expect(within(unit).getByRole('option', { name: 'g' })).toBeInTheDocument()
    const maximum = dialog.getByRole('spinbutton', { name: `最大装料量（${expectedUnit}）` })
    fireEvent.change(maximum, { target: { value: tooMuch } })
    const save = dialog.getByRole('button', { name: '保存' })
    expect(save).toBeDisabled()
    expect(dialog.getByRole('alert')).toHaveTextContent(/额定/)
    fireEvent.click(save)
    expect(updateMaterialCapacity).not.toHaveBeenCalled()
    fireEvent.change(maximum, { target: { value: valid } })
    expect(save).toBeEnabled()
    fireEvent.click(save)
    await waitFor(() => expect(updateMaterialCapacity).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ uuid: material.uuid }), expected))
  })

  it('空容器没有额定容量时可编辑已有手填上限，也能明确清除旧配置', async () => {
    const material = { ...bottle, ratedCapacity: undefined, capacity: { max_volume_ul: 80000 }, config: { capacity: { max_volume_ul: 80000 }, vendor: 'retained' } }
    const { onRefresh } = mountPage(material)
    const dialog = await openCapacity()
    expect(dialog.getByRole('spinbutton', { name: /最大装料量/ })).toBeEnabled()
    expect(dialog.getByRole('spinbutton', { name: /最大装料量/ })).toHaveValue(80)
    expect(dialog.getByRole('button', { name: '保存' })).toBeEnabled()
    expect(updateMaterialCapacity).not.toHaveBeenCalled()
    const clear = dialog.getByRole('button', { name: '清除自定义上限' })
    expect(clear).toBeEnabled()
    fireEvent.click(clear)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(updateMaterialCapacity).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ config: material.config, revision: 7 }), {})
    expect(onRefresh).toHaveBeenCalledOnce()
  })

  it('空容器没有额定容量时支持留空，也可选择单位并填写正数上限', async () => {
    mountPage({ ...bottle, ratedCapacity: undefined, capacity: undefined, config: {} })
    const dialog = await openCapacity()
    const unit = dialog.getByRole('combobox', { name: '最大装料量单位' })
    expect(within(unit).getByRole('option', { name: 'mL' })).toBeInTheDocument()
    expect(within(unit).getByRole('option', { name: 'g' })).toBeInTheDocument()
    expect(dialog.getByRole('spinbutton', { name: /最大装料量/ })).toBeEnabled()
    const save = dialog.getByRole('button', { name: '保存' })
    expect(save).toBeEnabled()
    expect(dialog.queryByRole('button', { name: '清除自定义上限' })).not.toBeInTheDocument()
    expect(updateMaterialCapacity).not.toHaveBeenCalled()
    fireEvent.change(unit, { target: { value: 'g' } })
    const maximum = dialog.getByRole('spinbutton', { name: '最大装料量（g）' })
    fireEvent.change(maximum, { target: { value: '0' } })
    expect(save).toBeDisabled()
    fireEvent.click(save)
    expect(updateMaterialCapacity).not.toHaveBeenCalled()
    fireEvent.change(maximum, { target: { value: '50' } })
    expect(save).toBeEnabled()
    fireEvent.click(save)
    await waitFor(() => expect(updateMaterialCapacity).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ uuid: bottle.uuid }), { max_mass_g: 50 }))
  })
})

/**
 * 物料关系区块（当前物料与子物料卡片）不再以 UUID 示人：主行是条码、副行是模板显示名；
 * UUID 仍以 code 形式保留作身份。
 */
it('物料关系链用条码与模板名标注，不再显示 UUID 文本', async () => {
  const childUuid = 'f76eefbc-fdae-5bda-9ecf-79ce13e3254c'
  const child: MaterialRecord = {
    ...bottle, uuid: childUuid, name: childUuid, barcode: 'bottle-6-hole-6',
    parentUuid: bottle.uuid, capacity: undefined, ratedCapacity: undefined,
  }
  vi.mocked(loadMaterialDetail).mockResolvedValue(child)
  vi.mocked(loadReagents).mockResolvedValue([])
  vi.mocked(loadResourceTemplates).mockResolvedValue([{
    uuid: 'container-template', name: 'container-template', displayName: '试剂容器',
    description: '', resourceType: 'resource', tags: ['container'], availableSites: [],
  }])
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MaterialsPage materials={[bottle, child]} total={2} connected onNotify={vi.fn()} onRefresh={vi.fn()} targetMaterialUuid={childUuid} /></QueryClientProvider>)
  const chain = await screen.findByText('物料关系')
  const section = chain.closest('section')!
  // 主行是条码；UUID 不再作为主行文本出现（仍以 code 保留作身份）。
  expect(within(section).getAllByText('bottle-6-hole-6').length).toBeGreaterThan(0)
  expect(within(section).queryByText(childUuid, { selector: 'strong' })).not.toBeInTheDocument()
})
