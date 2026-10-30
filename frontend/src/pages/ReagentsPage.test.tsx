import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ReagentsPage } from './ReagentsPage'

afterEach(() => vi.unstubAllGlobals())

function response(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as Response
}

describe('ReagentsPage', () => {
  it('disables reagent mutations while showing a stale read-only snapshot', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><ReagentsPage materials={[]} connected={false} onNotify={vi.fn()} /></QueryClientProvider>)

    expect(screen.getByRole('button', { name: '新增试剂目录' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '录入试剂' })).toBeDisabled()
  })

  it('opens the immutable operation history for a reagent container', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/reagent-infos?')) return response({ code: 0, data: { items: [], total: 0 } })
      if (url.includes('/reagents?')) return response({ code: 0, data: { items: [{
        uuid: 'reagent-1', material_uuid: 'material-1', reagent_info_uuid: 'info-1', name: '乙醇',
        cas: '64-17-5', quantity: 500, quantity_unit: 'mL', revision: 1,
        container_name: '乙醇试剂瓶', container_barcode: 'R-001',
      }], total: 1 } })
      if (url.includes('/materials/material-1/reagent-history')) return response({ code: 0, data: { items: [{
        uuid: 'history-1', material_uuid: 'material-1', subject_uuid: 'reagent-1', event_type: 'add',
        operator_type: 'frontend', quantity_delta: 500, quantity_unit: 'mL', revision: 1,
        recorded_at: '2026-09-02T01:02:03.000Z', changes: { result: { quantity: 500, quantity_unit: 'mL' } },
        extension: { source: 'frontend:workbench' },
      }], has_more: false } })
      throw new Error(`Unexpected URL: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><ReagentsPage materials={[]} connected onNotify={vi.fn()} /></QueryClientProvider>)

    const historyButton = await screen.findByRole('button', { name: '查看操作历史 乙醇 reagent-1' })
    fireEvent.click(historyButton)

    const drawer = await screen.findByRole('dialog', { name: '乙醇 操作历史' })
    expect(await within(drawer).findByText('录入 / 补充')).toBeInTheDocument()
    expect(within(drawer).getByText('+500 mL')).toBeInTheDocument()
    expect(within(drawer).getByText('frontend:workbench')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/materials/material-1/reagent-history'))).toBe(true)
  })
})

describe('ReagentsPage edit and delete', () => {
  function stubFetch(calls: Array<{ url: string; method: string; body?: unknown }>) {
    const reagent = {
      uuid: 'reagent-1', material_uuid: 'material-1', reagent_info_uuid: 'info-1', name: '乙醇',
      cas: '64-17-5', quantity: 60, quantity_unit: 'mL', revision: 2, container_name: 'R3C2 试剂瓶',
      meta_data: { source_reagent_uuid: 'reagent-0', dispense_command_id: 'cmd-1' },
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input); const method = init?.method || 'GET'
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined })
      if (url.includes('/reagent-infos?')) return response({ code: 0, data: { items: [], total: 0 } })
      if (url.includes('/resource-templates')) return response({ code: 0, data: { items: [], total: 0 } })
      if (url.includes('/reagents?')) return response({ code: 0, data: { items: [reagent], total: 1 } })
      if (url.endsWith('/reagents/reagent-1') && method === 'PUT') return response({ code: 0, data: { ...reagent, quantity: 45, revision: 3 } })
      if (url.endsWith('/reagents/reagent-1') && method === 'DELETE') return response({ code: 0 })
      throw new Error(`Unexpected URL: ${method} ${url}`)
    }))
  }

  it('edits the quantity with optimistic revision and keeps dispense lineage metadata', async () => {
    const calls: Array<{ url: string; method: string; body?: any }> = []
    stubFetch(calls)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><ReagentsPage materials={[]} connected onNotify={vi.fn()} /></QueryClientProvider>)
    fireEvent.click(await screen.findByRole('button', { name: '编辑 乙醇 reagent-1' }))
    const quantity = screen.getByLabelText('数量') as HTMLInputElement
    expect(quantity.value).toBe('60')
    fireEvent.change(quantity, { target: { value: '45' } })
    fireEvent.change(screen.getByLabelText('说明'), { target: { value: '盘点复核' } })
    fireEvent.click(screen.getByRole('button', { name: '保存修改' }))
    const put = await vi.waitFor(() => { const found = calls.find((call) => call.method === 'PUT'); if (!found) throw new Error('no PUT yet'); return found })
    expect(put.url).toBe('/api/v1/reagents/reagent-1')
    expect(put.body).toMatchObject({ quantity: 45, quantity_unit: 'mL', expected_revision: 2, description: '盘点复核', meta_data: { source_reagent_uuid: 'reagent-0', dispense_command_id: 'cmd-1' } })
  })

  it('deletes a reagent record after confirmation', async () => {
    const calls: Array<{ url: string; method: string }> = []
    stubFetch(calls)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><ReagentsPage materials={[]} connected onNotify={vi.fn()} /></QueryClientProvider>)
    fireEvent.click(await screen.findByRole('button', { name: '删除 乙醇 reagent-1' }))
    await vi.waitFor(() => { if (!calls.some((call) => call.method === 'DELETE')) throw new Error('no DELETE yet') })
    expect(calls.find((call) => call.method === 'DELETE')?.url).toBe('/api/v1/reagents/reagent-1')
  })
})
