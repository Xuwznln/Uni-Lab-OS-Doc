import { fireEvent, render as renderUi, screen, waitFor, within } from '@testing-library/react'
import type { ReactElement, ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as edgeClient from '../lib/edgeClient'
import type { TaskMaterialEvent } from '../lib/edgeClient'
import { MaterialResourceCanvas } from './MaterialResourceCanvas'
import { ResourceHoverCard } from './ResourceHoverCard'
import { buildResourceUsage } from '../lib/resourceUsage'
import { transferSnapshot, usageSnapshot, usageTask } from '../test/resourceUsageFixtures'
import type { MaterialUsageContext } from './MaterialUsagePanel'

// 画布的"物料变化"面板读取任务历史接口，渲染需要查询上下文。
function render(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return renderUi(ui, { wrapper: ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider> })
}
function event(overrides: Partial<TaskMaterialEvent>): TaskMaterialEvent {
  return { uuid: 'e1', occurredAt: '2026-09-25T04:54:34Z', kind: 'moved', materialUuid: 'sample', materialName: '测试烧杯', stepPath: ['1 上样上料'], nodeName: '确认放下并登记位置', taskUuid: 'task-1', ...overrides }
}
function context(snapshot = transferSnapshot()): MaterialUsageContext {
  return { model: buildResourceUsage(snapshot, [usageTask()]), tasks: [usageTask()], stale: false, loading: false, updatedAt: 0, refresh: vi.fn(), onOpenTask: vi.fn() }
}
function canvas(ctx: MaterialUsageContext) {
  return <MaterialResourceCanvas model={ctx.model!} context={ctx} onSelect={vi.fn()} onClear={vi.fn()} onDetails={vi.fn()} />
}
function multiSectionSnapshot() {
  const snapshot = structuredClone(usageSnapshot())
  snapshot.nodes[1].material.name = '01 转运仓'
  snapshot.nodes.push({
    material: { uuid: 'bulk-warehouse', name: '02 扩展仓', type: 'warehouse', parent_uuid: 'robot' },
    sites: Array.from({ length: 24 }, (_, index) => ({
      uuid: `bulk-${index}`, name: `B${index + 1}`, position_x: index % 6, position_y: Math.floor(index / 6),
      occupied_material_uuid: null,
    })),
  })
  return snapshot
}
afterEach(() => localStorage.clear())
describe('画布实时占用交互', () => {
  it('labels the two S1 layers and keeps site selection and dragged coordinates correct after inventory refresh', () => {
    const snapshot = structuredClone(usageSnapshot())
    snapshot.nodes[1].material.name = 'S1 上料过渡仓'
    snapshot.nodes[1].sites = Array.from({ length: 6 }, (_, index) => ({
      uuid: `s1-${index}`, name: `L${Math.floor(index / 3) + 1}C${index % 3 + 1}`,
      position_x: 6, position_y: 58 + index % 3 * 120, position_z: index < 3 ? 70 : 260,
      occupied_material_uuid: index === 0 ? 'sample' : null,
    }))
    snapshot.nodes[2].current_site_uuid = 's1-0'
    const onSelect = vi.fn()
    const props = { onSelect, onClear: vi.fn(), onDetails: vi.fn() }
    const ctx = context(snapshot)
    const { rerender, unmount } = render(<MaterialResourceCanvas {...props} model={ctx.model!} context={ctx} />)
    const group = screen.getByLabelText('设备组 转运机械臂')
    expect(within(group).getByText('L1')).toBeVisible()
    expect(within(group).getByText('L2')).toBeVisible()
    expect(within(group).getByRole('button', { name: '库位 S1 上料过渡仓 / L1C3' })).toHaveStyle({ gridColumn: '1', gridRow: '2' })
    const target = within(group).getByRole('button', { name: '库位 S1 上料过渡仓 / L2C1' })
    expect(target).toHaveStyle({ gridColumn: '2', gridRow: '4' })
    fireEvent.click(target)
    expect(onSelect).toHaveBeenCalledWith('warehouse', 's1-3')
    fireEvent.keyDown(within(group).getByRole('button', { name: '移动设备及库位 转运机械臂' }), { key: 'ArrowRight' })
    const bounds = { width: group.style.width, height: group.style.height, left: group.style.left, top: group.style.top }
    snapshot.nodes[1].sites[0].occupied_material_uuid = null
    snapshot.nodes[1].sites[3].occupied_material_uuid = 'sample'
    snapshot.nodes[2].current_site_uuid = 's1-3'
    const updated = context(snapshot)
    rerender(<MaterialResourceCanvas {...props} model={updated.model!} context={updated} selection={{ materialUuid: 'warehouse', siteUuid: 's1-3', request: 1 }} />)
    expect(target).toHaveClass('selected')
    expect(target).toHaveAccessibleDescription('当前物料：测试烧杯；未绑定 Task')
    expect(group).toHaveStyle(bounds)
    unmount()
    render(canvas(context(snapshot)))
    expect(screen.getByLabelText('设备组 转运机械臂')).toHaveStyle(bounds)
  })

  it('shows all six stirrer sites across heights together and updates inventory in both rows', () => {
    const snapshot = structuredClone(usageSnapshot())
    snapshot.nodes[0].material.name = 'S04 磁搅'
    snapshot.nodes[1].material.name = 'S04磁搅工位仓'
    snapshot.nodes[1].sites = Array.from({ length: 6 }, (_, index) => ({
      uuid: `s04-${index + 1}`, name: `S04${index + 1}`, sort_order: index,
      position_x: 92.1 + (index % 3) * 225, position_y: 89.4, position_z: index < 3 ? 150 : 535,
      occupied_material_uuid: index === 0 ? 'sample' : null,
    }))
    snapshot.nodes[2].current_site_uuid = 's04-1'
    const { rerender } = render(canvas(context(snapshot)))
    const group = screen.getByLabelText('设备组 S04 磁搅')
    const sites = within(group).getAllByRole('button', { name: /^库位 / })
    expect(sites).toHaveLength(6)
    expect(within(group).queryByRole('button', { name: /第 .* 层/ })).not.toBeInTheDocument()
    expect(sites[0]).toHaveStyle({ gridColumn: '1', gridRow: '1' })
    expect(sites[3]).toHaveStyle({ gridColumn: '1', gridRow: '2' })
    expect(sites[5]).toHaveStyle({ gridColumn: '3', gridRow: '2' })
    expect(sites[0]).toHaveAttribute('data-occupied', 'true')
    expect(sites[3]).toHaveAttribute('data-occupied', 'false')
    const bounds = { width: group.style.width, height: group.style.height, left: group.style.left, top: group.style.top }
    snapshot.nodes[1].sites[0].occupied_material_uuid = null
    snapshot.nodes[1].sites[3].occupied_material_uuid = 'sample'
    snapshot.nodes[2].current_site_uuid = 's04-4'
    rerender(canvas(context(snapshot)))
    expect(sites[0]).toHaveAttribute('data-occupied', 'false')
    expect(sites[3]).toHaveAttribute('data-occupied', 'true')
    expect(sites[3]).toHaveTextContent('有料')
    expect(sites[3]).toHaveAccessibleDescription('当前物料：测试烧杯；未绑定 Task')
    expect(group).toHaveStyle(bounds)
  })

  it('fits the visible section without reserving space inside the frame for a hidden larger section', () => {
    const snapshot = multiSectionSnapshot()
    const smallOnly = structuredClone(snapshot)
    smallOnly.nodes = smallOnly.nodes.filter((node) => node.material.uuid !== 'bulk-warehouse')
    const { rerender } = render(canvas(context(smallOnly)))
    const group = screen.getByLabelText('设备组 转运机械臂')
    const smallSize = { width: group.style.width, height: group.style.height }
    rerender(canvas(context(snapshot)))
    const select = within(group).getByRole('combobox', { name: '转运机械臂 库位分组' })
    expect(select).toHaveValue('warehouse')
    expect(group).toHaveStyle(smallSize)
    expect(within(group).getAllByRole('button', { name: /^库位 / })).toHaveLength(2)
    const position = { left: group.style.left, top: group.style.top }
    const neighbor = screen.getByLabelText('设备组 相机')
    const neighborBounds = { width: neighbor.style.width, height: neighbor.style.height, left: neighbor.style.left, top: neighbor.style.top }

    fireEvent.change(select, { target: { value: 'bulk-warehouse' } })
    expect(within(group).getAllByRole('button', { name: /^库位 / })).toHaveLength(24)
    expect(parseFloat(group.style.width)).toBeGreaterThan(parseFloat(smallSize.width))
    expect(parseFloat(group.style.height)).toBeGreaterThan(parseFloat(smallSize.height))
    expect(group).toHaveStyle(position)
    expect(neighbor).toHaveStyle(neighborBounds)

    fireEvent.change(select, { target: { value: 'warehouse' } })
    expect(group).toHaveStyle({ ...smallSize, ...position })
    expect(within(group).getAllByRole('button', { name: /^库位 / })).toHaveLength(2)
  })

  it('centers an explicitly selected larger section using its new bounds', () => {
    const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(new DOMRect(0, 0, 800, 600))
    try {
      const ctx = context(multiSectionSnapshot())
      const props = { model: ctx.model!, context: ctx, onSelect: vi.fn(), onClear: vi.fn(), onDetails: vi.fn() }
      const { rerender } = render(<MaterialResourceCanvas {...props} selection={{ materialUuid: 'warehouse', request: 1 }} />)
      const group = screen.getByLabelText('设备组 转运机械臂')
      const smallWidth = parseFloat(group.style.width)
      rerender(<MaterialResourceCanvas {...props} selection={{ materialUuid: 'bulk-warehouse', siteUuid: 'bulk-23', request: 2 }} />)
      expect(within(group).getByRole('combobox')).toHaveValue('bulk-warehouse')
      expect(parseFloat(group.style.width)).toBeGreaterThan(smallWidth)
      expect(within(group).getByRole('button', { name: '库位 02 扩展仓 / B24' })).toHaveClass('selected')
      const plane = group.parentElement!
      const transform = plane.style.transform.match(/translate\(([-\d.]+)px, ([-\d.]+)px\) scale\(([-\d.]+)\)/)!
      const [, x, y, scale] = transform.map(Number)
      expect(x + (parseFloat(group.style.left) + parseFloat(group.style.width) / 2) * scale).toBeCloseTo(400)
      expect(y + (parseFloat(group.style.top) + parseFloat(group.style.height) / 2) * scale).toBeCloseTo(300)
    } finally {
      rect.mockRestore()
    }
  })

  it('fits a larger section into a short viewport after changing the dropdown without moving the device', () => {
    const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(new DOMRect(0, 0, 420, 280))
    try {
      render(canvas(context(multiSectionSnapshot())))
      const group = screen.getByLabelText('设备组 转运机械臂')
      const position = { left: group.style.left, top: group.style.top }
      fireEvent.change(within(group).getByRole('combobox'), { target: { value: 'bulk-warehouse' } })
      expect(group).toHaveStyle(position)
      expect(within(group).getAllByRole('button', { name: /^库位 / })).toHaveLength(24)
      const transform = group.parentElement!.style.transform.match(/translate\(([-\d.]+)px, ([-\d.]+)px\) scale\(([-\d.]+)\)/)!
      const [, x, y, scale] = transform.map(Number)
      const left = x + parseFloat(group.style.left) * scale
      const top = y + parseFloat(group.style.top) * scale
      const width = parseFloat(group.style.width) * scale
      const height = parseFloat(group.style.height) * scale
      expect(scale).toBeLessThan(1)
      expect(left).toBeGreaterThanOrEqual(0)
      expect(top).toBeGreaterThanOrEqual(0)
      expect(left + width).toBeLessThanOrEqual(420)
      expect(top + height).toBeLessThanOrEqual(280)
      expect(left + width / 2).toBeCloseTo(210)
      expect(top + height / 2).toBeCloseTo(140)
    } finally {
      rect.mockRestore()
    }
  })

  it('opens the current site owner for loading and unloading when selection came from a beaker', () => {
    const ctx = context()
    const onDetails = vi.fn()
    render(<MaterialResourceCanvas model={ctx.model!} context={ctx} selection={{ materialUuid: 'sample', request: 1 }} onSelect={vi.fn()} onClear={vi.fn()} onDetails={onDetails} />)
    fireEvent.click(screen.getByRole('button', { name: '查看详情 / 上下料' }))
    expect(onDetails).toHaveBeenCalledWith('warehouse', 'site-a')
    fireEvent.click(screen.getByRole('button', { name: '查看物料' }))
    expect(onDetails).toHaveBeenLastCalledWith('sample')
  })

  it('lights only actual inventory, then clears source and lights destination after a successful transfer', () => {
    const snapshot = transferSnapshot()
    const { rerender } = render(canvas(context(snapshot)))
    const source = screen.getByRole('button', { name: '库位 转运仓 / A01' })
    const target = screen.getByRole('button', { name: '库位 相机 / 8' })
    expect(source).toHaveAttribute('data-occupied', 'true')
    expect(source).toHaveTextContent('转移中')
    expect(source).toHaveAccessibleDescription('当前物料：测试烧杯；转移中；关联 Task：task-1')
    expect(target).toHaveAttribute('data-occupied', 'false')
    expect(target).toHaveTextContent('待放入')
    expect(target).toHaveAccessibleDescription('当前物料：空库位；待放入')
    expect(screen.getByLabelText('设备组 转运机械臂').querySelector('header')).toHaveAttribute('data-in-use', 'true')
    snapshot.nodes[1].sites[0].occupied_material_uuid = null
    snapshot.nodes[3].sites[0].occupied_material_uuid = 'sample'
    snapshot.nodes[2].current_site_uuid = 'site-8'
    snapshot.jobs = []
    rerender(canvas(context(snapshot)))
    expect(source).toHaveAttribute('data-occupied', 'false')
    expect(source).toHaveTextContent('空')
    expect(source).toHaveAccessibleDescription('当前物料：空库位')
    expect(target).toHaveAttribute('data-occupied', 'true')
    expect(target).toHaveTextContent('有料')
    expect(target).toHaveAccessibleDescription('当前物料：测试烧杯；关联 Task：task-1')
    expect(screen.getAllByRole('button', { name: /^库位 / }).filter((site) => site.dataset.occupied === 'true')).toHaveLength(1)
    expect(screen.getByLabelText('设备组 转运机械臂').querySelector('header')).toHaveAttribute('data-in-use', 'false')
  })

  it('moves the entire group and persists presentation only without a network write', () => {
    const snapshot = multiSectionSnapshot()
    const { rerender, unmount } = render(canvas(context(snapshot)))
    const group = screen.getByLabelText('设备组 转运机械臂')
    const before = parseFloat(group.style.left)
    const top = group.style.top
    const site = within(group).getByRole('button', { name: '库位 01 转运仓 / A01' })
    const fetcher = vi.spyOn(globalThis, 'fetch')
    fireEvent.keyDown(within(group).getByRole('button', { name: '移动设备及库位 转运机械臂' }), { key: 'ArrowRight' })
    expect(parseFloat(group.style.left)).toBe(before + 20)
    expect(group).toContainElement(site)
    fireEvent.change(within(group).getByRole('combobox'), { target: { value: 'bulk-warehouse' } })
    const largeSize = { width: group.style.width, height: group.style.height }
    snapshot.nodes[1].sites[0].occupied_material_uuid = null
    snapshot.nodes[4].sites[0].occupied_material_uuid = 'sample'
    snapshot.nodes[2].current_site_uuid = 'bulk-0'
    snapshot.nodes[2].material.parent_uuid = 'bulk-warehouse'
    rerender(canvas(context(snapshot)))
    expect(within(group).getByRole('combobox')).toHaveValue('bulk-warehouse')
    expect(within(group).getByRole('button', { name: '库位 02 扩展仓 / B1' })).toHaveAccessibleDescription('当前物料：测试烧杯；未绑定 Task')
    expect(group).toHaveStyle({ ...largeSize, top })
    expect(parseFloat(group.style.left)).toBe(before + 20)
    unmount()
    render(canvas(context(snapshot)))
    expect(screen.getByLabelText('设备组 转运机械臂')).toHaveStyle({ left: `${before + 20}px`, top })
    expect(fetcher).not.toHaveBeenCalled()
    fetcher.mockRestore()
  })

  it('shows initial unbound inventory as occupied while the device stays idle', () => {
    const ctx = { ...context(), model: buildResourceUsage(usageSnapshot(), []) }
    render(canvas(ctx))
    expect(screen.getByRole('button', { name: '库位 转运仓 / A01' })).toHaveTextContent('有料')
    expect(screen.getByRole('button', { name: '库位 转运仓 / A01' })).toHaveAccessibleDescription('当前物料：测试烧杯；未绑定 Task')
    expect(screen.getByRole('button', { name: '库位 转运仓 / A01' })).toHaveAttribute('data-occupied', 'true')
    expect(screen.getByLabelText('设备组 转运机械臂').querySelector('header')).toHaveAttribute('data-in-use', 'false')
  })

  it('keeps material task and target-site use separate in hover and follows location changes', () => {
    const snapshot = transferSnapshot()
    const { rerender } = render(<ResourceHoverCard hover={{ siteUuid: 'site-8', left: 0, top: 0 }} context={context(snapshot)} />)
    expect(screen.getByRole('tooltip')).toHaveTextContent('当前物料：空库位')
    expect(screen.getByRole('tooltip')).toHaveTextContent('库位使用 Task')
    expect(screen.getByRole('tooltip')).toHaveTextContent('Task task-1')
    rerender(<ResourceHoverCard hover={{ materialUuid: 'sample', left: 0, top: 0 }} context={context(snapshot)} />)
    expect(screen.getByRole('tooltip')).toHaveTextContent('转运仓 / A01')
    expect(screen.getByRole('tooltip')).toHaveTextContent('Task task-1')
    snapshot.nodes[1].sites[0].occupied_material_uuid = null
    snapshot.nodes[3].sites[0].occupied_material_uuid = 'sample'
    snapshot.nodes[2].current_site_uuid = 'site-8'
    rerender(<ResourceHoverCard hover={{ materialUuid: 'sample', left: 0, top: 0 }} context={context(snapshot)} />)
    expect(screen.getByRole('tooltip')).toHaveTextContent('相机 / 8')
    expect(screen.getByRole('tooltip')).not.toHaveTextContent('转运仓 / A01')
  })
  it('highlights a live move and follows it without inventing history when no task is watched', () => {
    const before = structuredClone(usageSnapshot())
    const first = context(before)
    const props = { onSelect: vi.fn(), onClear: vi.fn(), onDetails: vi.fn() }
    const { rerender } = render(<MaterialResourceCanvas {...props} model={first.model!} context={first} />)
    const panel = screen.getByLabelText('任务物料变化')
    expect(within(panel).getByText('暂无运行中的任务')).toBeVisible()
    const after = structuredClone(usageSnapshot())
    after.nodes[1].sites[0].occupied_material_uuid = null
    after.nodes[1].sites[1].occupied_material_uuid = 'sample'
    after.nodes[2].current_site_uuid = 'site-b'
    const second = context(after)
    rerender(<MaterialResourceCanvas {...props} model={second.model!} context={second} />)
    expect(screen.getByRole('button', { name: '库位 转运仓 / A02' })).toHaveClass('moved-to')
    expect(screen.getByRole('button', { name: '库位 转运仓 / A01' })).toHaveClass('moved-from')
    expect(within(panel).getByRole('checkbox', { name: '跟随变化' })).toBeChecked()
  })
  it('lists the watched task history with the step that caused each change and locates a move on click', async () => {
    const load = vi.spyOn(edgeClient, 'loadTaskMaterialEvents').mockResolvedValue([
      event({ uuid: 'e1', fromSite: '转运仓 / A01', toSite: '转运仓 / A02', fromOwnerUuid: 'warehouse', toOwnerUuid: 'warehouse' }),
      event({ uuid: 'e2', kind: 'consumed', occurredAt: '2026-09-25T04:56:08Z', materialName: '展开溶剂瓶 1', quantity: 60, quantityUnit: 'mL', balanceAfter: 920, stepPath: ['展缸预备', '展开-准备'], nodeName: 'fill' }),
      event({ uuid: 'e3', kind: 'used', occurredAt: '2026-09-25T05:50:45Z', reason: '已装样品粉末', stepPath: ['8 粉末收集', '收集-周期', '收集-下料'], nodeName: 'mark_used' }),
    ])
    const ctx = context(structuredClone(usageSnapshot()))
    const onOpenTask = vi.fn()
    render(<MaterialResourceCanvas model={ctx.model!} context={{ ...ctx, onOpenTask }} onSelect={vi.fn()} onClear={vi.fn()} onDetails={vi.fn()} historyTask={{ uuid: 'task-1', name: '并行全流程 v1', running: true }} />)
    const panel = screen.getByLabelText('任务物料变化')
    const toggle = await within(panel).findByRole('button', { name: '物料变化 3 条' })
    expect(load).toHaveBeenCalledWith('task-1', expect.anything())
    expect(toggle).toHaveTextContent('测试烧杯标为已使用（已装样品粉末）')
    fireEvent.click(toggle)
    const items = within(panel).getAllByRole('listitem')
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining('标为已使用（已装样品粉末）'),
      expect.stringContaining('扣 60 mL，余 920 mL'),
      expect.stringContaining('转运仓 / A01 → 转运仓 / A02'),
    ])
    expect(within(items[0]).getByText('收集-周期 · 收集-下料 · mark_used')).toHaveAttribute('title', '8 粉末收集 / 收集-周期 / 收集-下料 / mark_used')
    expect(within(items[2]).getByText('1 上样上料 · 确认放下并登记位置')).toBeVisible()
    fireEvent.click(within(items[2]).getByRole('button'))
    await waitFor(() => expect(screen.getByRole('button', { name: '库位 转运仓 / A02' })).toHaveClass('moved-to'))
    expect(screen.getByRole('button', { name: '库位 转运仓 / A01' })).toHaveClass('moved-from')
    fireEvent.click(within(panel).getByRole('button', { name: /并行全流程 v1/ }))
    expect(onOpenTask).toHaveBeenCalledWith('task-1')
    load.mockRestore()
  })
  it('counts filled sites per section and shows each section fill in the selector', () => {
    const ctx = context(multiSectionSnapshot())
    render(canvas(ctx))
    const group = screen.getByLabelText('设备组 转运机械臂')
    const selector = within(group).getByRole('combobox', { name: '转运机械臂 库位分组' })
    expect(within(selector).getAllByRole('option').map((option) => option.textContent)).toEqual(['01 转运仓 · 1/2', '02 扩展仓 · 0/24'])
    expect(within(group).getByText('1/2 有料')).toBeVisible()
    fireEvent.change(selector, { target: { value: 'bulk-warehouse' } })
    expect(within(group).getByText('0/24 有料')).toBeVisible()
  })
})
