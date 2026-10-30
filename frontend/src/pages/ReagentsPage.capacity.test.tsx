import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { demoMaterials } from '../data/demo'
import type { MaterialRecord, ReagentInfoRecord, ReagentRecord } from '../types'
import { DispenseForm, EditReagentForm, ReagentsPage, RegisterForm, type RegisterFields } from './ReagentsPage'

const container = { ...demoMaterials[0], uuid: 'bottle', name: '100 mL 瓶', capacity: { max_volume_ul: 100000 }, ratedCapacity: { max_volume_ul: 100000 } }
const water: ReagentInfoRecord = { uuid: 'info', name: '水', aliases: [], physicalState: 'liquid', densityGPerMl: 1, updatedAt: '' }
const powder: ReagentInfoRecord = { uuid: 'powder', name: '粉体', aliases: [], physicalState: 'solid', updatedAt: '' }
const reagent: ReagentRecord = { uuid: 'reagent', materialUuid: 'bottle', reagentInfoUuid: 'info', name: '试剂', physicalState: 'liquid', densityGPerMl: 1, quantity: 50, quantityUnit: 'mL', revision: 1, updatedAt: '', maximumCapacity: { max_volume_ul: 80000 }, ratedCapacity: container.ratedCapacity, materialRevision: 1 }
type EditFields = { quantity: string; concentrationValue: string; concentrationUnit: string; description: string; maximum?: string }
type TargetRow = { id: number; materialUuid: string; quantity: string; maximum?: string }

function Registration({ initial = {}, infos = [water], containers = [container] }: { initial?: Partial<RegisterFields>; infos?: ReagentInfoRecord[]; containers?: MaterialRecord[] }) {
  const [form, setForm] = useState<RegisterFields>({ materialUuid: 'bottle', reagentInfoUuid: 'info', quantity: '50', quantityUnit: 'mL', concentrationValue: '', concentrationUnit: '', description: '', ...initial })
  return <RegisterForm form={form} setForm={setForm} infos={infos} containers={containers} templates={[]} filterTags={[]} saving={false} onSave={vi.fn()} />
}

afterEach(() => vi.unstubAllGlobals())

describe('最大装料量交互', () => {
  it('选择液体后默认使用体积单位，容量位于容器之后、数量之前', () => {
    render(<Registration initial={{ reagentInfoUuid: '', quantity: '', quantityUnit: '' }} />)
    fireEvent.change(screen.getByRole('combobox', { name: /^试剂目录/ }), { target: { value: water.uuid } })
    expect(screen.getByRole('combobox', { name: /^单位/ })).toHaveValue('mL')
    const picker = screen.getByRole('combobox', { name: '试剂容器' })
    const maximum = screen.getByRole('spinbutton', { name: '最大装料量（mL）' })
    const quantity = screen.getByRole('spinbutton', { name: /^数量/ })
    expect(picker.compareDocumentPosition(maximum) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(maximum.compareDocumentPosition(quantity) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it.each([undefined, 0, -1, NaN, Infinity])('液体密度为 %s 时不提供质量单位', (density) => {
    render(<Registration infos={[{ ...water, densityGPerMl: density }]} />)
    const unit = screen.getByRole('combobox', { name: /^单位/ })
    expect(within(unit).getByRole('option', { name: 'mL' })).toBeInTheDocument()
    expect(within(unit).queryByRole('option', { name: 'g' })).not.toBeInTheDocument()
    expect(within(unit).queryByRole('option', { name: 'mg' })).not.toBeInTheDocument()
  })

  it.each(['0', '95'])('液体填写浓度 %s 后不能再使用质量单位', (concentration) => {
    render(<Registration />)
    const unit = screen.getByRole('combobox', { name: /^单位/ })
    expect(within(unit).getByRole('option', { name: 'g' })).toBeInTheDocument()
    fireEvent.change(screen.getByRole('spinbutton', { name: '浓度' }), { target: { value: concentration } })
    expect(within(unit).queryByRole('option', { name: 'g' })).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole('spinbutton', { name: '浓度' }), { target: { value: '' } })
    expect(within(unit).getByRole('option', { name: 'g' })).toBeInTheDocument()
  })

  it('已有不兼容质量单位会保留原值并报错，不会悄悄当作体积数量', () => {
    render(<Registration infos={[{ ...water, densityGPerMl: undefined }]} initial={{ quantityUnit: 'g', quantity: '50' }} />)
    const unit = screen.getByRole('combobox', { name: /^单位/ })
    expect(unit).toHaveValue('g')
    expect(within(unit).getByRole('option', { name: /g/ })).toBeDisabled()
    expect(screen.getByRole('spinbutton', { name: /^数量/ })).toHaveValue(50)
    expect(screen.getByRole('button', { name: '确认录入' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(/密度/)
  })

  it('固体只提供质量单位，从液体切换后清空不兼容的数量和装料上限', () => {
    render(<Registration infos={[water, powder]} />)
    fireEvent.change(screen.getByRole('spinbutton', { name: '最大装料量（mL）' }), { target: { value: '80' } })
    fireEvent.change(screen.getByRole('combobox', { name: /^试剂目录/ }), { target: { value: powder.uuid } })
    const unit = screen.getByRole('combobox', { name: /^单位/ })
    expect(unit).toHaveValue('g')
    expect(within(unit).getAllByRole('option').every((option) => ['g', 'mg', 'kg'].includes((option as HTMLOptionElement).value))).toBe(true)
    expect(screen.getByRole('spinbutton', { name: /^数量/ })).toHaveValue(null)
    expect(screen.getByRole('spinbutton', { name: '最大装料量（g）' })).toHaveValue(null)
    expect(screen.getByRole('button', { name: '确认录入' })).toBeDisabled()
  })

  it('物态未知时禁止录入，已有数量和单位不能绕过限制', () => {
    render(<Registration infos={[{ ...water, physicalState: 'unknown' }]} />)
    expect(screen.getByRole('button', { name: '确认录入' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(/物态/)
  })

  it('录入只显示一个最大装料量，默认带入容器容量并即时校验', () => {
    render(<Registration />)
    const maximum = screen.getByRole('spinbutton', { name: '最大装料量（mL）' })
    const save = screen.getByRole('button', { name: '确认录入' })
    expect(maximum).toHaveValue(100)
    expect(screen.getAllByRole('spinbutton', { name: /最大装料量/ })).toHaveLength(1)
    expect(screen.queryByText(/本次装料上限|配置此容器的规格/)).not.toBeInTheDocument()
    expect(screen.queryByRole('spinbutton', { name: /容器规格/ })).not.toBeInTheDocument()
    fireEvent.change(maximum, { target: { value: '40' } })
    expect(save).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('数量超过最大装料量 40 mL')
    fireEvent.change(maximum, { target: { value: '101' } })
    expect(save).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(/额定/)
    fireEvent.change(maximum, { target: { value: '60' } })
    expect(save).toBeEnabled()
    fireEvent.change(maximum, { target: { value: '' } })
    expect(save).toBeEnabled()
  })

  it('清空最大装料量后仍阻止数量超过领域包默认容量', () => {
    render(<Registration initial={{ quantity: '101', maximum: '' }} />)
    expect(screen.getByRole('button', { name: '确认录入' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('数量超过最大装料量 100 mL')
  })

  it('切换同类单位时同时换算数量和最大装料量', () => {
    render(<Registration />)
    fireEvent.change(screen.getByRole('spinbutton', { name: '最大装料量（mL）' }), { target: { value: '80' } })
    fireEvent.change(screen.getByRole('combobox', { name: /^单位/ }), { target: { value: 'L' } })
    expect(screen.getByRole('spinbutton', { name: '最大装料量（L）' })).toHaveValue(0.08)
    expect(screen.getByRole('spinbutton', { name: /^数量/ })).toHaveValue(0.05)
    expect(screen.getByRole('button', { name: '确认录入' })).toBeEnabled()
    fireEvent.change(screen.getByRole('combobox', { name: /^单位/ }), { target: { value: 'mL' } })
    expect(screen.getByRole('spinbutton', { name: /^数量/ })).toHaveValue(50)
    expect(screen.getByRole('spinbutton', { name: '最大装料量（mL）' })).toHaveValue(80)
  })

  it('有效液体密度用于换算数量和最大装料量，额定规格始终固定显示', () => {
    render(<Registration infos={[{ ...water, densityGPerMl: 2 }]} />)
    const rated = screen.getByText('容器额定容量').nextElementSibling
    expect(rated).toHaveTextContent('100 mL')
    expect(screen.queryByRole('spinbutton', { name: /额定容量/ })).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole('spinbutton', { name: '最大装料量（mL）' }), { target: { value: '80' } })
    fireEvent.change(screen.getByRole('combobox', { name: /^单位/ }), { target: { value: 'g' } })
    expect(screen.getByRole('spinbutton', { name: /^数量/ })).toHaveValue(100)
    expect(screen.getByRole('spinbutton', { name: '最大装料量（g）' })).toHaveValue(160)
    expect(screen.getByRole('button', { name: '确认录入' })).toBeEnabled()
    expect(rated).toHaveTextContent('100 mL')
    expect(rated).not.toHaveTextContent('g')
  })

  it('通过搜索选择容器后带入其容量，并可直接校验录入量', () => {
    render(<Registration initial={{ materialUuid: '', quantity: '50' }} />)
    const picker = screen.getByRole('combobox', { name: '试剂容器' })
    fireEvent.click(picker)
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索试剂容器' }), { target: { value: '100 mL' } })
    fireEvent.click(screen.getByRole('option', { name: /^100 mL 瓶/ }))
    expect(picker).toHaveTextContent('100 mL 瓶')
    expect(screen.getByRole('spinbutton', { name: '最大装料量（mL）' })).toHaveValue(100)
    expect(screen.getByRole('button', { name: '确认录入' })).toBeEnabled()
  })

  it('水以质量计量时仍受 100 mL 额定容积约束，不能用 200 g 自定义上限绕过', () => {
    render(<Registration initial={{ quantity: '200', quantityUnit: 'g' }} />)
    const save = screen.getByRole('button', { name: '确认录入' })
    expect(save).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(/超过|额定/)
    fireEvent.change(screen.getByRole('spinbutton', { name: '最大装料量（g）' }), { target: { value: '200' } })
    expect(save).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(/额定/)
  })

  it('300 mL 粉体容器允许手填 50 g 最大装料量，并按该上限校验 30 g 装料', () => {
    const powderContainer = { ...container, name: '300 mL 粉体瓶', capacity: { max_volume_ul: 300000 }, ratedCapacity: { max_volume_ul: 300000 } }
    render(<Registration infos={[{ ...powder, densityGPerMl: 2 }]} containers={[powderContainer]} initial={{ reagentInfoUuid: powder.uuid, quantityUnit: 'g', quantity: '30' }} />)
    const maximum = screen.getByRole('spinbutton', { name: '最大装料量（g）' })
    const save = screen.getByRole('button', { name: '确认录入' })
    expect(screen.getByText('容器容积').nextElementSibling).toHaveTextContent('300 mL')
    expect(maximum).toBeEnabled()
    expect(maximum).toHaveValue(null)
    expect(save).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(/填写最大装料量/)
    expect(screen.getByRole('alert')).not.toHaveTextContent(/请先补充.*规格|额定装料质量/)
    fireEvent.change(maximum, { target: { value: '50' } })
    expect(save).toBeEnabled()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole('spinbutton', { name: /^数量/ }), { target: { value: '51' } })
    expect(save).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('数量超过最大装料量 50 g')
    fireEvent.change(screen.getByRole('spinbutton', { name: /^数量/ }), { target: { value: '30' } })
    fireEvent.change(maximum, { target: { value: '' } })
    expect(save).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(/填写最大装料量/)
  })

  it('缺少额定容量时允许填写最大装料量，留空或超出该值仍阻止录入', () => {
    render(<Registration containers={[{ ...container, ratedCapacity: undefined, capacity: undefined }]} />)
    const maximum = screen.getByRole('spinbutton', { name: '最大装料量（mL）' })
    const save = screen.getByRole('button', { name: '确认录入' })
    expect(maximum).toBeEnabled()
    expect(save).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(/填写最大装料量/)
    fireEvent.change(maximum, { target: { value: '100' } })
    expect(save).toBeEnabled()
    fireEvent.change(screen.getByRole('spinbutton', { name: /^数量/ }), { target: { value: '101' } })
    expect(save).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('数量超过最大装料量 100 mL')
    fireEvent.change(maximum, { target: { value: '' } })
    expect(save).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(/填写最大装料量/)
  })

  it('编辑默认带入现有最大装料量，清空恢复领域包容量并校验余量', () => {
    function Form() {
      const [form, setForm] = useState<EditFields>({ quantity: '50', concentrationValue: '', concentrationUnit: '', description: '' })
      return <EditReagentForm target={reagent} form={form} setForm={setForm} saving={false} onSave={vi.fn()} />
    }
    render(<Form />)
    const maximum = screen.getByRole('spinbutton', { name: '最大装料量（mL）' })
    const save = screen.getByRole('button', { name: '保存修改' })
    expect(maximum).toHaveValue(80)
    expect(screen.getAllByRole('spinbutton', { name: /最大装料量/ })).toHaveLength(1)
    fireEvent.change(maximum, { target: { value: '49' } })
    expect(save).toBeDisabled()
    fireEvent.change(maximum, { target: { value: '101' } })
    expect(save).toBeDisabled()
    fireEvent.change(maximum, { target: { value: '' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: '数量' }), { target: { value: '90' } })
    expect(save).toBeEnabled()
    fireEvent.change(screen.getByRole('spinbutton', { name: '数量' }), { target: { value: '101' } })
    expect(save).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('数量超过最大装料量 100 mL')
  })

  it('旧记录缺少物态和额定容量时仍可减量，但加量或变更浓度会被阻止', () => {
    const legacy = { ...reagent, physicalState: 'unknown', ratedCapacity: undefined }
    function Form() {
      const [form, setForm] = useState<EditFields>({ quantity: '40', concentrationValue: '', concentrationUnit: '', description: '' })
      return <EditReagentForm target={legacy} form={form} setForm={setForm} saving={false} onSave={vi.fn()} />
    }
    render(<Form />)
    const save = screen.getByRole('button', { name: '保存修改' })
    expect(save).toBeEnabled()
    fireEvent.change(screen.getByRole('spinbutton', { name: '数量' }), { target: { value: '51' } })
    expect(save).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(/物态/)
    fireEvent.change(screen.getByRole('spinbutton', { name: '数量' }), { target: { value: '40' } })
    expect(save).toBeEnabled()
    fireEvent.change(screen.getByRole('spinbutton', { name: '浓度值' }), { target: { value: '10' } })
    expect(save).toBeDisabled()
  })

  it('分装每个目标只显示一个质量上限，并使用该目标的容量', () => {
    const targets = [
      { ...container, uuid: 'target-1', capacity: { max_volume_ul: 300000, max_mass_g: 10 }, ratedCapacity: { max_volume_ul: 300000, max_mass_g: 10 } },
      { ...container, uuid: 'target-2', capacity: { max_mass_g: 20 }, ratedCapacity: { max_mass_g: 20 } },
    ]
    function Form() {
      const [rows, setRows] = useState<TargetRow[]>([{ id: 1, materialUuid: 'target-1', quantity: '10' }, { id: 2, materialUuid: 'target-2', quantity: '20' }])
      return <DispenseForm source={{ ...reagent, physicalState: 'solid', quantity: 50, quantityUnit: 'g' }} rows={rows} setRows={setRows} containers={targets} templates={[]} filterTags={[]} preferredTag="" saving={false} onSave={vi.fn()} />
    }
    render(<Form />)
    const firstMaximum = screen.getByRole('spinbutton', { name: '目标 1 最大装料量（g）' })
    expect(firstMaximum).toHaveValue(10)
    expect(screen.getByRole('spinbutton', { name: '目标 2 最大装料量（g）' })).toHaveValue(20)
    expect(screen.getAllByRole('spinbutton', { name: /最大装料量/ })).toHaveLength(2)
    expect(screen.getByRole('combobox', { name: '目标容器 1' }).compareDocumentPosition(firstMaximum) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(firstMaximum.compareDocumentPosition(screen.getByRole('spinbutton', { name: '分装量 1' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByRole('button', { name: '确认分装' })).toBeEnabled()
    fireEvent.change(screen.getByRole('spinbutton', { name: '分装量 1' }), { target: { value: '11' } })
    expect(screen.getByRole('button', { name: '确认分装' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('数量超过最大装料量 10 g')
    fireEvent.change(firstMaximum, { target: { value: '' } })
    expect(screen.getByRole('button', { name: '确认分装' })).toBeDisabled()
    fireEvent.change(screen.getByRole('spinbutton', { name: '分装量 1' }), { target: { value: '10' } })
    expect(screen.getByRole('button', { name: '确认分装' })).toBeEnabled()
  })

  it.each([
    { name: '水以质量计量超过额定容积', source: { ...reagent, quantity: 1000, quantityUnit: 'g' }, target: container, quantity: '200', maximum: '200', error: /额定/ },
    { name: '粉体没有质量上限且未填写最大装料量', source: { ...reagent, physicalState: 'solid', quantity: 1000, quantityUnit: 'g' }, target: container, quantity: '50', maximum: '', error: /填写最大装料量/ },
    { name: '目标没有额定容量且未填写最大装料量', source: { ...reagent, quantity: 1000 }, target: { ...container, ratedCapacity: undefined, capacity: undefined }, quantity: '50', maximum: '', error: /填写最大装料量/ },
    { name: '源试剂物态未知', source: { ...reagent, physicalState: 'unknown', quantity: 1000 }, target: container, quantity: '50', maximum: '100', error: /物态/ },
    { name: '有浓度的液体按质量分装', source: { ...reagent, quantity: 1000, quantityUnit: 'g', concentrationValue: 95, concentrationUnit: '%' }, target: container, quantity: '50', maximum: '100', error: /浓度|单位|计量/ },
  ])('分装阻止 $name，自定义上限不能绕过', ({ source, target, quantity, maximum, error }) => {
    function Form() {
      const [rows, setRows] = useState<TargetRow[]>([{ id: 1, materialUuid: target.uuid, quantity, maximum }])
      return <DispenseForm source={source} rows={rows} setRows={setRows} containers={[target]} templates={[]} filterTags={[]} preferredTag="" saving={false} onSave={vi.fn()} />
    }
    render(<Form />)
    expect(screen.getByRole('button', { name: '确认分装' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(error)
  })

  it.each([
    { name: '只有容积的粉体容器', source: { ...reagent, physicalState: 'solid', quantity: 1000, quantityUnit: 'g' }, target: { ...container, capacity: { max_volume_ul: 300000 }, ratedCapacity: { max_volume_ul: 300000 } }, quantity: '30', maximum: '50', unit: 'g' },
    { name: '未配置额定容量的液体容器', source: { ...reagent, quantity: 1000 }, target: { ...container, ratedCapacity: undefined, capacity: undefined }, quantity: '30', maximum: '50', unit: 'mL' },
  ])('分装到$name时允许使用手动最大装料量，并阻止超量', ({ source, target, quantity, maximum, unit }) => {
    function Form() {
      const [rows, setRows] = useState<TargetRow[]>([{ id: 1, materialUuid: target.uuid, quantity, maximum }])
      return <DispenseForm source={source} rows={rows} setRows={setRows} containers={[target]} templates={[]} filterTags={[]} preferredTag="" saving={false} onSave={vi.fn()} />
    }
    render(<Form />)
    const maximumInput = screen.getByRole('spinbutton', { name: `目标 1 最大装料量（${unit}）` })
    const save = screen.getByRole('button', { name: '确认分装' })
    expect(maximumInput).toBeEnabled()
    expect(save).toBeEnabled()
    fireEvent.change(screen.getByRole('spinbutton', { name: '分装量 1' }), { target: { value: '51' } })
    expect(save).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(`数量超过最大装料量 50 ${unit}`)
    fireEvent.change(maximumInput, { target: { value: '' } })
    expect(save).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(/填写最大装料量/)
  })

  it.each(['maximum_capacity', 'loading_limits'])('历史以一个最大装料量显示 %s 的前后值', async (capacityKey) => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      let items: unknown[] = []
      if (url.includes('/reagents?')) items = [{ uuid: 'reagent', material_uuid: 'bottle', reagent_info_uuid: 'info', name: '试剂', physical_state: 'liquid', density_g_per_ml: 1, quantity: 50, quantity_unit: 'mL', revision: 2, maximum_capacity: { max_volume_ul: 60000 }, rated_capacity: { max_volume_ul: 100000 }, material_revision: 2 }]
      if (url.includes('/reagent-history')) items = [{ uuid: 'history', material_uuid: 'bottle', subject_uuid: 'reagent', event_type: 'adjust', quantity_delta: 0, quantity_unit: 'mL', revision: 2, recorded_at: '2026-09-07T01:00:00Z', changes: { previous: { [capacityKey]: { max_volume_ul: 80000 } }, result: { quantity: 50, [capacityKey]: { max_volume_ul: 60000 } } } }]
      return { ok: true, status: 200, json: async () => ({ code: 0, data: { items, total: items.length } }) } as Response
    }))
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ReagentsPage materials={[container]} connected onNotify={vi.fn()} /></QueryClientProvider>)
    fireEvent.click(await screen.findByRole('button', { name: '查看操作历史 试剂 reagent' }))
    const history = await screen.findByRole('dialog', { name: '试剂 操作历史' })
    expect(await within(history).findByText('80 mL → 60 mL')).toBeInTheDocument()
    expect(within(history).getByText('最大装料量')).toBeInTheDocument()
    expect(within(history).queryByText('容器规格')).not.toBeInTheDocument()
    expect(within(history).getByText('0 mL')).toBeInTheDocument()
  })
})
