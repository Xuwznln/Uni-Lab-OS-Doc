import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  HANDLING_INTENT_STORAGE, HandlingApiError, STATION_QUERY_KEY, handlingErrorMessage,
  loadStationHandling, submitHandlingIntent,
} from '../lib/stationErrorClient'
import type { HandlingIntent } from '../lib/stationErrorClient'

function restoreIntent(): HandlingIntent | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(HANDLING_INTENT_STORAGE) || 'null')
    return value && typeof value.path === 'string' && value.path.startsWith('/') && !value.path.includes('..')
      && typeof value.key === 'string' && typeof value.label === 'string'
      && value.body && typeof value.body === 'object' ? value : null
  } catch { return null }
}

export function useStationErrorHandling(connected: boolean, onNotify: (message: string) => void) {
  const queryClient = useQueryClient()
  const [unconfirmed, setUnconfirmed] = useState<HandlingIntent | null>(restoreIntent)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const inFlight = useRef(false)
  const query = useQuery({
    queryKey: STATION_QUERY_KEY,
    queryFn: ({ signal }) => loadStationHandling(signal),
    enabled: connected,
    retry: false,
    refetchInterval: (state) => state.state.data?.mode === 'PAUSED' ? 1500 : 5000,
    refetchOnWindowFocus: true,
    staleTime: 0,
  })

  const refresh = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: STATION_QUERY_KEY }, { cancelRefetch: false })
  }, [queryClient])

  useEffect(() => {
    if (!connected) return
    // 复用全局事件连接；轮询兜底覆盖刷新和连接恢复。
    const update = () => { void refresh() }
    window.addEventListener('unilab:station-error-changed', update)
    return () => window.removeEventListener('unilab:station-error-changed', update)
  }, [connected, refresh])

  const execute = useCallback(async (intent: HandlingIntent) => {
    if (inFlight.current || !connected || query.isError) return false
    inFlight.current = true
    setPending(true)
    setError('')
    setUnconfirmed(intent)
    try {
      // 保存后才发请求，网络中断或刷新页面后仍使用同一个幂等键和参数。
      sessionStorage.setItem(HANDLING_INTENT_STORAGE, JSON.stringify(intent))
    } catch {
      setError('浏览器无法保存本次操作，请启用会话存储后再提交。')
      setUnconfirmed(null)
      setPending(false)
      inFlight.current = false
      return false
    }
    try {
      const result = await submitHandlingIntent(intent)
      setUnconfirmed(null)
      sessionStorage.removeItem(HANDLING_INTENT_STORAGE)
      if (result.status === 'FAILED') setError(result.result.error || '操作已被拒绝，请检查当前状态。')
      else if (result.application_error || result.dispatch_error || result.status === 'PENDING') {
        setError(`操作已受理，仍待应用。${result.application_error || result.dispatch_error || ''}`)
      }
      onNotify(result.status === 'FAILED' ? '操作未应用' : `${intent.label}已受理`)
      // 不用较早返回的命令快照覆盖轮询取得的新事实。
      await Promise.all([
        refresh(),
        queryClient.invalidateQueries({ queryKey: ['edge-tasks'] }, { cancelRefetch: false }),
        queryClient.invalidateQueries({ queryKey: ['handling-inventory'] }),
        queryClient.invalidateQueries({ queryKey: ['handling-occupancies'] }),
      ])
      return result.status === 'APPLIED' && !result.application_error && !result.dispatch_error
    } catch (cause) {
      if (cause instanceof HandlingApiError && cause.definitive) {
        setUnconfirmed(null)
        sessionStorage.removeItem(HANDLING_INTENT_STORAGE)
        setError(handlingErrorMessage(cause))
        void queryClient.invalidateQueries({ queryKey: ['handling-actions'] })
        await refresh()
      } else {
        setError('提交结果尚未确认。请使用“确认原请求结果”，按相同身份查询或重发，避免重复操作。')
      }
      return false
    } finally {
      inFlight.current = false
      setPending(false)
    }
  }, [connected, onNotify, query.isError, queryClient, refresh])

  const submit = useCallback((path: string, body: Record<string, unknown>, label: string) => {
    if (unconfirmed) return Promise.resolve(false)
    return execute({ path, body, label, key: crypto.randomUUID() })
  }, [execute, unconfirmed])

  return {
    snapshot: query.data, loading: query.isPending, readError: query.error,
    writable: connected && !query.isError && Boolean(query.data) && !pending && !unconfirmed,
    canConfirmRequest: connected && !query.isError && !pending,
    pending, error, unconfirmed, submit, refresh,
    retryUnconfirmed: () => unconfirmed ? execute(unconfirmed) : Promise.resolve(false),
  }
}
