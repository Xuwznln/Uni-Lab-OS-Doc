import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { EdgeSnapshot } from '../types'
import { loadEdgeSnapshot, loadEdgeTasks } from '../lib/edgeClient'
import { useEdgeData } from './useEdgeData'

vi.mock('../lib/edgeClient', async (importOriginal) => ({
  ...await importOriginal<typeof import('../lib/edgeClient')>(),
  loadEdgeSnapshot: vi.fn(),
  loadEdgeTasks: vi.fn(),
}))

const authoritativeSnapshot: EdgeSnapshot = {
  capabilities: {
    canEditDefinitions: true,
    canViewDrafts: true,
    canRunUnpublished: true,
    canDebug: true,
    canExecuteDeviceActions: true,
  },
  workflows: [],
  tasks: [],
  materials: [{
    uuid: 'material-authoritative',
    name: '权威物料',
    category: 'beaker',
    currentLocation: {
      kind: 'site',
      label: 'S6 / S061',
      siteUuid: 'site-s061',
      ownerMaterialUuid: 'station-s6',
    },
    configuredSource: 'S6 / S061',
    taskReferences: [],
    barcode: 'BKR-1',
    className: 'community.szlab.beaker',
    updatedAt: '12:00',
    isStructural: false,
    siteCount: 0,
    sites: [],
    revision: 1,
    position: [0, 0, 0],
    size: [80, 80, 100],
  }],
  materialTotal: 1,
  workflowLoaded: 1,
  workflowTotal: 1,
}

class FakeEventSource {
  static latest: FakeEventSource | undefined
  listeners = new Map<string, EventListener>()
  closed = false
  constructor(public url: string) { FakeEventSource.latest = this }
  addEventListener(type: string, listener: EventListener) { this.listeners.set(type, listener) }
  close() { this.closed = true }
  emit(type: string) { this.listeners.get(type)?.(new Event(type)) }
}

afterEach(() => vi.unstubAllGlobals())

describe('useEdgeData', () => {
  beforeEach(() => {
    FakeEventSource.latest = undefined
    vi.mocked(loadEdgeSnapshot).mockReset()
    vi.mocked(loadEdgeTasks).mockReset()
    vi.mocked(loadEdgeTasks).mockResolvedValue([])
  })

  it.each(['open', 'stream.reset'])('rehydrates active state on %s, including a detail request already in flight', async (event) => {
    vi.stubGlobal('EventSource', FakeEventSource)
    vi.mocked(loadEdgeSnapshot).mockResolvedValue(authoritativeSnapshot)
    let resolveOldDetail: ((value: string) => void) | undefined
    const loadDetail = vi.fn()
      .mockImplementationOnce(() => new Promise<string>((resolve) => { resolveOldDetail = resolve }))
      .mockResolvedValue('succeeded')
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => ({
      edge: useEdgeData(),
      detail: useQuery({ queryKey: ['workflow-task-detail', 'task-1'], queryFn: loadDetail }),
    }), { wrapper })
    await waitFor(() => expect(FakeEventSource.latest).toBeDefined())
    act(() => FakeEventSource.latest?.emit(event))
    await act(async () => { resolveOldDetail?.('running') })
    await waitFor(() => expect(result.current.detail.data).toBe('succeeded'))
    expect(loadEdgeSnapshot).toHaveBeenCalledTimes(2)
    expect(loadEdgeTasks).not.toHaveBeenCalled()
  })

  it('refreshes an active task detail when a runtime notification arrives', async () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    vi.mocked(loadEdgeSnapshot).mockResolvedValue(authoritativeSnapshot)
    const loadDetail = vi.fn().mockResolvedValueOnce('running').mockResolvedValue('succeeded')
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => ({
      edge: useEdgeData(),
      detail: useQuery({ queryKey: ['workflow-task-detail', 'task-1'], queryFn: loadDetail, staleTime: Infinity }),
    }), { wrapper })
    await waitFor(() => expect(result.current.detail.data).toBe('running'))
    await waitFor(() => expect(FakeEventSource.latest).toBeDefined())
    act(() => {
      for (let i = 0; i < 100; i++) FakeEventSource.latest?.emit('workflow.runtime.changed')
    })
    await waitFor(() => expect(result.current.detail.data).toBe('succeeded'))
    expect(loadDetail).toHaveBeenCalledTimes(2)
    expect(loadEdgeTasks).toHaveBeenCalledTimes(1)
    expect(loadEdgeSnapshot).toHaveBeenCalledTimes(1)
  })

  it('keeps a reset arriving while an earlier full snapshot is in flight', async () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    let resolveOldSnapshot: ((value: EdgeSnapshot) => void) | undefined
    vi.mocked(loadEdgeSnapshot)
      .mockResolvedValueOnce(authoritativeSnapshot)
      .mockImplementationOnce(() => new Promise<EdgeSnapshot>((resolve) => { resolveOldSnapshot = resolve }))
      .mockResolvedValue({ ...authoritativeSnapshot, workflowLoaded: 3 })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useEdgeData(), { wrapper })
    await waitFor(() => expect(FakeEventSource.latest).toBeDefined())
    act(() => FakeEventSource.latest?.emit('open'))
    await waitFor(() => expect(loadEdgeSnapshot).toHaveBeenCalledTimes(2))
    act(() => FakeEventSource.latest?.emit('stream.reset'))
    await act(async () => { resolveOldSnapshot?.({ ...authoritativeSnapshot, workflowLoaded: 2 }) })
    await waitFor(() => expect(result.current.snapshot.workflowLoaded).toBe(3))
    expect(loadEdgeSnapshot).toHaveBeenCalledTimes(3)
  })

  it('keeps the last authoritative projection read-only when a later Edge refresh fails', async () => {
    vi.mocked(loadEdgeSnapshot)
      .mockResolvedValueOnce(authoritativeSnapshot)
      .mockRejectedValueOnce(new Error('Edge unavailable'))
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useEdgeData(), { wrapper })

    await waitFor(() => expect(result.current.connection).toBe('connected'))
    expect(result.current.snapshot.materials).toHaveLength(1)

    await act(async () => { await result.current.refetch() })

    await waitFor(() => expect(result.current.connection).toBe('reconnecting'))
    expect(result.current.snapshot).toMatchObject({
      workflows: [],
      tasks: [],
      materials: [expect.objectContaining({ uuid: 'material-authoritative' })],
      materialTotal: 1,
    })
    expect(result.current.lastSuccessfulAt).toBeGreaterThan(0)
  })

  it('refreshes task runtime data without reloading workflows and materials', async () => {
    vi.mocked(loadEdgeSnapshot).mockResolvedValue(authoritativeSnapshot)
    vi.mocked(loadEdgeTasks)
      .mockResolvedValueOnce([{
        uuid: 'task-new', workflowUuid: 'wf-1', workflowName: '流程', status: 'running',
        priority: 'normal',
        sample: 'sample-1', description: 'task', current: '运行中', progress: 0,
        updatedAt: '12:00', nodes: [], materialUuids: [], runMode: 'normal', executionMode: 'normal', dispatchGate: 'active', controlStatus: 'active', matrixGroupKey: 'wf-1',
      }])
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useEdgeData(), { wrapper })

    await waitFor(() => expect(result.current.connection).toBe('connected'))
    expect(loadEdgeTasks).not.toHaveBeenCalled()
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['edge-tasks'] })
    })

    await waitFor(() => expect(result.current.snapshot.tasks[0]?.uuid).toBe('task-new'))
    expect(loadEdgeSnapshot).toHaveBeenCalledTimes(1)
    expect(loadEdgeTasks).toHaveBeenCalledTimes(1)
  })

  it('keeps the last task matrix when a runtime-only refresh fails', async () => {
    const previousTask = {
      uuid: 'task-previous', workflowUuid: 'wf-1', workflowName: '流程', status: 'running' as const,
      priority: 'normal' as const,
      sample: 'sample-1', description: 'task', current: '运行中', progress: 20,
        updatedAt: '12:00', nodes: [], materialUuids: [], runMode: 'normal', executionMode: 'normal' as const, dispatchGate: 'active' as const,
      controlStatus: 'active', matrixGroupKey: 'wf-1',
    }
    vi.mocked(loadEdgeSnapshot).mockResolvedValue({
      ...authoritativeSnapshot,
      tasks: [previousTask],
    })
    vi.mocked(loadEdgeTasks).mockRejectedValue(new Error('Edge busy'))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useEdgeData(), { wrapper })

    await waitFor(() => expect(result.current.connection).toBe('connected'))
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['edge-tasks'] })
    })

    await waitFor(() => expect(result.current.connection).toBe('reconnecting'))
    expect(result.current.snapshot.tasks).toEqual([previousTask])
  })

  it('invalidates the shared task query when the scheduler SSE announces manual confirmation', async () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    vi.mocked(loadEdgeSnapshot).mockResolvedValue(authoritativeSnapshot)
    vi.mocked(loadEdgeTasks).mockResolvedValue([])
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { result, unmount } = renderHook(() => useEdgeData(), { wrapper })

    await waitFor(() => expect(result.current.connection).toBe('connected'))
    await waitFor(() => expect(FakeEventSource.latest?.url).toBe('/api/v1/events'))
    const callsBeforeEvent = vi.mocked(loadEdgeTasks).mock.calls.length
    act(() => FakeEventSource.latest?.emit('manual_confirmation.required'))
    await waitFor(() => expect(loadEdgeTasks).toHaveBeenCalledTimes(callsBeforeEvent + 1))

    const source = FakeEventSource.latest
    unmount()
    expect(source?.closed).toBe(true)
  })

  it('coalesces a burst of scheduler SSE events into one trailing task refresh', async () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    vi.mocked(loadEdgeSnapshot).mockResolvedValue(authoritativeSnapshot)
    vi.mocked(loadEdgeTasks).mockResolvedValue([])
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useEdgeData(), { wrapper })

    await waitFor(() => expect(result.current.connection).toBe('connected'))
    await waitFor(() => expect(FakeEventSource.latest?.url).toBe('/api/v1/events'))
    const callsBeforeEvents = vi.mocked(loadEdgeTasks).mock.calls.length

    await act(async () => {
      FakeEventSource.latest?.emit('workflow.runtime.changed')
      await new Promise((resolve) => window.setTimeout(resolve, 50))
      FakeEventSource.latest?.emit('workflow.runtime.changed')
      await new Promise((resolve) => window.setTimeout(resolve, 50))
      FakeEventSource.latest?.emit('manual_confirmation.required')
    })

    await waitFor(
      () => expect(loadEdgeTasks).toHaveBeenCalledTimes(callsBeforeEvents + 1),
      { timeout: 1_000 },
    )
  })

  it('keeps a queued SSE refresh while a full snapshot object is replaced', async () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    vi.mocked(loadEdgeSnapshot)
      .mockResolvedValueOnce(authoritativeSnapshot)
      .mockResolvedValueOnce({ ...authoritativeSnapshot, workflowLoaded: 2 })
    vi.mocked(loadEdgeTasks).mockResolvedValue([])
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useEdgeData(), { wrapper })

    await waitFor(() => expect(result.current.connection).toBe('connected'))
    await waitFor(() => expect(FakeEventSource.latest?.url).toBe('/api/v1/events'))
    const source = FakeEventSource.latest
    act(() => source?.emit('workflow.runtime.changed'))
    await act(async () => { await result.current.refetch() })

    await waitFor(() => expect(loadEdgeTasks).toHaveBeenCalledTimes(1), { timeout: 1_000 })
    expect(FakeEventSource.latest).toBe(source)
  })

  it('runs at most one task refresh and preserves one trailing refresh while it is busy', async () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    vi.mocked(loadEdgeSnapshot).mockResolvedValue(authoritativeSnapshot)
    let releaseFirstRefresh: (() => void) | undefined
    vi.mocked(loadEdgeTasks)
      .mockImplementationOnce(() => new Promise<Awaited<ReturnType<typeof loadEdgeTasks>>>((resolve) => {
        releaseFirstRefresh = () => resolve([])
      }))
      .mockResolvedValue([])
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useEdgeData(), { wrapper })

    await waitFor(() => expect(result.current.connection).toBe('connected'))
    act(() => FakeEventSource.latest?.emit('workflow.runtime.changed'))
    await waitFor(() => expect(loadEdgeTasks).toHaveBeenCalledTimes(1))

    act(() => {
      FakeEventSource.latest?.emit('workflow.runtime.changed')
      FakeEventSource.latest?.emit('manual_confirmation.required')
    })
    await new Promise((resolve) => window.setTimeout(resolve, 350))
    expect(loadEdgeTasks).toHaveBeenCalledTimes(1)

    await act(async () => { releaseFirstRefresh?.() })
    await waitFor(
      () => expect(loadEdgeTasks).toHaveBeenCalledTimes(2),
      { timeout: 1_000 },
    )
  })
})
