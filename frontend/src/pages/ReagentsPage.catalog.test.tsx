import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ReagentsPage } from './ReagentsPage'

vi.mock('../components/ChemicalStructurePreview', () => ({
  ChemicalStructurePreview: ({ smiles }: { smiles: string }) => <div data-testid="structure-preview">{smiles}</div>,
}))

type WireRecord = Record<string, unknown>
type ApiCall = { url: string; method: string; body?: WireRecord }

const catalogItem = {
  uuid: 'info-ethanol', name: '乙醇', name_en: 'Ethanol', aliases: ['酒精', 'EtOH'], cas: '64-17-5',
  molecular_formula: 'C2H6O', smiles: 'CCO', inchi_key: 'LFQSCWFLJHTTHZ-UHFFFAOYSA-N',
  molecular_weight: 46.07, density_g_per_ml: 0.789, physical_state: 'liquid', description: '原始目录说明',
  meta_data: { supplier: { id: 'supplier-1', verified: true }, imported_at: '2026-09-01', custom_parameters: [{ name: '纯度', value: '99.9%' }] },
}

function response(data: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => data } as Response
}

function installApi(options: { deleteError?: string; writeResponse?: Promise<Response>; aliases?: string[] } = {}) {
  let items: WireRecord[] = [{ ...structuredClone(catalogItem), aliases: options.aliases ?? catalogItem.aliases }]
  const calls: ApiCall[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    const body = init?.body ? JSON.parse(String(init.body)) as WireRecord : undefined
    calls.push({ url, method, body })
    if (url.includes('/reagent-infos?')) return response({ code: 0, data: { items, total: items.length } })
    if (url.includes('/reagents?')) return response({ code: 0, data: { items: [{
      uuid: 'reagent-1', material_uuid: 'bottle-1', reagent_info_uuid: catalogItem.uuid,
      name: items.find((item) => item.uuid === catalogItem.uuid)?.name || catalogItem.name,
      cas: catalogItem.cas, quantity: 50, quantity_unit: 'mL', revision: 1, container_name: 'R1C1 试剂瓶',
    }], total: 1 } })
    if (url.includes('/resource-templates')) return response({ code: 0, data: { items: [], total: 0 } })
    if (url.includes('/compounds/')) return response({ code: 0, data: { cas: url.split('/').at(-1), status: 'registered', message: '该 CAS 已登记，请使用现有目录项' } })
    if (url.endsWith(`/reagent-infos/${catalogItem.uuid}`) && method === 'PUT') {
      if (options.writeResponse) return options.writeResponse
      items = items.map((item) => item.uuid === catalogItem.uuid ? { ...item, ...body } : item)
      return response({ code: 0, data: items[0] })
    }
    if (url.endsWith('/reagent-infos') && method === 'POST') {
      const created = { uuid: 'info-new', ...body }
      items = [...items, created]
      return response({ code: 0, data: created })
    }
    if (url.endsWith(`/reagent-infos/${catalogItem.uuid}`) && method === 'DELETE') {
      if (options.deleteError) return response({ code: 1, error: { msg: options.deleteError } }, 409)
      items = []
      return response({ code: 0, data: null })
    }
    throw new Error(`Unexpected request: ${method} ${url}`)
  }))
  return calls
}

function mountPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onNotify = vi.fn()
  const page = (connected: boolean) => <QueryClientProvider client={client}><ReagentsPage materials={[]} connected={connected} onNotify={onNotify} /></QueryClientProvider>
  const result = render(page(true))
  return { onNotify, disconnect: () => result.rerender(page(false)) }
}

async function openEdit() {
  fireEvent.click(screen.getByRole('button', { name: '目录' }))
  fireEvent.click(await screen.findByRole('button', { name: `编辑试剂目录 ${catalogItem.name}` }))
  const dialog = screen.getByRole('dialog')
  expect(within(dialog).getByRole('heading', { name: '编辑试剂目录' })).toBeInTheDocument()
  return within(dialog)
}

function changeField(dialog: ReturnType<typeof within>, label: string | RegExp, value: string) {
  fireEvent.change(dialog.getByLabelText(label), { target: { value } })
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('试剂目录维护', () => {
  it('预填全部身份字段，保留元数据，并在编辑后刷新目录和库存名称', async () => {
    const calls = installApi()
    const { onNotify } = mountPage()
    const dialog = await openEdit()

    expect(dialog.getByLabelText(/^CAS 号/)).toHaveValue('64-17-5')
    expect(dialog.getByLabelText('试剂名称 *')).toHaveValue('乙醇')
    expect(dialog.getByLabelText('英文名称')).toHaveValue('Ethanol')
    expect(dialog.getByLabelText(/^别名/)).toHaveValue('酒精\nEtOH')
    expect(dialog.getByLabelText('分子式')).toHaveValue('C2H6O')
    expect(dialog.getByLabelText('SMILES')).toHaveValue('CCO')
    expect(dialog.getByTestId('structure-preview')).toHaveTextContent('CCO')
    expect(dialog.getByLabelText('分子量（g/mol）')).toHaveValue(46.07)
    expect(dialog.getByLabelText('参考密度（g/mL）')).toHaveValue(0.789)
    expect(dialog.getByLabelText('常温物态')).toHaveValue('liquid')
    expect(dialog.getByLabelText('InChIKey')).toHaveValue(catalogItem.inchi_key)
    expect(dialog.getByLabelText('说明')).toHaveValue('原始目录说明')
    expect(dialog.getByLabelText('参数名称')).toHaveValue('纯度')
    expect(dialog.getByLabelText('参数值')).toHaveValue('99.9%')
    expect(dialog.getByRole('button', { name: '保存修改' })).toBeEnabled()

    changeField(dialog, '试剂名称 *', '  无水乙醇  ')
    changeField(dialog, /^别名/, '酒精\nEtOH\n酒精')
    changeField(dialog, '参数值', ' 99.95% ')
    changeField(dialog, '说明', '复核后的目录说明')
    fireEvent.click(dialog.getByRole('button', { name: '保存修改' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const put = calls.find((call) => call.method === 'PUT')
    expect(put?.url).toBe('/api/v1/reagent-infos/info-ethanol')
    expect(put?.body).toMatchObject({
      name: '无水乙醇', name_en: 'Ethanol', cas: '64-17-5', aliases: ['酒精', 'EtOH'],
      molecular_formula: 'C2H6O', smiles: 'CCO', inchi_key: catalogItem.inchi_key,
      molecular_weight: 46.07, density_g_per_ml: 0.789, physical_state: 'liquid', description: '复核后的目录说明',
      meta_data: { supplier: { id: 'supplier-1', verified: true }, imported_at: '2026-09-01', custom_parameters: [{ name: '纯度', value: '99.95%' }] },
    })
    expect(calls.filter((call) => call.url.includes('/compounds/'))).toHaveLength(0)
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0)
    expect(calls.filter((call) => call.url.includes('/reagent-infos?'))).toHaveLength(2)
    expect(calls.filter((call) => call.url.includes('/reagents?'))).toHaveLength(2)
    expect(screen.getByRole('button', { name: '编辑试剂目录 无水乙醇' })).toBeInTheDocument()
    expect(onNotify).toHaveBeenCalledWith(expect.stringContaining('无水乙醇'))
    fireEvent.click(screen.getByRole('button', { name: '库存' }))
    expect(screen.getByRole('button', { name: '编辑 无水乙醇 reagent-1' })).toBeInTheDocument()
  })

  it('清空可选字段时显式发送 null，清除 CAS、别名和参数但保留无关元数据', async () => {
    const calls = installApi()
    mountPage()
    const dialog = await openEdit()
    for (const label of [/^CAS 号/, '英文名称', /^别名/, '分子式', 'SMILES', 'InChIKey', '分子量（g/mol）', '参考密度（g/mL）', '说明']) {
      changeField(dialog, label, '')
    }
    fireEvent.click(dialog.getByRole('button', { name: '删除参数' }))
    fireEvent.click(dialog.getByRole('button', { name: '保存修改' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(calls.find((call) => call.method === 'PUT')?.body).toMatchObject({
      name: '乙醇', name_en: null, cas: '', aliases: [], molecular_formula: null, smiles: null,
      inchi_key: null, molecular_weight: null, density_g_per_ml: null, description: null,
      meta_data: { supplier: { id: 'supplier-1', verified: true }, imported_at: '2026-09-01', custom_parameters: [] },
    })
    fireEvent.click(screen.getByRole('button', { name: '编辑试剂目录 乙醇' }))
    expect(screen.getByLabelText('英文名称')).toHaveValue('')
    expect(screen.getByLabelText('分子量（g/mol）')).toHaveValue(null)
    expect(screen.queryByLabelText('参数名称')).not.toBeInTheDocument()
  })

  it.each([
    { name: '含化学位次逗号', aliases: ['1,2-二氯乙烷', 'DCE'] },
    { name: '含重复项及边缘空白', aliases: [' DCE ', '1,2-二氯乙烷', 'DCE', ' DCE '] },
  ])('只修改其他字段时完整保留$name的原始别名数组', async ({ aliases }) => {
    const calls = installApi({ aliases })
    mountPage()
    const dialog = await openEdit()
    const aliasField = dialog.getByRole('textbox', { name: /^别名/ })
    expect(aliasField.tagName).toBe('TEXTAREA')
    expect(aliasField).toHaveValue(aliases.join('\n'))
    expect(dialog.getByText(/每行一个别名/)).toBeInTheDocument()
    changeField(dialog, '说明', '仅更新说明')
    fireEvent.click(dialog.getByRole('button', { name: '保存修改' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(calls.find((call) => call.method === 'PUT')?.body?.aliases).toEqual(aliases)
  })

  it('主动编辑别名时只按换行拆分，保留每行内的逗号、分号和顿号', async () => {
    const calls = installApi({ aliases: ['1,2-二氯乙烷', 'DCE'] })
    mountPage()
    const dialog = await openEdit()
    changeField(dialog, /^别名/, ' 1,2-二氯乙烷 \nDCE; analytical\n中文，逗号；分号、顿号\n\n')
    fireEvent.click(dialog.getByRole('button', { name: '保存修改' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(calls.find((call) => call.method === 'PUT')?.body?.aliases).toEqual([
      '1,2-二氯乙烷', 'DCE; analytical', '中文，逗号；分号、顿号',
    ])
    fireEvent.click(screen.getByRole('button', { name: '编辑试剂目录 乙醇' }))
    expect(screen.getByLabelText(/^别名/)).toHaveValue('1,2-二氯乙烷\nDCE; analytical\n中文，逗号；分号、顿号')
  })

  it('修改 CAS 后检查重复登记，改回自身 CAS 时恢复保存且不再查询', async () => {
    const calls = installApi()
    mountPage()
    const dialog = await openEdit()
    changeField(dialog, /^CAS 号/, '7732-18-5')
    expect(dialog.getByRole('button', { name: '保存修改' })).toBeDisabled()
    expect(await dialog.findByText('该 CAS 已登记，请使用现有目录项')).toBeInTheDocument()
    expect(dialog.getByRole('button', { name: '保存修改' })).toBeDisabled()
    fireEvent.click(dialog.getByRole('button', { name: '保存修改' }))
    expect(calls.some((call) => call.method === 'PUT')).toBe(false)

    changeField(dialog, /^CAS 号/, catalogItem.cas)
    expect(dialog.getByRole('button', { name: '保存修改' })).toBeEnabled()
    fireEvent.click(dialog.getByRole('button', { name: '保存修改' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(calls.filter((call) => call.url.includes('/compounds/')).map((call) => call.url)).toEqual(['/api/v1/compounds/7732-18-5'])
    expect(calls.filter((call) => call.method === 'PUT')).toHaveLength(1)
    expect(calls.find((call) => call.method === 'PUT')?.body?.meta_data).toEqual(catalogItem.meta_data)
  })

  it('从编辑切换到创建时清空原目录字段及元数据，并仍使用 POST', async () => {
    const calls = installApi()
    mountPage()
    const dialog = await openEdit()
    fireEvent.click(dialog.getByRole('button', { name: '关闭' }))
    fireEvent.click(screen.getByRole('button', { name: '新增试剂目录' }))
    const createDialog = within(screen.getByRole('dialog'))
    expect(createDialog.getByRole('heading', { name: '新增试剂目录' })).toBeInTheDocument()
    expect(createDialog.getByLabelText('试剂名称 *')).toHaveValue('')
    expect(createDialog.getByLabelText(/^CAS 号/)).toHaveValue('')
    expect(createDialog.getByLabelText('InChIKey')).toHaveValue('')
    expect(createDialog.queryByLabelText('参数名称')).not.toBeInTheDocument()
    changeField(createDialog, '试剂名称 *', '自配缓冲液')
    fireEvent.click(createDialog.getByRole('button', { name: '创建' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(calls.find((call) => call.method === 'POST')).toMatchObject({
      url: '/api/v1/reagent-infos', body: { name: '自配缓冲液', cas: '', aliases: [], meta_data: {} },
    })
    expect(calls.some((call) => call.method === 'PUT')).toBe(false)
    expect(screen.getByRole('button', { name: '编辑试剂目录 自配缓冲液' })).toBeInTheDocument()
  })

  it('取消删除不发送请求', async () => {
    const calls = installApi()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    mountPage()
    fireEvent.click(screen.getByRole('button', { name: '目录' }))
    fireEvent.click(await screen.findByRole('button', { name: '删除试剂目录 乙醇' }))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('乙醇'))
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    expect(screen.getByRole('button', { name: '编辑试剂目录 乙醇' })).toBeInTheDocument()
  })

  it('后端因库存引用拒绝删除时提示原因并保留目录项', async () => {
    const calls = installApi({ deleteError: '该试剂目录被库存记录引用，不能删除' })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { onNotify } = mountPage()
    fireEvent.click(screen.getByRole('button', { name: '目录' }))
    fireEvent.click(await screen.findByRole('button', { name: '删除试剂目录 乙醇' }))
    await waitFor(() => expect(onNotify).toHaveBeenCalledWith(expect.stringContaining('该试剂目录被库存记录引用，不能删除')))
    expect(calls.filter((call) => call.method === 'DELETE')).toEqual([{ url: '/api/v1/reagent-infos/info-ethanol', method: 'DELETE', body: undefined }])
    expect(screen.getByRole('button', { name: '编辑试剂目录 乙醇' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '删除试剂目录 乙醇' })).toBeEnabled()
  })

  it('确认删除成功后重新读取目录并移除该行', async () => {
    const calls = installApi()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { onNotify } = mountPage()
    fireEvent.click(screen.getByRole('button', { name: '目录' }))
    fireEvent.click(await screen.findByRole('button', { name: '删除试剂目录 乙醇' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: '编辑试剂目录 乙醇' })).not.toBeInTheDocument())
    expect(screen.getByText('暂无试剂目录')).toBeInTheDocument()
    expect(calls.filter((call) => call.url.includes('/reagent-infos?'))).toHaveLength(2)
    expect(calls.filter((call) => call.method === 'DELETE')).toHaveLength(1)
    expect(onNotify).toHaveBeenCalledWith(expect.stringMatching(/已删除.*乙醇/))
  })

  it('离线时缓存目录仍可查看，但创建、编辑和删除均不可执行', async () => {
    const calls = installApi()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { disconnect } = mountPage()
    fireEvent.click(screen.getByRole('button', { name: '目录' }))
    await screen.findByRole('button', { name: '编辑试剂目录 乙醇' })
    disconnect()
    for (const name of ['新增试剂目录', '编辑试剂目录 乙醇', '删除试剂目录 乙醇']) {
      const button = screen.getByRole('button', { name })
      expect(button).toBeDisabled()
      fireEvent.click(button)
    }
    expect(screen.getByText('乙醇')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(confirm).not.toHaveBeenCalled()
    expect(calls.some((call) => call.method !== 'GET')).toBe(false)
  })

  it('弹窗打开后断线会禁止保存', async () => {
    const calls = installApi()
    const { disconnect } = mountPage()
    const dialog = await openEdit()
    changeField(dialog, '试剂名称 *', '离线修改')
    disconnect()
    expect(dialog.getByRole('button', { name: '保存修改' })).toBeDisabled()
    fireEvent.click(dialog.getByRole('button', { name: '保存修改' }))
    expect(calls.some((call) => call.method === 'PUT')).toBe(false)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('保存进行中禁止重复提交及其他目录写操作', async () => {
    let resolveWrite!: (value: Response) => void
    const writeResponse = new Promise<Response>((resolve) => { resolveWrite = resolve })
    const calls = installApi({ writeResponse })
    const { onNotify } = mountPage()
    const dialog = await openEdit()
    fireEvent.click(dialog.getByRole('button', { name: '保存修改' }))
    const saveButton = dialog.getByRole('button', { name: /保存/ })
    expect(saveButton).toBeDisabled()
    fireEvent.click(saveButton)
    for (const name of ['新增试剂目录', '编辑试剂目录 乙醇', '删除试剂目录 乙醇']) {
      const button = screen.getByRole('button', { name })
      expect(button).toBeDisabled()
      fireEvent.click(button)
    }
    expect(calls.filter((call) => call.method === 'PUT')).toHaveLength(1)
    expect(calls.some((call) => call.method === 'DELETE' || call.method === 'POST')).toBe(false)
    await act(async () => resolveWrite(response({ code: 0, data: catalogItem })))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(onNotify).toHaveBeenCalledTimes(1)
  })
})
