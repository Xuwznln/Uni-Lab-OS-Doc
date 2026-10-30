import { useEffect, useRef, type ReactNode } from 'react'
import { capacityText, stricterCapacity } from '../lib/capacity'
import type { MaterialRecord, ReagentInfoRecord, ReagentRecord } from '../types'
import { ChemicalStructurePreview } from './ChemicalStructurePreview'

const states: Record<string, string> = { solid: '固体', liquid: '液体', gas: '气体', other: '其他', unknown: '未知' }

function Field({ label, children, wide = false }: { label: string; children?: ReactNode; wide?: boolean }) {
  return <div className={wide ? 'wide' : undefined}><dt>{label}</dt><dd>{children === undefined || children === null || children === '' ? <span className="reagent-detail-missing">未填写</span> : children}</dd></div>
}

function Timestamp({ value }: { value?: string }) {
  if (!value) return <span className="reagent-detail-missing">未记录</span>
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : <time dateTime={value}>{date.toLocaleString('zh-CN', { hour12: false })}</time>
}

function DetailDialog({ title, subtitle, onClose, children }: { title: string; subtitle: string; onClose: () => void; children: ReactNode }) {
  const panel = useRef<HTMLDivElement>(null)
  const close = useRef(onClose)
  close.current = onClose
  useEffect(() => {
    const previous = document.activeElement
    panel.current?.querySelector<HTMLButtonElement>('button')?.focus()
    return () => { if (previous instanceof HTMLElement && previous.isConnected) previous.focus() }
  }, [])
  return <div className="dialog-backdrop" role="presentation"><div ref={panel} className="material-write-dialog reagent-dialog reagent-details-dialog" role="dialog" aria-modal="true" aria-label={title} onKeyDown={(event) => {
    if (event.key === 'Escape') { event.stopPropagation(); close.current(); return }
    if (event.key !== 'Tab') return
    const focusable = panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], summary, [tabindex="0"]')
    if (!focusable?.length) return
    const first = focusable[0], last = focusable[focusable.length - 1]
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
  }}>
    <header><div><span>REAGENT DETAILS</span><h2>{title}</h2><p>{subtitle}</p></div><button type="button" aria-label="关闭详情" onClick={onClose}>×</button></header>
    <div className="dialog-content reagent-details-content">{children}</div>
  </div></div>
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return <section className="reagent-detail-section"><h3>{title}</h3><dl className="reagent-detail-fields">{children}</dl></section>
}

function AliasList({ aliases }: { aliases?: string[] }) {
  return aliases?.length ? <ul className="reagent-detail-aliases">{aliases.map((alias, index) => <li key={index}>{alias}</li>)}</ul> : <span className="reagent-detail-missing">未填写</span>
}

// 扩展字段可能来自领域包；递归显示原值，保留 0、false 和嵌套结构。
function MetadataValue({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="reagent-detail-missing">未填写</span>
  if (typeof value === 'boolean') return <>{value ? '是' : '否'}</>
  if (Array.isArray(value)) return value.length ? <ul className="reagent-detail-values">{value.map((item, index) => <li key={index}><MetadataValue value={item} /></li>)}</ul> : <>空列表</>
  if (typeof value === 'object') {
    const entries = Object.entries(value)
    return entries.length ? <dl className="reagent-detail-metadata">{entries.map(([key, item]) => <Field key={key} label={key}><MetadataValue value={item} /></Field>)}</dl> : <>空对象</>
  }
  return <>{String(value)}</>
}

function MetadataSection({ metadata }: { metadata?: Record<string, unknown> }) {
  if (!metadata || !Object.keys(metadata).length) return null
  const { custom_parameters: parameters, ...other } = metadata
  const namedParameters = Array.isArray(parameters) && parameters.every((value) => value && typeof value === 'object' && 'name' in value && 'value' in value)
  return <>
    {parameters !== undefined ? <Section title="自定义参数">{namedParameters && parameters.length ? parameters.map((parameter, index) => {
      const { name, value, ...extra } = parameter
      return <Field key={index} label={String(name)} wide={Object.keys(extra).length > 0}><MetadataValue value={value} />{Object.keys(extra).length ? <MetadataValue value={extra} /> : null}</Field>
    }) : <Field label="参数" wide><MetadataValue value={parameters} /></Field>}</Section> : null}
    {Object.keys(other).length ? <Section title="其他信息">{Object.entries(other).map(([key, value]) => <Field key={key} label={key} wide={typeof value === 'object'}><MetadataValue value={value} /></Field>)}</Section> : null}
  </>
}

function ChemicalFields({ item }: { item: Pick<ReagentInfoRecord, 'name' | 'nameEn' | 'cas' | 'molecularFormula' | 'smiles' | 'inchiKey' | 'molecularWeight'> & { aliases?: string[] } }) {
  return <>
    <Section title="化学身份">
      <Field label="试剂名称">{item.name}</Field><Field label="英文名称">{item.nameEn}</Field>
      <Field label="别名" wide><AliasList aliases={item.aliases} /></Field>
      <Field label="CAS 号">{item.cas}</Field><Field label="分子式">{item.molecularFormula}</Field>
      <Field label="分子量">{item.molecularWeight == null ? undefined : `${item.molecularWeight} g/mol`}</Field>
      <Field label="InChIKey" wide>{item.inchiKey ? <code>{item.inchiKey}</code> : undefined}</Field>
      <Field label="SMILES" wide>{item.smiles ? <code>{item.smiles}</code> : undefined}</Field>
    </Section>
    <ChemicalStructurePreview smiles={item.smiles || ''} />
  </>
}

export function CatalogDetails({ item, onClose }: { item: ReagentInfoRecord; onClose: () => void }) {
  return <DetailDialog title={`${item.name} 目录详情`} subtitle="化学身份、基础理化信息与自定义参数" onClose={onClose}>
    <ChemicalFields item={item} />
    <Section title="物理属性">
      <Field label="常温物态">{states[item.physicalState] || item.physicalState}</Field>
      <Field label="参考密度">{item.densityGPerMl == null ? undefined : `${item.densityGPerMl} g/mL`}</Field>
      <Field label="说明" wide>{item.description}</Field>
    </Section>
    <MetadataSection metadata={item.metadata} />
    <Section title="记录信息">
      <Field label="创建时间"><Timestamp value={item.createdAt} /></Field><Field label="更新时间"><Timestamp value={item.updatedAt} /></Field>
      <Field label="目录 UUID" wide><code>{item.uuid}</code></Field>
    </Section>
  </DetailDialog>
}

export function InventoryDetails({ item, material, onClose }: { item: ReagentRecord; material?: MaterialRecord; onClose: () => void }) {
  const maximum = item.maximumCapacity ?? stricterCapacity(item.containerCapacity, item.loadingLimits)
  const graphIdentity = item.containerBarcode?.startsWith('UNILAB-GRAPH-') ? item.containerBarcode : undefined
  return <DetailDialog title={`${item.name} 库存详情`} subtitle={item.containerName || material?.name || item.materialUuid} onClose={onClose}>
    <Section title="容器与库存">
      <Field label="容器">{item.containerName || material?.name}</Field><Field label="库位">{material?.currentLocation?.label}</Field>
      <Field label="容器条码" wide>{graphIdentity ? undefined : item.containerBarcode}</Field>
      <Field label="当前余量">{item.quantity == null ? undefined : `${item.quantity} ${item.quantityUnit || ''}`}</Field>
      <Field label="任务预留量">{item.activeWorkflowReservedQuantity == null ? undefined : `${item.activeWorkflowReservedQuantity} ${item.quantityUnit || ''}`}</Field>
      <Field label="最大装料量">{capacityText(maximum)}</Field><Field label="容器额定容量">{capacityText(item.ratedCapacity)}</Field>
      <Field label="浓度">{item.concentrationValue == null ? undefined : `${item.concentrationValue} ${item.concentrationUnit || ''}`}</Field>
      <Field label="库存物态">{states[item.physicalState] || item.physicalState}</Field>
      <Field label="库存密度">{item.densityGPerMl == null ? undefined : `${item.densityGPerMl} g/mL`}</Field>
      <Field label="密度来源">{item.densitySource === 'dictionary' ? '入库时的试剂目录' : item.densitySource}</Field>
      <Field label="说明" wide>{item.description}</Field>
    </Section>
    <ChemicalFields item={item} />
    <MetadataSection metadata={item.metaData} />
    <Section title="记录信息">
      <Field label="创建时间"><Timestamp value={item.createdAt} /></Field><Field label="更新时间"><Timestamp value={item.updatedAt} /></Field>
      <Field label="库存修订">{item.revision}</Field><Field label="容器修订">{item.materialRevision}</Field>
      {item.source ? <Field label="来源" wide>{item.source}</Field> : null}
      {item.sourceReagentUuid ? <Field label="分装源瓶 UUID" wide><code>{item.sourceReagentUuid}</code></Field> : null}
      {item.dispenseCommandId ? <Field label="分装批次" wide><code>{item.dispenseCommandId}</code></Field> : null}
      <Field label="试剂 UUID" wide><code>{item.uuid}</code></Field>
      <Field label="容器 UUID" wide><code>{item.materialUuid}</code></Field>
      <Field label="目录 UUID" wide><code>{item.reagentInfoUuid}</code></Field>
      {graphIdentity ? <Field label="图初始化标识" wide><code>{graphIdentity}</code></Field> : null}
    </Section>
  </DetailDialog>
}
