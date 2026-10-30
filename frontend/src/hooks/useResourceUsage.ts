import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { EDGE_API_BASE } from '../lib/edgeClient'
import { loadResourceUsage } from '../lib/resourceUsage'

export const RESOURCE_USAGE_QUERY_KEY = ['resource-usage', EDGE_API_BASE] as const

export function useResourceUsage(enabled: boolean) {
  const query = useQuery({
    queryKey: RESOURCE_USAGE_QUERY_KEY,
    queryFn: ({ signal }) => loadResourceUsage(signal),
    enabled, retry: false, refetchInterval: 5_000, staleTime: 0,
  })
  const { refetch } = query
  useEffect(() => {
    if (!enabled) return
    let timer: number | undefined
    let pending = false
    let running = false
    let disposed = false
    const schedule = () => {
      pending = true
      if (timer !== undefined || running || disposed) return
      timer = window.setTimeout(async () => {
        timer = undefined
        pending = false
        running = true
        try { await refetch({ cancelRefetch: false }) }
        finally { running = false; if (pending && !disposed) schedule() }
      }, 250)
    }
    window.addEventListener('unilab:resource-usage-changed', schedule)
    return () => {
      disposed = true
      window.removeEventListener('unilab:resource-usage-changed', schedule)
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [enabled, refetch])
  return query
}
