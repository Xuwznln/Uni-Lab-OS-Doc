import { useState } from 'react'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SearchableContainerSelect } from './SearchableContainerSelect'

const containers = Array.from({ length: 95 }, (_, index) => ({
  value: `container-${index + 1}`,
  label: `试剂瓶 ${String(index + 1).padStart(2, '0')} · 完整容器名称`,
  description: `Rack-A / R${index + 1}C1`,
}))

function Controlled({ onChange = vi.fn() }: { onChange?: (value: string) => void }) {
  const [value, setValue] = useState('')
  return <SearchableContainerSelect label="试剂容器" value={value} options={containers} placeholder="选择空容器" onChange={(next) => { setValue(next); onChange(next) }} />
}

describe('SearchableContainerSelect', () => {
  it('全部 95 个容器都可访问，搜索末项后选择并完整显示名称', () => {
    const onChange = vi.fn()
    render(<Controlled onChange={onChange} />)
    const trigger = screen.getByRole('combobox', { name: '试剂容器' })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(trigger).toHaveTextContent('选择空容器')
    fireEvent.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    const list = screen.getByRole('listbox', { name: '试剂容器选项' })
    expect(within(list).getAllByRole('option')).toHaveLength(95)
    expect(within(list).getByRole('option', { name: /试剂瓶 95/ })).toBeInTheDocument()
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索试剂容器' }), { target: { value: '95' } })
    expect(within(list).getAllByRole('option')).toHaveLength(1)
    fireEvent.click(within(list).getByRole('option', { name: /试剂瓶 95/ }))
    expect(onChange).toHaveBeenCalledExactlyOnceWith('container-95')
    expect(trigger).toHaveTextContent(containers[94].label)
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    fireEvent.click(trigger)
    expect(screen.getByRole('searchbox')).toHaveValue('')
    expect(screen.getAllByRole('option')).toHaveLength(95)
    expect(screen.getByRole('option', { name: /试剂瓶 95/ })).toHaveAttribute('aria-selected', 'true')
  })

  it('搜索名称及描述时忽略大小写，并在关闭后重置搜索', () => {
    render(<Controlled />)
    const trigger = screen.getByRole('combobox', { name: '试剂容器' })
    fireEvent.click(trigger)
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'rack-a / r42c1' } })
    expect(screen.getAllByRole('option')).toHaveLength(1)
    expect(screen.getByRole('option', { name: /试剂瓶 42/ })).toBeInTheDocument()
    fireEvent.keyDown(screen.getByRole('searchbox'), { key: 'Escape' })
    fireEvent.click(trigger)
    expect(screen.getByRole('searchbox')).toHaveValue('')
    expect(screen.getAllByRole('option')).toHaveLength(95)
  })

  it('方向键打开并导航选项，Enter 选择当前匹配容器', () => {
    const onChange = vi.fn()
    render(<Controlled onChange={onChange} />)
    const trigger = screen.getByRole('combobox', { name: '试剂容器' })
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    const search = screen.getByRole('searchbox')
    fireEvent.change(search, { target: { value: '试剂瓶 0' } })
    expect(screen.getAllByRole('option')).toHaveLength(9)
    fireEvent.keyDown(search, { key: 'ArrowDown' })
    fireEvent.keyDown(search, { key: 'ArrowDown' })
    fireEvent.keyDown(search, { key: 'ArrowUp' })
    fireEvent.keyDown(search, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledExactlyOnceWith('container-2')
    expect(trigger).toHaveTextContent(containers[1].label)
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('Escape 只关闭选择弹层，不触发父级弹窗关闭', () => {
    const parentEscape = vi.fn()
    render(<div onKeyDown={(event) => { if (event.key === 'Escape') parentEscape() }}><Controlled /></div>)
    const trigger = screen.getByRole('combobox', { name: '试剂容器' })
    fireEvent.click(trigger)
    fireEvent.keyDown(screen.getByRole('searchbox'), { key: 'Escape' })
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(parentEscape).not.toHaveBeenCalled()
    expect(trigger).toHaveFocus()
  })

  it('无匹配和无可选容器分别提示，搜索结果为空时不会选择', () => {
    const onChange = vi.fn()
    const { rerender } = render(<SearchableContainerSelect label="目标容器 1" value="" options={containers} onChange={onChange} />)
    fireEvent.click(screen.getByRole('combobox', { name: '目标容器 1' }))
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索目标容器 1' }), { target: { value: '不存在的容器' } })
    expect(screen.getByText('未找到匹配容器')).toBeInTheDocument()
    expect(screen.queryByRole('option')).not.toBeInTheDocument()
    fireEvent.keyDown(screen.getByRole('searchbox'), { key: 'Enter' })
    expect(onChange).not.toHaveBeenCalled()
    fireEvent.keyDown(screen.getByRole('searchbox'), { key: 'Escape' })
    rerender(<SearchableContainerSelect label="目标容器 1" value="" options={[]} onChange={onChange} />)
    fireEvent.click(screen.getByRole('combobox', { name: '目标容器 1' }))
    expect(screen.getByText('暂无可选容器')).toBeInTheDocument()
    expect(screen.queryByRole('option')).not.toBeInTheDocument()
  })

  it('禁用状态下点击和键盘均不能打开', () => {
    const onChange = vi.fn()
    render(<SearchableContainerSelect label="试剂容器" value="container-1" options={containers} disabled onChange={onChange} />)
    const trigger = screen.getByRole('combobox', { name: '试剂容器' })
    expect(trigger).toBeDisabled()
    expect(trigger).toHaveTextContent(containers[0].label)
    fireEvent.click(trigger)
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
  })

  it('点击组件外部关闭弹层，组件内部搜索不关闭', () => {
    render(<Controlled />)
    fireEvent.click(screen.getByRole('combobox', { name: '试剂容器' }))
    fireEvent.pointerDown(screen.getByRole('searchbox'))
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    fireEvent.pointerDown(document.body)
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('表单内打开和选择容器不会提交父表单', () => {
    const onSubmit = vi.fn((event: React.FormEvent) => event.preventDefault())
    render(<form onSubmit={onSubmit}><Controlled /></form>)
    const trigger = screen.getByRole('combobox', { name: '试剂容器' })
    expect(trigger).toHaveAttribute('type', 'button')
    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('option', { name: /试剂瓶 01/ }))
    expect(onSubmit).not.toHaveBeenCalled()
  })
})
