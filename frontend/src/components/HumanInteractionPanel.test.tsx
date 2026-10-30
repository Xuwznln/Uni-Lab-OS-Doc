import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { HumanInteractionPanel } from './HumanInteractionPanel'

afterEach(() => vi.unstubAllGlobals())

it('requires an explicit choice and posts only typed reply fields to the current request', async () => {
  const request = { uuid: 'request-one', job_uuid: 'job-one', task_uuid: 'task-one', kind: 'review', status: 'pending',
    deadline_at: new Date(Date.now() + 60000).toISOString(), arguments: {
      prompt: 'Review current candidate', options: ['dispatch', 'reanalyze'], labels: ['下发当前路径', '重新识别并预览'], forms: ['none', 'numbers'],
      text_default: 'B1', number_labels: ['阈值'], number_defaults: [1.5], image: '',
    } }
  const replies: unknown[] = []
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      expect(url).toContain('/workflow-human-interactions/request-one/reply')
      replies.push(JSON.parse(String(init.body)))
      return { ok: true, status: 200, json: async () => ({ code: 0, data: { ...request, status: 'answered' } }) }
    }
    return { ok: true, status: 200, json: async () => ({ code: 0, data: replies.length ? [] : [request] }) }
  })
  vi.stubGlobal('fetch', fetch)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={queryClient}><HumanInteractionPanel taskUuid="task-one" connected onNotify={() => {}} /></QueryClientProvider>)
  expect(await screen.findByRole('button', { name: '提交本次回复' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '重新识别并预览' }))
  fireEvent.change(screen.getByRole('spinbutton', { name: '阈值' }), { target: { value: '2.75' } })
  fireEvent.change(screen.getByLabelText('人工文本输入'), { target: { value: 'B2' } })
  fireEvent.click(screen.getByRole('button', { name: '提交本次回复' }))
  await waitFor(() => expect(replies).toEqual([{ choice: 'reanalyze', text: 'B2', values: [2.75], x: [], y: [], corner_x: [], corner_y: [] }]))
})
