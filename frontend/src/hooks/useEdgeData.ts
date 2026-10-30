import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { QueryFilters } from '@tanstack/react-query'
import { demoMaterials, demoTasks, demoWorkflows } from '../data/demo'
import { EDGE_API_BASE, loadEdgeSnapshot, loadEdgeTasks, materialsWithTaskReferences } from '../lib/edgeClient'
import { DEFAULT_RUNTIME_CAPABILITIES } from '../types'
import type { ConnectionMode, EdgeSnapshot } from '../types'
import { STATION_QUERY_KEY } from '../lib/stationErrorClient'

const demoSnapshot: EdgeSnapshot = {
  capabilities: DEFAULT_RUNTIME_CAPABILITIES,
  workflows: demoWorkflows,
  tasks: demoTasks,
  materials: demoMaterials,
  materialTotal: 48,
  workflowLoaded: demoWorkflows.length,
  workflowTotal: demoWorkflows.length,
}

const emptySnapshot: EdgeSnapshot = {
  capabilities: { canEditDefinitions: false, canViewDrafts: false, canRunUnpublished: false, canDebug: false, canExecuteDeviceActions: false },
  workflows: [],
  tasks: [],
  materials: [],
  materialTotal: 0,
  workflowLoaded: 0,
  workflowTotal: 0,
}

const demoEnabled = import.meta.env.VITE_ENABLE_DEMO_DATA === 'true'
const taskEventRefreshDelayMs = 300

export function useEdgeData() {
  const queryClient = useQueryClient()
  const refreshState = useRef({
    timer: undefined as number | undefined,
    taskInFlight: false,
    taskPending: false,
    fullInFlight: false,
    fullPending: false,
    stationPending: false,
    mounted: true,
  })
  const scheduleTaskRefreshRef = useRef<() => void>(() => undefined)
  const scheduleFullRefreshRef = useRef<() => void>(() => undefined)
  const query = useQuery({
    queryKey: ['edge-snapshot'],
    queryFn: async ({ signal }) => {
      const snapshot = await loadEdgeSnapshot(signal)
      queryClient.setQueryData(['edge-tasks'], snapshot.tasks)
      return snapshot
    },
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  })
  const hasSnapshot = Boolean(query.data)
  const taskQuery = useQuery({
    queryKey: ['edge-tasks'],
    queryFn: ({ signal }) => loadEdgeTasks(
      query.data?.workflows || [],
      query.data?.materials || [],
      signal,
    ),
    enabled: hasSnapshot,
    staleTime: 15_000,
  })

  useEffect(() => {
    refreshState.current.mounted = true
    return () => { refreshState.current.mounted = false }
  }, [])

  useEffect(() => {
    if (!hasSnapshot) return undefined
    const refreshAfterCurrent = async (predicate: QueryFilters['predicate']) => {
      // 先等通知之前已经发出的请求结束，再重新读取，避免旧响应吞掉最后一次变更。
      await queryClient.refetchQueries(
        { predicate, type: 'active', fetchStatus: 'fetching' },
        { cancelRefetch: false },
      )
      if (!refreshState.current.mounted) return
      await queryClient.invalidateQueries({ predicate }, { cancelRefetch: false })
    }
    const runFullRefresh = async () => {
      const state = refreshState.current
      state.fullInFlight = true
      state.fullPending = false
      try {
        // 其他组件可能已发起任务刷新，先等它结束，避免旧任务响应覆盖新快照。
        await queryClient.refetchQueries(
          { queryKey: ['edge-tasks'], exact: true, type: 'active', fetchStatus: 'fetching' },
          { cancelRefetch: false },
        )
        if (queryClient.isFetching({ queryKey: ['edge-snapshot'], exact: true })) {
          await queryClient.refetchQueries(
            { queryKey: ['edge-snapshot'], exact: true }, { cancelRefetch: false },
          )
        }
        if (!state.mounted) return
        await queryClient.refetchQueries(
          { queryKey: ['edge-snapshot'], exact: true, type: 'active' },
          { cancelRefetch: false },
        )
        // 完整快照已更新任务列表；补刷当前详情、工站异常等其他活动查询。
        await refreshAfterCurrent((query) => !['edge-snapshot', 'edge-tasks'].includes(String(query.queryKey[0])))
      } finally {
        state.fullInFlight = false
        armRefresh()
      }
    }
    const runTaskRefresh = async () => {
      const state = refreshState.current
      if (state.fullInFlight || state.taskInFlight) {
        state.taskPending = true
        return
      }
      const joinedExistingRefresh = queryClient.isFetching({ queryKey: ['edge-tasks'], exact: true }) > 0
      state.taskInFlight = true
      state.taskPending = joinedExistingRefresh
      const refreshStation = state.stationPending
      state.stationPending = false
      try {
        await Promise.all([
          queryClient.refetchQueries(
            { queryKey: ['edge-tasks'], exact: true, type: 'active' },
            { cancelRefetch: false },
          ),
          refreshAfterCurrent((query) => String(query.queryKey[0]).startsWith('workflow-task-')
            || (refreshStation && query.queryKey[0] === STATION_QUERY_KEY[0])),
        ])
      } finally {
        state.taskInFlight = false
        armRefresh()
      }
    }
    const armRefresh = () => {
      const state = refreshState.current
      if (!state.mounted || (!state.fullPending && !state.taskPending)) return
      if (state.timer !== undefined || state.taskInFlight || state.fullInFlight) return
      state.timer = window.setTimeout(() => {
        state.timer = undefined
        if (state.taskInFlight || state.fullInFlight) return
        state.taskPending = false
        if (state.fullPending) void runFullRefresh()
        else void runTaskRefresh()
      }, taskEventRefreshDelayMs)
    }
    const scheduleTaskRefresh = () => {
      window.dispatchEvent(new Event('unilab:resource-usage-changed'))
      refreshState.current.taskPending = true
      armRefresh()
    }
    const scheduleFullRefresh = () => {
      refreshState.current.fullPending = true
      armRefresh()
    }
    scheduleTaskRefreshRef.current = scheduleTaskRefresh
    scheduleFullRefreshRef.current = scheduleFullRefresh
    const pollingTimer = window.setInterval(scheduleTaskRefresh, 30_000)
    const events = typeof EventSource === 'undefined'
      ? undefined
      : new EventSource(`${EDGE_API_BASE}/events`)
    events?.addEventListener('open', scheduleFullRefresh)
    events?.addEventListener('stream.reset', scheduleFullRefresh)
    events?.addEventListener('workflow.authoring.changed', scheduleFullRefresh)
    events?.addEventListener('manual_confirmation.required', scheduleTaskRefresh)
    events?.addEventListener('manual_confirmation.resolved', scheduleTaskRefresh)
    events?.addEventListener('workflow.runtime.changed', scheduleTaskRefresh)
    events?.addEventListener('station.error_handling.changed', () => {
      refreshState.current.stationPending = true
      scheduleTaskRefresh()
    })
    return () => {
      events?.close()
      window.clearInterval(pollingTimer)
      const state = refreshState.current
      if (state.timer !== undefined) window.clearTimeout(state.timer)
      state.timer = undefined
      scheduleTaskRefreshRef.current = () => undefined
      scheduleFullRefreshRef.current = () => undefined
    }
  }, [hasSnapshot, queryClient])

  let connection: ConnectionMode = 'loading'
  if (query.isSuccess) connection = 'connected'
  if (query.isError || taskQuery.isError) {
    connection = demoEnabled
      ? 'demo'
      : query.data
        ? 'reconnecting'
        : 'error'
  }

  const baseSnapshot = query.data
    ?? (demoEnabled ? demoSnapshot : emptySnapshot)
  const effectiveTasks = taskQuery.data ?? baseSnapshot.tasks
  const snapshot = connection === 'error'
    ? emptySnapshot
    : {
        ...baseSnapshot,
        tasks: effectiveTasks,
        materials: materialsWithTaskReferences(baseSnapshot.materials, effectiveTasks),
      }

  const refetch = async () => {
    // 完整快照本身已经携带任务矩阵并原子更新 edge-tasks，手动刷新不能再并发
    // 发起第二份相同矩阵请求。
    const state = refreshState.current
    state.fullInFlight = true
    try {
      if (queryClient.isFetching({ queryKey: ['edge-tasks'], exact: true })) {
        await queryClient.refetchQueries(
          { queryKey: ['edge-tasks'], exact: true, type: 'active' },
          { cancelRefetch: false },
        )
      }
      const snapshotResult = await query.refetch({ cancelRefetch: false })
      return { isError: snapshotResult.isError }
    } finally {
      state.fullInFlight = false
      if (state.fullPending) scheduleFullRefreshRef.current()
      else if (state.taskPending) scheduleTaskRefreshRef.current()
    }
  }

  return {
    ...query,
    error: query.error || taskQuery.error,
    isError: query.isError || taskQuery.isError,
    refetch,
    connection,
    snapshot,
    lastSuccessfulAt: Math.max(query.dataUpdatedAt, taskQuery.dataUpdatedAt),
    isDemoFallback: demoEnabled && (query.isError || taskQuery.isError || !query.data),
  }
}
