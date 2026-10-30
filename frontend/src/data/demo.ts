import type { MaterialRecord, TaskNode, WorkflowDefinition, WorkflowTask } from '../types'

export const demoWorkflows: WorkflowDefinition[] = [
  {
    uuid: '6d9fb3e2-4dcb-5f23-93b4-74d1b6083393',
    name: 'SZLab 单样品全流程（物料感知）',
    revision: 12,
    status: 'published',
    workflowType: 'normal',
    description: '烧杯、粉体、溶剂、移液、搅拌、检测与成品回库的完整闭环。',
    nodeCount: 10,
    tags: ['生产发布'],
    sourcePath: 'workflows/single_sample_atomic_material_target.py',
    inputContract: [
      { name: 'sample_id', type: 'string', required: true, schema: { type: 'string' } },
      { name: 'target_powder_mass_g', type: 'number', required: true, defaultValue: 1, schema: { type: 'number', minimum: 0.001, maximum: 100 } },
      { name: 'volume_pump_1', type: 'integer', defaultValue: 10, schema: { type: 'integer', minimum: 0 } },
      { name: 'volume_pump_2', type: 'integer', defaultValue: 10, schema: { type: 'integer', minimum: 0 } },
      { name: 'pipette_volume_raw', type: 'integer', defaultValue: 5000, schema: { type: 'integer', minimum: 1 } },
    ],
    outputContract: [
      { name: 'product_vial', type: 'ResourceSlot', schema: { $slot: 'ResourceSlot' } },
      { name: 'used_beaker', type: 'ResourceSlot', schema: { $slot: 'ResourceSlot' } },
      { name: 'inspection_result', type: 'string', schema: { type: 'string' } },
    ],
  },
  {
    uuid: 'e7c53119-9fde-5250-9bf5-264f23d157a8',
    name: 'SZLab 标准物料转运',
    revision: 7,
    status: 'published',
    workflowType: 'normal',
    description: '机器人取料、放料与 Host 物料归属提交。',
    nodeCount: 6,
    tags: ['复合工作流'],
    inputContract: [
      { name: 'resource', type: 'ResourceSlot', required: true, schema: { $slot: 'ResourceSlot' } },
      { name: 'source_warehouse', type: 'ResourceSlot', required: true, schema: { $slot: 'ResourceSlot' } },
      { name: 'target_warehouse', type: 'ResourceSlot', required: true, schema: { $slot: 'ResourceSlot' } },
    ],
    outputContract: [{ name: 'material', type: 'ResourceSlot', schema: { $slot: 'ResourceSlot' } }],
  },
  {
    uuid: '5e7ce142-bf5a-5d30-8666-fdf5374941f1',
    name: 'S07 物料投粉',
    revision: 5,
    status: 'source',
    workflowType: 'normal',
    description: '粗粉、精粉和烧杯三物料汇合投粉。',
    nodeCount: 5,
    tags: ['工站流程'],
    inputContract: [{ name: 'target_mass_g', type: 'number', schema: { type: 'number' } }],
    outputContract: [{ name: 'dosed_beaker', type: 'ResourceSlot', schema: { $slot: 'ResourceSlot' } }],
  },
]

const stageNames = [
  '运行输入',
  '物料准入',
  '粉桶扫码',
  'S07 投粉',
  '转运至 S09',
  'S09 移液',
  'S04 搅拌',
  'S05 检测',
  '倒料与关盖',
  '结果汇总',
]

function nodes(activeIndex: number, taskStatus: WorkflowTask['status']): TaskNode[] {
  return stageNames.map((name, index) => {
    let status: TaskNode['status'] = 'pending'
    if (taskStatus === 'succeeded' || index < activeIndex) status = 'succeeded'
    if (index === activeIndex && taskStatus === 'running') status = 'running'
    if (index === activeIndex && taskStatus === 'admission_blocked') status = 'waiting'
    if (index === activeIndex && taskStatus === 'failed') status = 'failed'
    const waitReason = index === activeIndex && taskStatus === 'admission_blocked'
      ? {
          code: 'material_site_unavailable',
          title: '等待库位',
          message: '样品瓶目标库位当前不可用',
          details: ['目标库位：S08 样品瓶位'],
        }
      : undefined
    return {
      uuid: `demo-node-${index}`,
      name,
      kind: index === 0 || index === 9 ? 'boundary' : 'device_action',
      index,
      status,
      device: index > 1 && index < 9 ? name.split(' ')[0] : undefined,
      waitReason,
    }
  })
}

function demoTask(
  uuid: string,
  status: WorkflowTask['status'],
  current: string,
  progress: number,
  sample: string,
  updatedAt: string,
  activeIndex: number,
): WorkflowTask {
  return {
    uuid,
    status,
    priority: 'normal',
    current,
    progress,
    sample,
    updatedAt,
    workflowUuid: demoWorkflows[0].uuid,
    workflowName: demoWorkflows[0].name,
    workflowRevision: demoWorkflows[0].revision,
    description: 'SZLab 单样品全流程运行任务',
    nodes: nodes(activeIndex, status),
    materialUuids: [],
    runMode: 'normal',
    executionMode: 'normal',
    dispatchGate: 'active',
    controlStatus: 'active',
    matrixGroupKey: 'single-sample-atomic-v3',
  }
}

export const demoTasks: WorkflowTask[] = [
  demoTask('TK-240831-020', 'running', 'S05 拍照检测', 78, 'sample-0243', '18:49:08', 7),
  demoTask('TK-240831-019', 'running', 'S04 磁力搅拌', 69, 'sample-0242', '18:46:31', 6),
  demoTask('TK-240831-018', 'running', '烧杯转运至 S09', 62, 'sample-0241', '18:42:16', 4),
  demoTask('TK-240831-017', 'admission_blocked', '等待 S08 样品瓶位', 12, 'sample-0240', '18:36:02', 1),
  demoTask('TK-240831-016', 'succeeded', '已生成实验结果', 100, 'sample-0239', '18:19:44', 9),
  demoTask('TK-240831-015', 'failed', 'S07 投粉结果异常', 31, 'sample-0238', '17:58:11', 3),
]

export const demoMaterials: MaterialRecord[] = [
  ['MAT-BKR-031', '500 mL 烧杯', 'beaker', 'S06 / S061', 'BKR-2408'],
  ['MAT-VIAL-088', '250 mL 样品瓶', 'sample_vial', 'S08 / VIAL1', 'VIAL-2408'],
  ['MAT-PWD-012', '粗粉瓶', 'powder', 'S07 / POWDER1', 'PWD-A19'],
  ['MAT-PWD-019', '精粉瓶', 'powder', 'S07 / POWDER2', 'PWD-B07'],
  ['MAT-LIQ-044', '液体试剂瓶', 'liquid_reagent', 'S06 / PUMP1', 'LIQ-0831'],
  ['MAT-TIP-006', 'TIP Box', 'consumable', 'S09 / TIP1', 'TIP-2408'],
].map(([uuid, name, category, location, barcode], index) => ({
  uuid,
  name,
  category,
  currentLocation: {
    kind: 'site' as const,
    label: location,
    siteUuid: `demo-site-${index}`,
    ownerMaterialUuid: `demo-owner-${index}`,
  },
  configuredSource: location,
  taskReferences: index < 3 ? [{
    taskUuid: demoTasks[index].uuid,
    taskStatus: demoTasks[index].status,
    workflowName: demoTasks[index].workflowName,
    sample: demoTasks[index].sample,
  }] : [],
  barcode,
  className: `community.szlab.${category}`,
  sourceGraph: 'szlab-local-debug.json',
  updatedAt: '18:47',
  isStructural: false,
  siteCount: 0,
  sites: [],
  revision: 1,
  position: [index * 120, 0, 0],
  size: [80, 80, 100],
}))

export const stationOverview = [
  { code: 'S07', name: '固体加料', x: 18, y: 26 },
  { code: 'S08', name: '开关盖', x: 51, y: 16 },
  { code: 'S09', name: '移液工作站', x: 76, y: 38 },
  { code: 'S04', name: '磁力搅拌', x: 30, y: 72 },
  { code: 'S05', name: '拍照检测', x: 73, y: 76 },
  { code: 'ROBOT', name: '机械臂', x: 49, y: 48 },
]
