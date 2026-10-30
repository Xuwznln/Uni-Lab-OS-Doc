import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { StationErrorHandling } from './components/StationErrorHandling'
import { useEdgeData } from './hooks/useEdgeData'
import { useStationErrorHandling } from './hooks/useStationErrorHandling'
import { pageFromSearch, searchForWorkflow, searchWithPage, workflowTargetFromSearch } from './lib/routes'
import { LiveMaterialsPage } from './pages/LiveMaterialsPage'
import { OverviewPage } from './pages/OverviewPage'
import { TasksPage } from './pages/TasksPage'
import { WorkflowsPage } from './pages/WorkflowsPage'
import { OperationsPage } from './pages/OperationsPage'
import { ReagentsPage } from './pages/ReagentsPage'
import { DevicesPage } from './pages/DevicesPage'
import type { PageId, WorkflowTarget } from './types'

export default function App() {
  const [searchParams, setSearchParams] = useSearchParams()
  const page = pageFromSearch(`?${searchParams.toString()}`)
  const workflowTarget = workflowTargetFromSearch(`?${searchParams.toString()}`)
  const { snapshot, connection, error, refetch, lastSuccessfulAt } = useEdgeData()
  const [toast, setToast] = useState('')
  const errorHandling = useStationErrorHandling(connection === 'connected', setToast)
  const [handlingTarget, setHandlingTarget] = useState<{ taskUuid: string; sequence: number }>()

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(''), 3200)
    return () => window.clearTimeout(timer)
  }, [toast])

  const navigate = useCallback((nextPage: PageId) => {
    setSearchParams((current) => new URLSearchParams(searchWithPage(`?${current.toString()}`, nextPage)))
  }, [setSearchParams])

  const openWorkflow = useCallback((target: WorkflowTarget) => {
    setSearchParams((current) => new URLSearchParams(searchForWorkflow(
      `?${current.toString()}`,
      target,
    )))
  }, [setSearchParams])

  const openTask = useCallback((taskUuid: string) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(searchWithPage(`?${current.toString()}`, 'tasks'))
      next.set('task', taskUuid)
      return next
    })
  }, [setSearchParams])

  const openResources = useCallback((taskUuid: string) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(searchWithPage(`?${current.toString()}`, 'materials'))
      next.set('usageTask', taskUuid)
      return next
    })
  }, [setSearchParams])

  const selectDevice = useCallback((materialUuid: string) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(searchWithPage(`?${current.toString()}`, 'devices'))
      if (materialUuid) next.set('device', materialUuid)
      else next.delete('device')
      return next
    })
  }, [setSearchParams])

  const refresh = useCallback(() => {
    void refetch().then((result) => {
      setToast(result.isError ? '刷新失败，已保留最后一次成功的只读快照' : 'Edge 状态已刷新')
    })
  }, [refetch])

  return (
    <AppShell
      page={page}
      connection={connection}
      activeTaskCount={snapshot.tasks.filter((task) => task.status === 'running' || task.status === 'canceling').length}
      onNavigate={navigate}
      onNotify={setToast}
    >
      {connection === 'error' ? (
        <div className="connection-alert" role="alert">
          <div><strong>Edge 数据暂不可用</strong><span>{error instanceof Error ? error.message : '请检查 Edge 服务与网络连接。'}</span></div>
          <button type="button" onClick={refresh}>重新连接</button>
        </div>
      ) : null}
      {connection === 'reconnecting' ? (
        <div className="connection-alert connection-alert-reconnecting" role="status">
          <div>
            <strong>Edge 连接短暂中断，当前为只读快照</strong>
            <span>
              {lastSuccessfulAt
                ? `显示 ${new Date(lastSuccessfulAt).toLocaleTimeString('zh-CN', { hour12: false })} 的最后一次成功数据；写操作已暂停。`
                : '正在恢复连接；写操作已暂停。'}
              {error instanceof Error ? ` ${error.message}` : ''}
            </span>
          </div>
          <button type="button" onClick={refresh}>立即重试</button>
        </div>
      ) : null}
      {connection === 'demo' ? (
        <div className="connection-alert connection-alert-demo" role="status">
          <div><strong>当前为显式演示模式</strong><span>以下内容不是 Edge 生产数据。</span></div>
        </div>
      ) : null}
      {connection !== 'demo' ? <StationErrorHandling controller={errorHandling} materials={snapshot.materials} tasks={snapshot.tasks} openRequest={handlingTarget} /> : null}
      {page === 'overview' ? (
        <OverviewPage tasks={snapshot.tasks} materialTotal={snapshot.materialTotal} workflowLoaded={snapshot.workflowLoaded} connection={connection} onNavigate={navigate} onNotify={setToast} />
      ) : page === 'materials' ? (
        <LiveMaterialsPage key={searchParams.get('usageTask') || 'all'} materials={snapshot.materials} tasks={snapshot.tasks} connected={connection === 'connected'} onNotify={setToast} onRefresh={refresh} onOpenTask={openTask} targetTaskUuid={searchParams.get('usageTask') || undefined} />
      ) : page === 'devices' ? (
        <DevicesPage
          materials={snapshot.materials}
          connected={connection === 'connected'}
          capabilities={snapshot.capabilities}
          targetDeviceUuid={searchParams.get('device') || undefined}
          onSelectDevice={selectDevice}
          onOpenTask={openTask}
          onRefresh={refresh}
          onNotify={setToast}
        />
      ) : page === 'operations' ? (
        <OperationsPage materials={snapshot.materials} connected={connection === 'connected'} onNotify={setToast} />
      ) : page === 'reagents' ? (
        <ReagentsPage materials={snapshot.materials} connected={connection === 'connected'} onNotify={setToast} onRefresh={refresh} />
      ) : page === 'workflows' ? (
        <WorkflowsPage
          workflows={snapshot.workflows}
          materials={snapshot.materials}
          connected={connection === 'connected'}
          onNavigate={navigate}
          onNotify={setToast}
          onSelectWorkflow={openWorkflow}
          targetWorkflow={workflowTarget}
          catalogStatus={connection === 'loading' ? 'loading' : connection === 'error' ? 'error' : 'ready'}
          capabilities={snapshot.capabilities}
        />
      ) : (
        <TasksPage
          tasks={snapshot.tasks}
          workflows={snapshot.workflows}
          materials={snapshot.materials}
          connected={connection === 'connected'}
          onRefresh={refresh}
          onNotify={setToast}
          onOpenWorkflow={openWorkflow}
          capabilities={snapshot.capabilities}
          errorTaskIds={errorHandling.snapshot?.errors.map((item) => item.source_task_uuid)}
          onHandleError={(taskUuid) => setHandlingTarget({ taskUuid, sequence: Date.now() })}
          targetTaskUuid={searchParams.get('task') || undefined}
          onViewResources={openResources}
        />
      )}
      <div className={`toast ${toast ? 'show' : ''}`} role="status" aria-live="polite">{toast}</div>
    </AppShell>
  )
}
