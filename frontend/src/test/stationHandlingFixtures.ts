import type { ManualDeviceAction, StationSnapshot } from '../lib/stationErrorClient'
import type { MaterialRecord, WorkflowTask } from '../types'

export function handlingSnapshot(): StationSnapshot {
  return {
    station_id: 'local', mode: 'PAUSED', station_version: 7, error_epoch: 3, snapshot_id: 'local:7:3',
    active_session_id: null, active_session: null, manual_actions: [], control_commands: [],
    errors: [{ decision_id: 'decision-1', decision_version: 2, source_task_uuid: 'task-1', source_job_uuid: 'job-1',
      stage: 'DECISION_REQUIRED', status: 'open', meta_data: { message: 'SZLAB_TEST_NODE_FAILURE', source_node_id: 'node-1' } }],
  }
}

export const handlingDevice: MaterialRecord = {
  uuid: 'device-2', name: '软件探针', category: 'device', sourceNodeId: 'probe', resourceTemplateUuid: 'probe-template',
  currentLocation: { kind: 'unassigned', label: '未分配' }, configuredSource: '', taskReferences: [], barcode: '', className: 'probe', updatedAt: '',
  isStructural: false, siteCount: 0, sites: [], revision: 1, position: [0, 0, 0], size: [0, 0, 0],
}

export const handlingTask: WorkflowTask = {
  uuid: 'task-1', workflowUuid: 'workflow-1', workflowName: '错误测试', status: 'intervention_required',
  priority: 'normal', sample: '', description: '', current: '失败节点', progress: 33, updatedAt: '', materialUuids: [],
  runMode: 'normal', executionMode: 'normal', dispatchGate: 'active', controlStatus: 'error_waiting', matrixGroupKey: 'test',
  nodes: [{ uuid: 'node-1', name: '注入节点错误', kind: 'action', index: 1, status: 'failed' }],
  trace: { traceId: 'trace-1', mode: 'trace', url: 'http://localhost:30081/trace/trace-1' },
}

export const handlingAction: ManualDeviceAction = {
  action_id: 'action-1', action_version: 'sha256:action-v1', name: 'probe_once', display_name: '探针动作',
  device_binding: { material_uuid: 'device-2', edge_local_id: 'probe' },
  parameter_schema: { type: 'object', properties: {
    duration: { type: 'number', title: '持续秒数', minimum: 0, maximum: 30 },
    enabled: { type: 'boolean', title: '启用' },
  }, required: ['duration', 'enabled'] }, defaults: { duration: 2, enabled: true },
}

export function manualSnapshot(): StationSnapshot {
  const snapshot = handlingSnapshot()
  snapshot.active_session_id = 'session-1'
  snapshot.active_session = { session_id: 'session-1', decision_id: 'decision-1', stage: 'MANUAL_HANDLING', version: 1, summary: '' }
  snapshot.errors[0] = { ...snapshot.errors[0], stage: 'MANUAL_HANDLING', session_id: 'session-1', decision_version: 3 }
  return snapshot
}
