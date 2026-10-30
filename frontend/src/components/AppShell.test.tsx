import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AppShell } from './AppShell'

describe('AppShell', () => {
  it('does not render a global runtime mode switch', () => {
    render(
      <AppShell
        page="tasks"
        connection="connected"
        activeTaskCount={0}
        onNavigate={vi.fn()}
        onNotify={vi.fn()}
      >
        <div>content</div>
      </AppShell>,
    )

    expect(screen.queryByRole('button', { name: /模式/ })).not.toBeInTheDocument()
  })

  it('在侧边栏提供 Swagger 和接口 JSON 两个已有文档入口', () => {
    render(
      <AppShell
        page="overview"
        connection="connected"
        activeTaskCount={0}
        onNavigate={vi.fn()}
        onNotify={vi.fn()}
      >
        <div>页面内容</div>
      </AppShell>,
    )

    expect(screen.getByRole('link', { name: '打开 Swagger 接口文档' })).toHaveAttribute('href', '/api/docs')
    expect(screen.getByRole('link', { name: '打开接口 JSON 文档' })).toHaveAttribute('href', '/api/openapi.json')
  })

  it('在物料之后提供独立设备入口', () => {
    const onNavigate = vi.fn()
    render(
      <AppShell
        page="devices"
        connection="connected"
        activeTaskCount={0}
        onNavigate={onNavigate}
        onNotify={vi.fn()}
      >
        <div>设备页面</div>
      </AppShell>,
    )

    const navigation = screen.getByRole('navigation', { name: '主导航' })
    const device = screen.getByRole('button', { name: '设备' })
    expect(device).toHaveAttribute('aria-current', 'page')
    expect(Array.from(navigation.querySelectorAll('button')).map((item) => item.textContent)).toEqual(expect.arrayContaining(['物料', '设备']))
    fireEvent.click(device)
    expect(onNavigate).toHaveBeenCalledWith('devices')
  })
})
