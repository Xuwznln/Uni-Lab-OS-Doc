import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { ChevronRight, Search } from 'lucide-react'
import type { MaterialRecord } from '../types'
import { materialLabel } from '../lib/materialLabel'
import type { MaterialLabel } from '../lib/materialLabel'

type MaterialSite = MaterialRecord['sites'][number]

interface TreeNode { material: MaterialRecord; children: TreeNode[]; sites: MaterialSite[] }

/**
 * 将物料清单整理为展示树，并把库位占用物料挂到库位所属物料下面。
 *
 * @param materials OS 返回的完整物料快照。
 * @returns 按名称排序的物料树；每个节点保留其完整库位清单。
 */
function buildTree(materials: MaterialRecord[], labels: Map<string, MaterialLabel>): TreeNode[] {
  const byLabel = (a: MaterialRecord, b: MaterialRecord) => (labels.get(a.uuid)?.text || a.name).localeCompare(labels.get(b.uuid)?.text || b.name, 'zh-CN', { numeric: true })
  const byParent = new Map<string, MaterialRecord[]>()
  const byId = new Set(materials.map((material) => material.uuid))
  const siteOwnerByOccupant = new Map<string, string>()
  materials.forEach((owner) => owner.sites.forEach((site) => {
    if (site.occupiedMaterialUuid) siteOwnerByOccupant.set(site.occupiedMaterialUuid, owner.uuid)
  }))
  materials.forEach((material) => {
    const siteOwner = siteOwnerByOccupant.get(material.uuid)
    const parent = siteOwner && byId.has(siteOwner)
      ? siteOwner
      : material.parentUuid && byId.has(material.parentUuid)
        ? material.parentUuid
        : '__root__'
    byParent.set(parent, [...(byParent.get(parent) || []), material])
  })
  const build = (material: MaterialRecord): TreeNode => ({
    material,
    children: (byParent.get(material.uuid) || []).sort(byLabel).map(build),
    sites: material.sites,
  })
  return (byParent.get('__root__') || []).sort(byLabel).map(build)
}

/**
 * 过滤物料树，同时保持命中库位与占用物料之间的真实关系。
 *
 * @param nodes 待过滤的完整物料树。
 * @param query 用户输入的物料、设备或库位关键字。
 * @returns 仅包含命中节点、祖先路径和命中库位的展示树；不会修改原始树。
 */
function filterTree(nodes: TreeNode[], query: string, labels: Map<string, MaterialLabel>): TreeNode[] {
  const needle = query.trim().toLowerCase()
  if (!needle) return nodes
  return nodes.flatMap((node) => {
    const children = filterTree(node.children, query, labels)
    const childIds = new Set(children.map((child) => child.material.uuid))
    const sites = node.sites.filter((site) => [site.uuid, site.name, site.occupiedMaterialName]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
      .includes(needle))
    sites.forEach((site) => {
      if (!site.occupiedMaterialUuid || childIds.has(site.occupiedMaterialUuid)) return
      const occupant = node.children.find((child) => child.material.uuid === site.occupiedMaterialUuid)
      if (!occupant) return
      children.push({ ...occupant, children: [], sites: [] })
      childIds.add(occupant.material.uuid)
    })
    const own = [labels.get(node.material.uuid)?.text, node.material.name, node.material.uuid, node.material.barcode, node.material.currentLocation.label]
      .join(' ')
      .toLowerCase()
      .includes(needle)
    return own || sites.length || children.length ? [{ ...node, children, sites }] : []
  })
}

/**
 * 展示可搜索的物料、设备与库位层级目录。
 *
 * @param materials OS 返回的物料快照。
 * @param selectedId 当前选中的物料 UUID。
 * @param onSelect 选择物料时触发的回调。
 * @returns 物料目录侧栏。
 */
export function MaterialHierarchyTree({ materials, selectedId, onSelect, onSelectSite, renderBadge, visibleMaterialIds, templateNames }: { materials: MaterialRecord[]; selectedId?: string; onSelect: (uuid: string) => void; onSelectSite?: (owner: string, site: string) => void; templateNames?: Map<string, string>; renderBadge?: (uuid: string) => ReactNode; visibleMaterialIds?: Set<string> }) {
  // 展示标签：主行条码 / 可读名，副行模板显示名；name 为 UUID 的物料不再以 UUID 示人。
  const labels = useMemo(() => new Map(materials.map((material) => [material.uuid,
    materialLabel(material, material.templateName ?? templateNames?.get(material.resourceTemplateUuid || ''))])), [materials, templateNames])
  const roots = useMemo(() => buildTree(materials, labels), [materials, labels])
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(roots.map((node) => node.material.uuid)))
  useEffect(() => {
    if (!selectedId) return
    const ancestors = new Set<string>()
    let parent = materials.find((material) => material.uuid === selectedId)?.parentUuid
    while (parent && !ancestors.has(parent)) {
      ancestors.add(parent)
      parent = materials.find((material) => material.uuid === parent)?.parentUuid
    }
    setExpanded((current) => [...ancestors].every((uuid) => current.has(uuid)) ? current : new Set([...current, ...ancestors]))
  }, [selectedId, materials])
  const visible = useMemo(() => {
    const filterIds = (nodes: TreeNode[]): TreeNode[] => nodes.flatMap((node) => {
      const children = filterIds(node.children)
      return !visibleMaterialIds || visibleMaterialIds.has(node.material.uuid) || children.length ? [{ ...node, children }] : []
    })
    return filterTree(filterIds(roots), query, labels)
  }, [roots, query, visibleMaterialIds, labels])
  return <aside className="electron-material-tree" aria-label="物料目录">
    <header><div><strong>物料列表</strong><span>({visibleMaterialIds ? `${visibleMaterialIds.size} / ${materials.length}` : materials.length})</span></div></header>
    <label><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="检索物料、设备或库位" /></label>
    <div className="electron-tree-legend" aria-label="库位状态说明"><span><i className="occupied" />有物料</span><span><i className="empty" />空库位</span></div>
    <div className="electron-tree-rows" role="tree">{visible.map((node) => <TreeRow key={node.material.uuid} node={node} depth={0} labels={labels} queryActive={Boolean(query.trim())} expanded={expanded} selectedId={selectedId} onSelect={onSelect} renderBadge={renderBadge} onSelectSite={onSelectSite} onToggle={(uuid) => setExpanded((current) => { const next = new Set(current); next.has(uuid) ? next.delete(uuid) : next.add(uuid); return next })} />)}</div>
  </aside>
}

/**
 * 递归展示单个物料节点及其可见库位和子物料。
 *
 * @param node 当前展示节点；搜索时只包含命中的库位和子节点。
 * @param depth 节点在树中的展示深度。
 * @param queryActive 是否处于搜索状态。
 * @param expanded 用户手动展开的节点集合。
 * @param selectedId 当前选中的物料 UUID。
 * @param onSelect 选择物料时触发的回调。
 * @param onToggle 展开或收起节点时触发的回调。
 * @returns 当前节点及其递归子树。
 */
function TreeRow({ node, depth, labels, queryActive, expanded, selectedId, onSelect, onToggle, renderBadge, onSelectSite }: { node: TreeNode; depth: number; labels: Map<string, MaterialLabel>; queryActive: boolean; expanded: Set<string>; selectedId?: string; onSelect: (uuid: string) => void; onToggle: (uuid: string) => void; renderBadge?: (uuid: string) => ReactNode; onSelectSite?: (owner: string, site: string) => void }) {
  const label = labels.get(node.material.uuid) || materialLabel(node.material, node.material.templateName)
  const hasContent = node.children.length > 0 || node.sites.length > 0
  const open = hasContent && (queryActive || expanded.has(node.material.uuid))
  const occupiedSite = node.material.currentLocation.kind === 'site'
  const occupiedIds = new Set(node.sites.map((site) => site.occupiedMaterialUuid).filter(Boolean))
  const unboundChildren = node.children.filter((child) => !occupiedIds.has(child.material.uuid))
  return <>
    <div className={`electron-tree-row ${selectedId === node.material.uuid ? 'selected' : ''}`} role="treeitem" aria-level={depth + 1} aria-expanded={hasContent ? open : undefined} style={{ '--tree-depth': depth } as React.CSSProperties}>
      <span className="tree-grip">⠿</span>{hasContent ? <button className={`tree-toggle ${open ? 'open' : ''}`} aria-label={`${open ? '收起' : '展开'} ${node.material.name}`} onClick={() => onToggle(node.material.uuid)}><ChevronRight size={14} /></button> : <span className="tree-toggle-spacer" />}
      <button className="tree-label" data-usage-material={node.material.uuid} aria-label={label.text} title={label.text} onClick={() => onSelect(node.material.uuid)}><strong>{label.primary}</strong>{label.secondary ? <small>{label.secondary}</small> : null}</button>{renderBadge?.(node.material.uuid)}{occupiedSite ? <i className="site-dot occupied" title="已占用库位" /> : null}
    </div>
    {open ? <>{node.sites.map((site) => {
      const occupant = node.children.find((child) => child.material.uuid === site.occupiedMaterialUuid)
      return occupant ? <TreeRow key={occupant.material.uuid} node={occupant} depth={depth + 1} labels={labels} queryActive={queryActive} expanded={expanded} selectedId={selectedId} onSelect={onSelect} renderBadge={renderBadge} onSelectSite={onSelectSite} onToggle={onToggle} /> : <div key={site.uuid} className="electron-tree-row site-row" role="treeitem" aria-level={depth + 2} aria-label={`${site.name}，未占用`} style={{ '--tree-depth': depth + 1 } as React.CSSProperties}><span className="tree-grip" /><span className="tree-toggle-spacer" /><button className="tree-label" data-usage-site={site.uuid} onClick={() => onSelectSite?.(node.material.uuid, site.uuid)}>{site.name}</button>{renderBadge?.(site.uuid)}<i className="site-dot empty" title="未占用" /></div>
    })}{unboundChildren.map((child) => <TreeRow key={child.material.uuid} node={child} depth={depth + 1} labels={labels} queryActive={queryActive} expanded={expanded} selectedId={selectedId} onSelect={onSelect} renderBadge={renderBadge} onSelectSite={onSelectSite} onToggle={onToggle} />)}</> : null}
  </>
}
