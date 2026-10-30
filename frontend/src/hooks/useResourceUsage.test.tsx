import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useResourceUsage } from './useResourceUsage'
import { usageSnapshot } from '../test/resourceUsageFixtures'

afterEach(() => vi.unstubAllGlobals())
describe('占用快照刷新', () => {
  it('retains a complete snapshot after a failed refresh and recovers on an event', async () => {
    let failed = false
    const fetcher = vi.fn(async (url: string) => ({ ok: !failed, status: failed ? 503 : 200, json: async () => ({ code: 0, data: url.endsWith('/materials/graph') ? { nodes: usageSnapshot().nodes } : { occupancies: [], jobs: [], material_bindings: [] } }) }))
    vi.stubGlobal('fetch', fetcher)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
    const { result, unmount } = renderHook(() => useResourceUsage(true), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    const initial = result.current.data
    failed = true
    await act(async () => { await result.current.refetch({ throwOnError: false }) })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.data).toEqual(initial)
    failed = false
    act(() => window.dispatchEvent(new Event('unilab:resource-usage-changed')))
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(fetcher).toHaveBeenCalledTimes(6)
    unmount(); client.clear()
  })
})
