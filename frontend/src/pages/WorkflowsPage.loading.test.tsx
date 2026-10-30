import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { WorkflowsPage } from './WorkflowsPage'

afterEach(cleanup)
for (const status of ['loading', 'error'] as const) {
  it(`目录${status}时不把未知数据展示成没有工作流`, () => {
    render(<QueryClientProvider client={new QueryClient()}>
      <WorkflowsPage workflows={[]} materials={[]} connected={false}
        catalogStatus={status} onNavigate={vi.fn()} onNotify={vi.fn()} />
    </QueryClientProvider>)
    expect(screen.queryByText('没有工作流定义')).not.toBeInTheDocument()
    expect(screen.queryByText('没有匹配的工作流')).not.toBeInTheDocument()
    expect(screen.getByText(status === 'loading' ? '正在加载工作流' : '工作流目录加载失败')).toBeInTheDocument()
  })
}
