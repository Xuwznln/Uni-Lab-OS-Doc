import { useMemo, useRef, useState } from 'react'
import { Boxes, ChevronDown, Cpu, Focus, Minus, Plus, RotateCcw, RotateCw, Scan, ZoomIn } from 'lucide-react'
import type { MaterialRecord, ResourceTemplateRecord } from '../types'

interface Point { x: number; y: number }
interface SceneObject { material: MaterialRecord; x: number; y: number; z: number; width: number; length: number; depth: number; order: number }
interface DragState { x: number; y: number; rotation: number; pan: Point; mode: 'rotate' | 'pan' }

const inventoryCategories = new Set(['beaker', 'sample_vial', 'powder', 'powder_reagent', 'liquid_reagent', 'tip_box', 'consumable'])

function isEquipment(material: MaterialRecord) {
  return material.isStructural || (!inventoryCategories.has(material.category) && material.currentLocation.kind !== 'site')
}

function project(x: number, y: number, z: number, angle: number): Point {
  const radians = angle * Math.PI / 180
  const rx = x * Math.cos(radians) - y * Math.sin(radians)
  const ry = x * Math.sin(radians) + y * Math.cos(radians)
  return { x: (rx - ry) * .58, y: (rx + ry) * .3 - z * .72 }
}

function points(value: Point[]) { return value.map((point) => `${point.x},${point.y}`).join(' ') }
function midpoint(value: Point[]) { return { x: value.reduce((sum, point) => sum + point.x, 0) / value.length, y: value.reduce((sum, point) => sum + point.y, 0) / value.length } }
function distance(left: Point, right: Point) { return Math.hypot(left.x - right.x, left.y - right.y) }
function isCylinder(material: MaterialRecord) { return ['beaker', 'sample_vial', 'powder', 'powder_reagent', 'liquid_reagent'].includes(material.category) }
function isRack(material: MaterialRecord) { return /stack|warehouse|rack/i.test(material.category) }

function worldObjects(materials: MaterialRecord[]): SceneObject[] {
  const byId = new Map(materials.map((material) => [material.uuid, material]))
  const cache = new Map<string, [number, number, number]>()
  const position = (material: MaterialRecord, seen = new Set<string>()): [number, number, number] => {
    const cached = cache.get(material.uuid); if (cached) return cached
    if (!material.parentUuid || seen.has(material.uuid)) return material.position
    const parent = byId.get(material.parentUuid); if (!parent) return material.position
    const nextSeen = new Set(seen).add(material.uuid); const root = position(parent, nextSeen)
    const result: [number, number, number] = [root[0] + material.position[0], root[1] + material.position[1], root[2] + material.position[2]]
    cache.set(material.uuid, result); return result
  }
  return materials.map((material) => {
    const [x, y, z] = position(material); const [width, length, depth] = material.size
    return { material, x, y, z, width: Math.max(width, 12), length: Math.max(length, 12), depth: Math.max(depth, 8), order: x + y + z * .1 }
  }).sort((left, right) => left.order - right.order)
}

export function LabObliqueOverview({ materials, templates = [], selectedId, onSelect, onSelectMaterialTemplate }: { materials: MaterialRecord[]; templates?: ResourceTemplateRecord[]; selectedId?: string; onSelect: (uuid: string) => void; onSelectMaterialTemplate?: (template: ResourceTemplateRecord) => void }) {
  const [rotation, setRotation] = useState(0)
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState<Point>({ x: 0, y: 0 })
  const [openTemplates, setOpenTemplates] = useState<'device' | 'material' | null>(null)
  const drag = useRef<DragState | null>(null)
  const visible = materials
  const deviceTemplates = useMemo(() => templates.filter((template) => template.resourceType === 'device'), [templates])
  const materialTemplates = useMemo(() => templates.filter((template) => template.resourceType !== 'device'), [templates])
  const objects = useMemo(() => worldObjects(visible), [visible])
  const projected = useMemo(() => objects.map((object) => {
    const bottom = [project(object.x, object.y, object.z, rotation), project(object.x + object.width, object.y, object.z, rotation), project(object.x + object.width, object.y + object.length, object.z, rotation), project(object.x, object.y + object.length, object.z, rotation)]
    const top = [project(object.x, object.y, object.z + object.depth, rotation), project(object.x + object.width, object.y, object.z + object.depth, rotation), project(object.x + object.width, object.y + object.length, object.z + object.depth, rotation), project(object.x, object.y + object.length, object.z + object.depth, rotation)]
    return { object, bottom, top }
  }), [objects, rotation])
  const bounds = useMemo(() => {
    const all = projected.flatMap(({ bottom, top }) => [...bottom, ...top]); if (!all.length) return { x: -500, y: -300, width: 1000, height: 600 }
    const xs = all.map((point) => point.x), ys = all.map((point) => point.y); const padding = 140
    return { x: Math.min(...xs) - padding, y: Math.min(...ys) - padding, width: Math.max(...xs) - Math.min(...xs) + padding * 2, height: Math.max(...ys) - Math.min(...ys) + padding * 2 }
  }, [projected])
  const width = bounds.width / zoom, height = bounds.height / zoom
  const center = { x: bounds.x + bounds.width / 2 + pan.x, y: bounds.y + bounds.height / 2 + pan.y }
  const viewBox = `${center.x - width / 2} ${center.y - height / 2} ${width} ${height}`
  const focusSelected = () => {
    const selected = projected.find(({ object }) => object.material.uuid === selectedId)
    if (!selected) return
    const corners = [...selected.bottom, ...selected.top]
    const target = {
      x: corners.reduce((sum, point) => sum + point.x, 0) / corners.length,
      y: corners.reduce((sum, point) => sum + point.y, 0) / corners.length,
    }
    setPan({ x: target.x - (bounds.x + bounds.width / 2), y: target.y - (bounds.y + bounds.height / 2) })
    setZoom(2.4)
  }

  return <div className="lab-oblique-overview" aria-label="实验室整体 2.5D 物料操作视图">
    <header><div><strong>实验室 2.5D</strong><span>{visible.length} / {materials.length} 个对象</span></div><div className="oblique-controls" aria-label="2.5D 视图控制"><button onClick={() => setRotation((value) => value - 15)} aria-label="向左旋转"><RotateCcw size={14} /></button><output>{Math.round(rotation)}°</output><button onClick={() => setRotation((value) => value + 15)} aria-label="向右旋转"><RotateCw size={14} /></button><button disabled={zoom <= 1} onClick={() => setZoom((value) => Math.max(1, value / 1.2))} aria-label="缩小"><Minus size={14} /></button><output>{Math.round(zoom * 100)}%</output><button onClick={() => setZoom((value) => Math.min(4, value * 1.2))} aria-label="放大"><Plus size={14} /></button><button onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }) }} aria-label="适应全部物料"><Scan size={14} /></button><button disabled={!selectedId} onClick={focusSelected} aria-label="聚焦已选物料"><Focus size={14} /></button></div></header>
    <div className="oblique-template-browser">
      <button aria-expanded={openTemplates === 'device'} onClick={() => setOpenTemplates((current) => current === 'device' ? null : 'device')}><Cpu size={15} /><span>仪器设备</span><ChevronDown size={14} /></button>
      {openTemplates === 'device' ? <div className="oblique-template-drawer" aria-label="设备模板"><header><strong>设备模板</strong><span>{deviceTemplates.length}</span></header>{deviceTemplates.map((template) => <article key={template.uuid}><Cpu size={14} /><div><strong>{template.displayName}</strong><small>{template.description || template.name}</small></div></article>)}{!deviceTemplates.length ? <p>暂无设备模板</p> : null}</div> : null}
      <button aria-expanded={openTemplates === 'material'} onClick={() => setOpenTemplates((current) => current === 'material' ? null : 'material')}><Boxes size={15} /><span>物料耗材</span><ChevronDown size={14} /></button>
      {openTemplates === 'material' ? <div className="oblique-template-drawer" aria-label="物料模板"><header><strong>物料模板</strong><span>{materialTemplates.length}</span></header>{materialTemplates.map((template) => <button key={template.uuid} onClick={() => onSelectMaterialTemplate?.(template)}><Boxes size={14} /><div><strong>{template.displayName}</strong><small>{template.description || template.name}</small></div></button>)}{!materialTemplates.length ? <p>暂无物料模板</p> : null}</div> : null}
    </div>
    <svg viewBox={viewBox} role="group" aria-label="实验室整体 2.5D 场景" tabIndex={0} onKeyDown={(event) => { if (event.key === 'Escape') onSelect('') }} onWheel={(event) => { event.preventDefault(); setZoom((value) => Math.min(4, Math.max(1, value * (event.deltaY < 0 ? 1.12 : 1 / 1.12)))) }} onPointerDown={(event) => { drag.current = { x: event.clientX, y: event.clientY, rotation, pan, mode: event.shiftKey ? 'pan' : 'rotate' }; event.currentTarget.setPointerCapture(event.pointerId) }} onPointerMove={(event) => { const state = drag.current; if (!state) return; if (state.mode === 'rotate') setRotation(state.rotation + (event.clientX - state.x) * .25); else { const scale = width / Math.max(event.currentTarget.clientWidth, 1); setPan({ x: state.pan.x - (event.clientX - state.x) * scale, y: state.pan.y - (event.clientY - state.y) * scale }) } }} onPointerUp={(event) => { drag.current = null; event.currentTarget.releasePointerCapture(event.pointerId) }}>
      <defs><filter id="lab-box-shadow"><feDropShadow dx="4" dy="8" stdDeviation="6" floodColor="#253858" floodOpacity=".16" /></filter></defs>
      {projected.map(({ object, bottom, top }) => {
        const selected = object.material.uuid === selectedId, structural = isEquipment(object.material)
        const cylinder = isCylinder(object.material)
        const rack = isRack(object.material)
        const baseCenter = midpoint(bottom), topCenter = midpoint(top)
        const radiusX = Math.max(4, (distance(top[0], top[1]) + distance(top[1], top[2])) / 8)
        const radiusY = Math.max(2, radiusX * .34)
        return <g key={object.material.uuid} className={`lab-oblique-object ${structural ? 'equipment' : 'inventory'} ${selected ? 'selected' : ''}`} role="button" tabIndex={0} aria-label={object.material.name} onClick={(event) => { event.stopPropagation(); onSelect(object.material.uuid) }} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') onSelect(object.material.uuid) }}>
          {cylinder ? <><path className="cylinder-body" d={`M ${topCenter.x - radiusX} ${topCenter.y} L ${baseCenter.x - radiusX} ${baseCenter.y} A ${radiusX} ${radiusY} 0 0 0 ${baseCenter.x + radiusX} ${baseCenter.y} L ${topCenter.x + radiusX} ${topCenter.y} Z`} /><ellipse className="cylinder-top" cx={topCenter.x} cy={topCenter.y} rx={radiusX} ry={radiusY} /><ellipse className="cylinder-rim" cx={topCenter.x} cy={topCenter.y} rx={radiusX * .72} ry={radiusY * .72} /></> : <><polygon className="box-side side-left" points={points([bottom[3], bottom[2], top[2], top[3]])} /><polygon className="box-side side-right" points={points([bottom[1], bottom[2], top[2], top[1]])} /><polygon className="box-top" points={points(top)} />{rack ? [0.28, 0.56, 0.82].map((ratio) => { const shelf = bottom.map((point, index) => ({ x: point.x + (top[index].x - point.x) * ratio, y: point.y + (top[index].y - point.y) * ratio })); return <polygon className="rack-shelf" points={points(shelf)} key={ratio} /> }) : null}</>}
          {structural || selected ? <g className="object-label" transform={`translate(${top[0].x},${top[0].y - 12})`}><rect x="-3" y="-16" width={Math.max(95, object.material.name.length * 11)} height="22" rx="3" /><text x="4" y="-2">{object.material.name}</text></g> : null}
        </g>
      })}
    </svg>
    <div className="oblique-help"><ZoomIn size={13} />滚轮缩放 · 拖动旋转 · Shift + 拖动平移 · Esc 清除选择</div>
  </div>
}
