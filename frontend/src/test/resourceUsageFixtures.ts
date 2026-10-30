import type { ResourceUsageSnapshot, UsageGraphNode } from '../lib/resourceUsage'
import type { ResourceOccupancy } from '../lib/stationErrorClient'
import type { WorkflowTask } from '../types'

export function usageTask(overrides: Partial<WorkflowTask> = {}): WorkflowTask {
  return { uuid: 'task-1', workflowUuid: 'wf-1', workflowName: '物料转运', status: 'running', priority: 'normal', sample: '样品', description: '', current: '转运', progress: 50, updatedAt: '12:00', nodes: [{ uuid: 'node-1', name: '转运到目标库位', kind: 'device_action', index: 0, status: 'running', device: 'robot-local', job: { uuid: 'job-1', param: {}, feedbackData: {}, returnInfo: {}, errorInfo: [] } }], materialUuids: [], runMode: 'normal', executionMode: 'normal', dispatchGate: 'active', controlStatus: 'active', matrixGroupKey: 'wf-1', trace: { url: 'http://127.0.0.1:30081/trace/trace-1', mode: 'trace' }, ...overrides }
}
export function usageLock(lock_key: string, overrides: Partial<ResourceOccupancy> = {}): ResourceOccupancy {
  return { uuid: lock_key, lock_key, state: 'running', occupancy_kind: 'execution_lock', version: 'v1', workflow_task_uuid: 'task-1', workflow_node_job_uuid: 'job-1', create_time: '2026-09-10T04:00:00Z', ...overrides }
}
export const usageNodes: UsageGraphNode[] = [
  { material: { uuid: 'robot', name: '转运机械臂', type: 'device', meta_data: { source_node_id: 'robot-local' } }, sites: [] },
  { material: { uuid: 'warehouse', name: '转运仓', type: 'warehouse', parent_uuid: 'robot' }, sites: [{ uuid: 'site-a', name: 'A01', occupied_material_uuid: 'sample' }, { uuid: 'site-b', name: 'A02', occupied_material_uuid: null }] },
  { material: { uuid: 'sample', name: '测试烧杯', type: 'container', parent_uuid: 'warehouse' }, current_site_uuid: 'site-a', sites: [] },
  { material: { uuid: 'camera', name: '相机', type: 'device' }, sites: [] },
]
export function usageSnapshot(occupancies: ResourceOccupancy[] = []): ResourceUsageSnapshot { return { nodes: usageNodes, occupancies } }

export function transferSnapshot() {
  const snapshot = structuredClone(usageSnapshot())
  snapshot.nodes[3].sites = [{ uuid: 'site-8', name: '8', position_x: 90, occupied_material_uuid: null }]
  snapshot.material_bindings = [{ uuid: 'binding', material_uuid: 'sample', workflow_task_uuid: 'task-1', workflow_node_job_uuid: 'job-1', status: 'active' }]
  snapshot.jobs = [{ uuid: 'job-1', task_uuid: 'task-1', task_name: '烧杯跨设备转运', task_status: 'running', node_uuid: 'node-1', node_name: '从 1 到 8', status: 'running', device_id: 'robot-local', expected_change_set: { kind: 'material_transfer', material_uuid: 'sample', source_site_uuid: 'site-a', target_site_uuid: 'site-8' } }]
  return snapshot
}
