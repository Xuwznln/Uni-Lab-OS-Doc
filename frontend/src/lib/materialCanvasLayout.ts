import type { ResourceUsageModel, UsageDevice, UsageGraphNode, UsageSite } from './resourceUsage'

export interface CanvasPoint { x: number; y: number }
export interface SiteSection {
  uuid: string; name: string; columns: number; rows: number
  sites: Array<{ site: UsageSite; column: number; row: number }>
  layerLabels?: Array<{ name: string; column: number }>
  stackedRows?: number
}
export interface CanvasBlock extends CanvasPoint { uuid: string; width: number; height: number; sections: SiteSection[] }

// 库位使用固定尺寸，避免少列设备把单个库位拉伸到整张设备卡片的宽度。
export const CANVAS_SITE_SIZE = { width: 48, height: 48, gap: 4 } as const
export const CANVAS_FRAME_SIZE = { minWidth: 180, border: 1, headerHeight: 55, paddingX: 8, paddingY: 7, sectionHeight: 24, sectionGap: 6, layerLabelHeight: 18 } as const

/** 外框贴合当前分组；少库位设备保留标题和分组选择器的最小宽度；没有库位的设备只保留标题栏。 */
export function canvasBlockSize(section?: Pick<SiteSection, 'columns' | 'rows' | 'layerLabels'>) {
  if (!section) return { width: CANVAS_FRAME_SIZE.minWidth, height: CANVAS_FRAME_SIZE.headerHeight + 2 * CANVAS_FRAME_SIZE.border }
  const columns = Math.max(1, section?.columns || 1), rows = Math.max(1, section?.rows || 1)
  const gridWidth = columns * CANVAS_SITE_SIZE.width + (columns - 1) * CANVAS_SITE_SIZE.gap
  const gridHeight = rows * CANVAS_SITE_SIZE.height + (rows - 1) * CANVAS_SITE_SIZE.gap
    + (section?.layerLabels?.length ? CANVAS_FRAME_SIZE.layerLabelHeight + CANVAS_SITE_SIZE.gap : 0)
  return {
    width: Math.max(CANVAS_FRAME_SIZE.minWidth, gridWidth + 2 * (CANVAS_FRAME_SIZE.paddingX + CANVAS_FRAME_SIZE.border)),
    height: gridHeight + CANVAS_FRAME_SIZE.headerHeight + CANVAS_FRAME_SIZE.sectionHeight + CANVAS_FRAME_SIZE.sectionGap + 2 * (CANVAS_FRAME_SIZE.paddingY + CANVAS_FRAME_SIZE.border),
  }
}

const number = (value: number | undefined, fallback = 0) => Number.isFinite(value) ? value! : fallback
const coordinate = (value?: number) => Math.round(number(value) * 1000) / 1000

/** 层内保持实际行列；单列的多层阵列利用外框留白并排展示，其余阵列纵向接续。 */
export function siteSections(device: UsageDevice): SiteSection[] {
  const owners = new Map<string, UsageSite[]>()
  for (const site of device.sites) owners.set(site.ownerUuid, [...(owners.get(site.ownerUuid) || []), site])
  return [...owners].map(([uuid, sites]) => {
    const heights = [...new Set(sites.map((site) => coordinate(site.pose?.position_z)))].sort((a, b) => a - b)
    const layers = heights.map((z) => {
      const layer = sites.filter((site) => coordinate(site.pose?.position_z) === z)
      const xs = [...new Set(layer.map((site) => coordinate(site.pose?.position_x)))].sort((a, b) => a - b)
      const ys = [...new Set(layer.map((site) => coordinate(site.pose?.position_y)))].sort((a, b) => b - a)
      const unique = new Set(layer.map((site) => `${coordinate(site.pose?.position_x)}:${coordinate(site.pose?.position_y)}`))
      const grid = unique.size === layer.length && xs.length <= 6 && ys.length <= 6 && xs.length * ys.length <= layer.length * 1.5
      const count = grid ? xs.length : Math.min(4, layer.length)
      const placements = [...layer].sort((a, b) => (a.sortOrder || 0) - (b.sortOrder || 0) || a.name.localeCompare(b.name, 'zh-CN', { numeric: true })).map((site, i) => ({ site,
        column: grid ? xs.indexOf(coordinate(site.pose?.position_x)) : i % count,
        row: grid ? ys.indexOf(coordinate(site.pose?.position_y)) : Math.floor(i / count),
      }))
      return { z, columns: count, rows: grid ? ys.length : Math.ceil(layer.length / count), sites: placements }
    })
    const stackedRows = layers.reduce((sum, layer) => sum + layer.rows, 0)
    const parallelWidth = layers.length * CANVAS_SITE_SIZE.width + (layers.length - 1) * CANVAS_SITE_SIZE.gap
      + 2 * (CANVAS_FRAME_SIZE.paddingX + CANVAS_FRAME_SIZE.border)
    const parallel = layers.length > 1 && layers.every((layer) => layer.columns === 1) && parallelWidth <= CANVAS_FRAME_SIZE.minWidth
    let rowOffset = 0
    const placements = layers.flatMap((layer, column) => {
      const result = layer.sites.map((entry) => ({ ...entry, column: parallel ? column : entry.column, row: entry.row + (parallel ? 0 : rowOffset) }))
      rowOffset += layer.rows
      return result
    })
    // 只有整层名称共享同一个明确前缀时才使用 L1/L2，否则直接显示实际高度。
    const names = layers.map((layer) => {
      const prefixes = layer.sites.map(({ site }) => site.name.match(/^(L\d+)(?=[A-Za-z]|$)/i)?.[1].toUpperCase())
      return prefixes[0] && prefixes.every((prefix) => prefix === prefixes[0]) ? prefixes[0] : undefined
    })
    const namedLayers = names.every(Boolean) && new Set(names).size === layers.length
    return { uuid, name: sites[0].ownerName,
      columns: parallel ? layers.length : Math.max(...layers.map((layer) => layer.columns)),
      rows: parallel ? Math.max(...layers.map((layer) => layer.rows)) : stackedRows,
      sites: placements,
      ...(parallel ? { stackedRows, layerLabels: layers.map((layer, column) => ({ column, name: namedLayers ? names[column]! : `高度 ${layer.z}` })) } : {}),
    }
  })
}

/** 组合父级平移、旋转及缩放，取得用于二维排布的参考位置。 */
function worldPosition(node: UsageGraphNode, nodes: Map<string, UsageGraphNode>): CanvasPoint {
  let point = { x: number(node.relative_position?.position_x), y: number(node.relative_position?.position_y) }
  let parent = nodes.get(node.material.parent_uuid || '')
  const visited = new Set([node.material.uuid])
  while (parent && !visited.has(parent.material.uuid)) {
    visited.add(parent.material.uuid)
    const pose = parent.relative_position
    const angle = number(pose?.rotation_z) * Math.PI / 180
    const x = point.x * number(pose?.scale_x, 1), y = point.y * number(pose?.scale_y, 1)
    point = { x: number(pose?.position_x) + x * Math.cos(angle) - y * Math.sin(angle), y: number(pose?.position_y) + x * Math.sin(angle) + y * Math.cos(angle) }
    parent = nodes.get(parent.material.parent_uuid || '')
  }
  return point
}

/** 固定资源拓扑决定初始布局，任务和物料占用不参与排序；扩展间距防止设备块重叠。 */
export function layoutMaterialCanvas(model: ResourceUsageModel): CanvasBlock[] {
  const nodes = new Map(model.nodes.map((node) => [node.material.uuid, node]))
  const depth = (uuid: string) => {
    const visited = new Set<string>()
    let node = nodes.get(uuid)
    while (node?.material.parent_uuid && !visited.has(node.material.uuid)) { visited.add(node.material.uuid); node = nodes.get(node.material.parent_uuid) }
    return visited.size
  }
  const items = model.devices.map((device) => {
    const sections = siteSections(device).sort((a, b) => depth(a.uuid) - depth(b.uuid) || a.name.localeCompare(b.name, 'zh-CN', { numeric: true }))
    const columns = Math.max(2, ...sections.map((section) => section.columns))
    const rows = Math.max(1, ...sections.map((section) => section.stackedRows ?? section.rows))
    const node = nodes.get(device.uuid)
    // 旧占地只用于初始避让，保持未拖动设备的位置，兼容浏览器已保存的局部排布。
    const sizes = sections.length ? sections.map((section) => canvasBlockSize(section)) : [canvasBlockSize()]
    return { uuid: device.uuid, name: device.label.text, sections, ...canvasBlockSize(sections[0]), footprint: { width: Math.max(260, columns * 76 + 20), height: 104 + rows * 58 },
      // 网格排布按最大分组占位，切换到大分组时不会压住相邻设备。
      largest: { width: Math.max(...sizes.map((size) => size.width)), height: Math.max(...sizes.map((size) => size.height)) },
      point: node ? worldPosition(node, nodes) : { x: 0, y: 0 } }
  }).sort((a, b) => b.point.y - a.point.y || a.point.x - b.point.x || a.uuid.localeCompare(b.uuid))
  // 部署没有给任何设备提供平面坐标时，坐标排布会把全部设备压成一列；此时改用网格铺开。
  if (items.length > 1 && new Set(items.map((item) => `${item.point.x}:${item.point.y}`)).size === 1) return gridLayout(items)
  const minX = Math.min(0, ...items.map((item) => item.point.x))
  const maxY = Math.max(0, ...items.map((item) => item.point.y))
  const blocks: CanvasBlock[] = []
  const footprints: Array<CanvasPoint & { width: number; height: number }> = []
  for (const item of items) {
    const x = 40 + (item.point.x - minX) * .65
    let y = 40 + (maxY - item.point.y) * .65
    // 只在初始排布时避让；状态刷新不会推动其它设备，拖动坐标由展示配置保留。
    let collision: typeof footprints[number] | undefined
    do {
      collision = footprints.find((block) => x < block.x + block.width + 40 && x + item.footprint.width + 40 > block.x && y < block.y + block.height + 40 && y + item.footprint.height + 40 > block.y)
      if (collision) y = collision.y + collision.height + 40
    } while (collision)
    footprints.push({ ...item.footprint, x, y })
    blocks.push({ uuid: item.uuid, sections: item.sections, width: item.width, height: item.height, x, y })
  }
  return blocks
}

type GridItem = { uuid: string; name: string; sections: SiteSection[]; width: number; height: number; largest: { width: number; height: number } }
const GRID_GAP = 40

/** 按行铺开：有库位的设备在前（按名称），无库位设备另起一行收在末尾；行宽取接近 16:10 的画幅。 */
function gridLayout(items: GridItem[]): CanvasBlock[] {
  const byName = (a: GridItem, b: GridItem) => a.name.localeCompare(b.name, 'zh-CN', { numeric: true }) || a.uuid.localeCompare(b.uuid)
  const groups = [items.filter((item) => item.sections.length).sort(byName), items.filter((item) => !item.sections.length).sort(byName)]
  const area = items.reduce((sum, item) => sum + (item.largest.width + GRID_GAP) * (item.largest.height + GRID_GAP), 0)
  const rowWidth = Math.max(...items.map((item) => item.largest.width), Math.sqrt(area * 1.6))
  const blocks: CanvasBlock[] = []
  let y = 40
  for (const group of groups) {
    let x = 40, rowHeight = 0
    for (const item of group) {
      if (x > 40 && x + item.largest.width > 40 + rowWidth) { x = 40; y += rowHeight + GRID_GAP; rowHeight = 0 }
      blocks.push({ uuid: item.uuid, sections: item.sections, width: item.width, height: item.height, x, y })
      x += item.largest.width + GRID_GAP
      rowHeight = Math.max(rowHeight, item.largest.height)
    }
    if (group.length) y += rowHeight + GRID_GAP
  }
  return blocks
}

/** 只把直接设备占用和正在执行的动作作为设备点亮依据，不把库位存货或预留冒充设备使用。 */
export function deviceTaskIds(device: UsageDevice): string[] {
  return [...new Set([
    ...device.holdings.filter((holding) => holding.resourceUuid === device.uuid && holding.state !== 'reserved' && !holding.retained && holding.state !== 'uncertain').map((holding) => holding.taskUuid),
    ...device.activities.map(({ task }) => task.uuid),
    ...(device.executions || []).map((job) => job.task_uuid),
  ].filter(Boolean))]
}

export function locateCanvasResource(model: ResourceUsageModel, materialUuid?: string, siteUuid?: string) {
  if (!materialUuid && !siteUuid) return { device: undefined, site: undefined }
  const site = siteUuid ? model.sites.get(siteUuid) : [...model.sites.values()].find((entry) => entry.materialUuid === materialUuid)
  const device = site ? model.devices.find((entry) => entry.sites.some((item) => item.uuid === site.uuid))
    : model.devices.find((entry) => entry.uuid === materialUuid || entry.sites.some((item) => item.ownerUuid === materialUuid))
  return { device, site }
}
