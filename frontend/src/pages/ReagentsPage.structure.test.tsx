import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ReagentsPage } from './ReagentsPage'

type ApiCall = { url: string; method: string }

function mountCatalog(items: Record<string, unknown>[]) {
  const calls: ApiCall[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    calls.push({ url, method })
    if (method !== 'GET') throw new Error(`列表预览不应写入数据：${method} ${url}`)
    const records = url.includes('/reagent-infos?') ? items
      : url.includes('/reagents?') || url.includes('/resource-templates?') ? [] : undefined
    if (!records) throw new Error(`列表预览不应请求其他接口：${url}`)
    return { ok: true, status: 200, json: async () => ({ code: 0, data: { items: records, total: records.length } }) } as Response
  }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ReagentsPage materials={[]} connected onNotify={vi.fn()} /></QueryClientProvider>)
  fireEvent.click(screen.getByRole('button', { name: '目录' }))
  return calls
}

function expectOnlyListReads(calls: ApiCall[]) {
  expect(calls).toHaveLength(3)
  expect(calls.every(({ method }) => method === 'GET')).toBe(true)
  expect(calls.some(({ url }) => url.includes('/compounds/') || url.includes('/structure-3d'))).toBe(false)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
}

beforeEach(() => {
  // jsdom 无文字测量画布；其余解析和 SVG 绘制均使用真实绘图库。
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('试剂目录列表直接展示 2D 结构', () => {
  it('无需打开详情即可把乙醇和水的 SMILES 绘制为各自可访问的真实 SVG', async () => {
    const calls = mountCatalog([
      { uuid: 'ethanol', name: '乙醇', smiles: 'CCO', aliases: ['酒精'], physical_state: 'liquid' },
      { uuid: 'water', name: '水', smiles: 'O', aliases: [], physical_state: 'liquid' },
      { uuid: 'custom', name: '自配试剂', aliases: [], physical_state: 'unknown' },
    ])

    const structureHeader = await screen.findByRole('columnheader', { name: /2D/ })
    const headers = screen.getAllByRole('columnheader')
    const structureColumn = headers.indexOf(structureHeader)
    expect(screen.getAllByRole('columnheader', { name: /2D/ })).toHaveLength(1)
    for (const name of ['乙醇', '水']) {
      const svg = await screen.findByRole('img', { name: `${name} 2D 分子结构` }, { timeout: 3000 })
      expect(svg.tagName.toLowerCase()).toBe('svg')
      expect(svg).toHaveAttribute('viewBox')
      expect(svg.querySelector('path, line, text')).not.toBeNull()
      expect(svg).toHaveTextContent('O')
      const row = svg.closest('tr')
      expect(row).not.toBeNull()
      expect(within(row!).getByRole('button', { name })).toBeInTheDocument()
      const cells = within(row!).getAllByRole('cell')
      expect(cells).toHaveLength(headers.length)
      expect(cells[structureColumn]).toContainElement(svg)
    }
    const emptyRow = screen.getByRole('button', { name: '自配试剂' }).closest('tr')
    expect(emptyRow).not.toBeNull()
    const emptyCells = within(emptyRow!).getAllByRole('cell')
    expect(emptyCells).toHaveLength(headers.length)
    expect(emptyCells[structureColumn]).toBeEmptyDOMElement()
    expect(screen.getAllByRole('img')).toHaveLength(2)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expectOnlyListReads(calls)
  })

  it.each([undefined, null, '   '])('未配置 SMILES（%s）的目录行不留结构空框或输入提示', async (smiles) => {
    const calls = mountCatalog([{ uuid: 'custom', name: '自配试剂', smiles, aliases: [], physical_state: 'unknown' }])
    const name = await screen.findByRole('button', { name: '自配试剂' })
    const row = name.closest('tr')
    expect(row).not.toBeNull()
    expect(row!.querySelector('svg[role="img"]')).toBeNull()
    expect(within(row!).queryByText(/2D|输入或查询 SMILES 后显示结构式/)).not.toBeInTheDocument()
    expect(within(row!).queryByRole('status')).not.toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: /2D/ })).not.toBeInTheDocument()
    expectOnlyListReads(calls)
  })

  it('无效 SMILES 在对应行提示错误，不影响其他结构，也不触发查询或写入', async () => {
    const calls = mountCatalog([
      { uuid: 'invalid', name: '待核对试剂', smiles: '[C', aliases: [], physical_state: 'unknown' },
      { uuid: 'water', name: '水', smiles: 'O', aliases: [], physical_state: 'liquid' },
    ])
    const name = await screen.findByRole('button', { name: '待核对试剂' })
    const row = name.closest('tr')
    expect(row).not.toBeNull()
    expect(await within(row!).findByRole('alert', {}, { timeout: 3000 })).toHaveTextContent(/无法解析|SMILES/)
    expect(within(row!).queryByRole('img')).not.toBeInTheDocument()
    expect(await screen.findByRole('img', { name: '水 2D 分子结构' }, { timeout: 3000 })).toHaveTextContent('O')
    expectOnlyListReads(calls)
  })
})
