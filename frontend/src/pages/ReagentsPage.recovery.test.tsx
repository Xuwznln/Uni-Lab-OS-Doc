import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { demoMaterials } from '../data/demo'
import { ReagentsPage } from './ReagentsPage'

vi.mock('../components/ChemicalStructurePreview', () => ({ ChemicalStructurePreview: () => null }))

type WireRecord = Record<string, any>
const pendingKey = 'unilab.pending-reagent-dispense:/api/v1'
const target = {
  ...demoMaterials[0], uuid: 'target-material', name: '目标瓶', resourceTemplateUuid: 'bottle-template', isStructural: false,
  capacity: { max_volume_ul: 100000 }, ratedCapacity: { max_volume_ul: 100000 },
}

function response(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

function installApi() {
  let reagent: WireRecord = {
    uuid: 'source', material_uuid: 'source-material', reagent_info_uuid: 'info', name: '乙醇',
    physical_state: 'liquid', quantity: 100, quantity_unit: 'mL', revision: 1,
    container_capacity: { max_volume_ul: 100000 }, rated_capacity: { max_volume_ul: 100000 },
  }
  const commands: WireRecord[] = []
  const writes: WireRecord[] = []
  let targetReagent: WireRecord | undefined
  let loseFirstResponse = false
  let rejectFirstCommand = false
  let reads = 0
  const results = new Map<string, WireRecord>()
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input), method = init?.method || 'GET'
    if (url.includes('/reagent-infos?')) return response({ code: 0, data: { items: [], total: 0 } })
    if (url.includes('/resource-templates?')) return response({ code: 0, data: { items: [{ uuid: 'bottle-template', tags: ['container'] }], total: 1 } })
    if (url.includes('/reagents?')) {
      reads += 1
      const items = [reagent, ...(targetReagent ? [targetReagent] : [])]
      return response({ code: 0, data: { items, total: items.length } })
    }
    if (url.endsWith('/inventory/commands')) {
      const command = JSON.parse(String(init?.body)) as WireRecord
      commands.push(command)
      const previous = results.get(command.command_id)
      if (previous) return response({ ...previous, replayed: true })
      if (rejectFirstCommand && commands.length === 1) {
        const rejection = { command_id: command.command_id, status: 'rejected', error: 'reagent revision has changed', error_code: '4002' }
        results.set(command.command_id, rejection)
        return response(rejection)
      }
      const requested = command.payload.targets[0]
      reagent = { ...reagent, quantity: reagent.quantity - requested.quantity, revision: reagent.revision + 1 }
      targetReagent = { ...reagent, uuid: 'target-reagent', material_uuid: requested.material_uuid, quantity: requested.quantity, revision: 1 }
      const completed = {
        command_id: command.command_id, status: 'completed',
        result: {
          source: { reagent_uuid: reagent.uuid, material_uuid: reagent.material_uuid, quantity: reagent.quantity, quantity_unit: reagent.quantity_unit, revision: reagent.revision },
          targets: [{ reagent_uuid: targetReagent.uuid, material_uuid: targetReagent.material_uuid, quantity: targetReagent.quantity, quantity_unit: targetReagent.quantity_unit, revision: 1 }],
        },
      }
      results.set(command.command_id, completed)
      if (loseFirstResponse && commands.length === 1) throw new TypeError('网络中断')
      return response(completed)
    }
    if (url.endsWith('/reagents/source') && method === 'PUT') {
      const body = JSON.parse(String(init?.body)) as WireRecord
      writes.push(body)
      if (body.expected_revision !== reagent.revision) return response({ code: 4002, error: { msg: 'reagent revision has changed' } })
      reagent = { ...reagent, ...body, revision: reagent.revision + 1 }
      return response({ code: 0, data: reagent })
    }
    throw new Error(`Unexpected request: ${method} ${url}`)
  }))
  return {
    commands, writes,
    changeReagent: (patch: WireRecord) => { reagent = { ...reagent, ...patch } },
    loseResponse: () => { loseFirstResponse = true },
    rejectCommand: () => { rejectFirstCommand = true },
    get reads() { return reads },
  }
}

function mountPage(connected = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, staleTime: 10_000 } } })
  const onNotify = vi.fn()
  const page = (online: boolean) => <QueryClientProvider client={client}><ReagentsPage materials={[target]} connected={online} onNotify={onNotify} /></QueryClientProvider>
  const rendered = render(page(connected))
  return { ...rendered, client, onNotify, disconnect: () => rendered.rerender(page(false)) }
}

async function openDispense(quantity = '10') {
  fireEvent.click(await screen.findByRole('button', { name: '分装 乙醇 source' }))
  fireEvent.click(screen.getByRole('combobox', { name: '目标容器 1' }))
  fireEvent.click(screen.getByRole('option', { name: /目标瓶/ }))
  fireEvent.change(screen.getByRole('spinbutton', { name: '分装量 1' }), { target: { value: quantity } })
}

afterEach(() => {
  window.sessionStorage.clear()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('分装恢复', () => {
  it('明确拒绝后刷新源瓶，修改请求使用新 ID，不重放旧拒绝', async () => {
    const api = installApi(); api.rejectCommand()
    mountPage()
    await openDispense()
    api.changeReagent({ quantity: 80, revision: 2 })
    fireEvent.click(screen.getByRole('button', { name: '确认分装' }))
    await screen.findByText(/分装未执行：reagent revision has changed。请核对/)
    expect(window.sessionStorage.getItem(pendingKey)).toBeNull()
    expect(screen.getByRole('spinbutton', { name: '分装量 1' })).toBeEnabled()
    fireEvent.change(screen.getByRole('spinbutton', { name: '分装量 1' }), { target: { value: '20' } })
    fireEvent.click(screen.getByRole('button', { name: '确认分装' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.commands).toHaveLength(2)
    expect(api.commands[1].command_id).not.toBe(api.commands[0].command_id)
    expect(api.commands[1].payload).toMatchObject({ expected_revision: 2, targets: [{ quantity: 20 }] })
  })

  it('响应丢失后锁定全部输入，即使目标已非空仍按完全相同请求核对，防止重复扣料', async () => {
    const api = installApi(); api.loseResponse()
    const page = mountPage()
    await openDispense()
    fireEvent.click(screen.getByRole('button', { name: '确认分装' }))
    await screen.findByText(/分装结果尚未确认：网络中断/)
    expect(screen.getByRole('spinbutton', { name: '分装量 1' })).toBeDisabled()
    expect(screen.getByRole('combobox', { name: '目标容器 1' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '添加容器' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '关闭' })).toBeDisabled()
    expect(window.sessionStorage.getItem(pendingKey)).not.toBeNull()
    await act(async () => { await page.client.refetchQueries({ queryKey: ['reagents'] }) })
    fireEvent.click(screen.getByRole('button', { name: '核对原分装结果' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.commands).toHaveLength(2)
    expect(api.commands[1]).toEqual(api.commands[0])
    expect(page.onNotify).toHaveBeenCalledWith('已分装到 1 个容器，源瓶剩余 90 mL')
    expect(window.sessionStorage.getItem(pendingKey)).toBeNull()
  })

  it('切页或刷新恢复后仍使用原命令核对，不生成第二次分装', async () => {
    const api = installApi(); api.loseResponse()
    const firstPage = mountPage()
    await openDispense()
    fireEvent.click(screen.getByRole('button', { name: '确认分装' }))
    await screen.findByText(/分装结果尚未确认：网络中断/)
    firstPage.unmount()
    mountPage()
    expect(screen.getByRole('spinbutton', { name: '分装量 1' })).toHaveValue(10)
    expect(screen.getByRole('spinbutton', { name: '分装量 1' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '核对原分装结果' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.commands).toHaveLength(2)
    expect(api.commands[1]).toEqual(api.commands[0])
  })

  it('无法暂存原请求时不发出分装写请求', async () => {
    const api = installApi()
    mountPage()
    await openDispense()
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota exceeded') })
    fireEvent.click(screen.getByRole('button', { name: '确认分装' }))
    await screen.findByText(/无法保存分装恢复信息，本次尚未提交/)
    expect(api.commands).toHaveLength(0)
    expect(screen.getByRole('spinbutton', { name: '分装量 1' })).toBeEnabled()
  })
})

describe('库存刷新与断连', () => {
  it('修订冲突后载入新余量，只有用户重新填写并确认才用新修订提交', async () => {
    const api = installApi()
    mountPage()
    fireEvent.click(await screen.findByRole('button', { name: '编辑 乙醇 source' }))
    api.changeReagent({ quantity: 50, revision: 2 })
    fireEvent.change(screen.getByRole('spinbutton', { name: '数量' }), { target: { value: '90' } })
    fireEvent.click(screen.getByRole('button', { name: '保存修改' }))
    await screen.findByText(/库存已变更，已载入最新余量和规格/)
    expect(screen.getByRole('spinbutton', { name: '数量' })).toHaveValue(50)
    expect(api.writes).toHaveLength(1)
    expect(api.reads).toBe(2)
    fireEvent.change(screen.getByRole('spinbutton', { name: '数量' }), { target: { value: '40' } })
    fireEvent.click(screen.getByRole('button', { name: '保存修改' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.writes.map((body) => [body.expected_revision, body.quantity])).toEqual([[1, 90], [2, 40]])
  })

  it('持续停留页面时轮询更新库存余量', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const api = installApi()
    mountPage()
    await screen.findByRole('button', { name: '编辑 乙醇 source' })
    api.changeReagent({ quantity: 42, revision: 2 })
    await act(async () => { await vi.advanceTimersByTimeAsync(15_100) })
    expect(api.reads).toBeGreaterThan(1)
    fireEvent.click(screen.getByRole('button', { name: '编辑 乙醇 source' }))
    expect(screen.getByRole('spinbutton', { name: '数量' })).toHaveValue(42)
  })

  it('断连后有缓存的行级修改和删除同样禁用', async () => {
    installApi()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const page = mountPage()
    await screen.findByRole('button', { name: '删除 乙醇 source' })
    page.disconnect()
    for (const action of ['删除', '编辑', '分装']) expect(screen.getByRole('button', { name: `${action} 乙醇 source` })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '删除 乙醇 source' }))
    expect(confirm).not.toHaveBeenCalled()
    expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false)
  })
})
