import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowRight, BookOpen, FlaskConical, History, PackagePlus, Pencil, Plus, Search, Trash2 } from 'lucide-react'
import { ReagentCapacityFields } from '../components/CapacityFields'
import { ChemicalStructurePreview } from '../components/ChemicalStructurePreview'
import { CatalogDetails, InventoryDetails } from '../components/ReagentDetails'
import { SearchableContainerSelect } from '../components/SearchableContainerSelect'
import { capacityText, configuredCapacity, convertMaximum, defaultQuantityUnit, maximumError, parseMaximum, quantityUnitError, quantityUnits, stricterCapacity, type MaximumFields, type QuantityContext } from '../lib/capacity'
import { Button, EmptyState, PageHeader, Panel, PanelHeader } from '../components/ui'
import { EDGE_API_BASE, InventoryCommandRejectedError, createReagent, createReagentInfo, deleteReagent, deleteReagentInfo, dispenseReagent, updateReagent, updateReagentInfo, loadReagentHistory, loadReagentInfos, loadReagents, loadResourceTemplates, lookupCompoundByCas } from '../lib/edgeClient'
import type { CompoundLookupResult, MaterialRecord, ReagentHistoryRecord, ReagentInfoRecord, ReagentRecord, ResourceTemplateRecord } from '../types'
import '../reagent-inventory.css'

const physicalStateLabels: Record<ReagentInfoRecord['physicalState'], string> = {
  solid: '固体', liquid: '液体', gas: '气体', other: '其他', unknown: '未知',
}
type IdentityForm = {
  name: string; nameEn: string; cas: string; aliases: string; molecularFormula: string
  smiles: string; inchiKey: string; molecularWeight: string; density: string
  physicalState: ReagentInfoRecord['physicalState']; description: string
}
type CustomParameter = { id: number; name: string; value: string }
type DispenseRow = { id: number; materialUuid: string; quantity: string } & MaximumFields
type EditForm = { quantity: string; concentrationValue: string; concentrationUnit: string; description: string } & MaximumFields
type PendingDispense = { request: Parameters<typeof dispenseReagent>[0]; source: ReagentRecord; rows: DispenseRow[] }
export type RegisterFields = { materialUuid: string; reagentInfoUuid: string; quantity: string; quantityUnit: string; concentrationValue: string; concentrationUnit: string; description: string } & MaximumFields

const pendingDispenseKey = `unilab.pending-reagent-dispense:${EDGE_API_BASE}`

/** 切页或刷新后仍须先核对同一个分装请求，不能丢掉可能已经执行的命令身份。 */
function restorePendingDispense(): PendingDispense | null {
  try {
    const stored = window.sessionStorage.getItem(pendingDispenseKey)
    if (!stored) return null
    const pending = JSON.parse(stored) as PendingDispense
    return pending.request?.commandId && pending.source?.uuid === pending.request.sourceReagentUuid
      && Array.isArray(pending.rows) && Array.isArray(pending.request.targets) ? pending : null
  } catch { return null }
}

function editFields(item: ReagentRecord): EditForm {
  return { quantity: numberText(item.quantity) || '', concentrationValue: numberText(item.concentrationValue) || '', concentrationUnit: item.concentrationUnit || '', description: item.description || '' }
}

function registrationContext(form: RegisterFields, info?: ReagentInfoRecord, container?: MaterialRecord): QuantityContext {
  return { physicalState: info?.physicalState, densityGPerMl: info?.densityGPerMl, concentrationValue: numberOrUndefined(form.concentrationValue), configuredCapacity: configuredCapacity(container?.config?.capacity) }
}

function dispenseContext(source: ReagentRecord, container?: MaterialRecord): QuantityContext {
  return { physicalState: source.physicalState, densityGPerMl: source.densityGPerMl, concentrationValue: source.concentrationValue, configuredCapacity: configuredCapacity(container?.config?.capacity) }
}

function concentrationError(value: string, unit: string): string {
  if (!value.trim()) return ''
  if (!Number.isFinite(Number(value)) || Number(value) < 0) return '浓度须为非负的有限数值。'
  return unit.trim() ? '' : '填写浓度时，请同时填写浓度单位。'
}

function legacyEditQuantity(target: ReagentRecord, form: EditForm): number | undefined {
  const concentrationUnchanged = numberOrUndefined(form.concentrationValue) === target.concentrationValue
    && (form.concentrationValue.trim() ? textOrUndefined(form.concentrationUnit) || '' : '') === (target.concentrationUnit || '')
  return concentrationUnchanged ? target.quantity : undefined
}

function containerOptions(containers: MaterialRecord[]) {
  return containers.map((item) => ({ value: item.uuid, label: item.name, description: item.currentLocation.label }))
}

const containerTagLabels: Record<string, string> = {
  liquid_reagent: '液体试剂瓶',
  powder_reagent: '粉末试剂瓶',
  sample_vial: '样品瓶',
  beaker: '烧杯',
}

/** 从领域包容器模板中提取有区分度的标签，排除 container 和所有模板共有的包级标签。 */
export function deriveContainerFilterTags(templates: ResourceTemplateRecord[]) {
  const containerTemplates = templates.filter((template) => (template.tags || []).includes('container'))
  const frequency = new Map<string, number>()
  for (const template of containerTemplates) {
    for (const tag of new Set((template.tags || []).map((value) => value.trim()).filter((value) => value && value !== 'container'))) {
      frequency.set(tag, (frequency.get(tag) || 0) + 1)
    }
  }
  return [...frequency]
    .filter(([, count]) => containerTemplates.length <= 1 || count < containerTemplates.length)
    .map(([tag]) => tag)
    .sort((left, right) => containerTagLabel(left).localeCompare(containerTagLabel(right), 'zh-CN'))
}

function containerTagLabel(tag: string) {
  return containerTagLabels[tag] || tag.replaceAll('_', ' ')
}

function filterContainersByTag(containers: MaterialRecord[], templateTags: Map<string, Set<string>>, selectedTag: string) {
  return selectedTag ? containers.filter((item) => item.resourceTemplateUuid && templateTags.get(item.resourceTemplateUuid)?.has(selectedTag)) : containers
}

function ContainerTagFilter({ tags, selectedTag, containers, templateTags, onChange, disabled = false }: { tags: string[]; selectedTag: string; containers: MaterialRecord[]; templateTags: Map<string, Set<string>>; onChange: (tag: string) => void; disabled?: boolean }) {
  if (!tags.length) return null
  const count = (tag: string) => filterContainersByTag(containers, templateTags, tag).length
  return <div className="container-tag-filter" role="group" aria-label="按容器标签筛选">
    <span>容器类型</span>
    <button type="button" disabled={disabled} aria-pressed={!selectedTag} onClick={() => onChange('')}>全部<em>{containers.length}</em></button>
    {tags.map((tag) => <button key={tag} type="button" disabled={disabled} aria-pressed={selectedTag === tag} onClick={() => onChange(tag)}>{containerTagLabel(tag)}<em>{count(tag)}</em></button>)}
  </div>
}

/** 生成分装命令 ID；只为同一份冻结请求重试复用。 */
function newCommandId() {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `dispense-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

/** 汇总分装行：合计、分装后剩余，以及每行是否合法。 */
export function summariseDispense(rows: DispenseRow[], available: number) {
  const parsed = rows.map((row) => ({ ...row, value: Number(row.quantity) }))
  const validRows = parsed.filter((row) => row.materialUuid && Number.isFinite(row.value) && row.value > 0)
  const total = validRows.reduce((sum, row) => sum + row.value, 0)
  const duplicated = new Set(validRows.map((row) => row.materialUuid)).size !== validRows.length
  const remaining = available - total
  const ready = validRows.length === rows.length && rows.length > 0 && !duplicated && total > 0 && remaining >= -1e-9
  return { total, remaining, duplicated, ready }
}
const emptyIdentity: IdentityForm = { name: '', nameEn: '', cas: '', aliases: '', molecularFormula: '', smiles: '', inchiKey: '', molecularWeight: '', density: '', physicalState: 'unknown', description: '' }

export function isValidCas(value: string) {
  if (!/^\d{2,7}-\d{2}-\d$/.test(value)) return false
  const digits = value.replaceAll('-', '')
  const body = digits.slice(0, -1)
  let sum = 0
  for (let index = body.length - 1, weight = 1; index >= 0; index -= 1, weight += 1) sum += Number(body[index]) * weight
  return sum % 10 === Number(digits.at(-1))
}

export function ReagentsPage({ materials, connected, onNotify, onRefresh }: { materials: MaterialRecord[]; connected: boolean; onNotify: (message: string) => void; onRefresh?: () => void }) {
  const [pendingDispense, setPendingDispense] = useState(restorePendingDispense)
  const [view, setView] = useState<'inventory' | 'catalog'>('inventory')
  const [query, setQuery] = useState('')
  const [dialog, setDialog] = useState<'catalog' | 'register' | 'dispense' | 'edit' | null>(pendingDispense ? 'dispense' : null)
  const [detailSelection, setDetailSelection] = useState<{ kind: 'catalog' | 'inventory'; uuid: string } | null>(null)
  const [editTarget, setEditTarget] = useState<ReagentRecord | null>(null)
  const [editForm, setEditForm] = useState<EditForm>({ quantity: '', concentrationValue: '', concentrationUnit: '', description: '' })
  const [saving, setSaving] = useState(false)
  const [dispenseSource, setDispenseSource] = useState<ReagentRecord | null>(pendingDispense?.source || null)
  const [dispenseRows, setDispenseRows] = useState<DispenseRow[]>(pendingDispense?.rows || [])
  const dispenseInFlight = useRef(false)
  const [catalogEditTarget, setCatalogEditTarget] = useState<ReagentInfoRecord | null>(null)
  const [identityForm, setIdentityForm] = useState<IdentityForm>(emptyIdentity)
  const [customParameters, setCustomParameters] = useState<CustomParameter[]>([])
  const [customParametersTouched, setCustomParametersTouched] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [formError, setFormError] = useState('')
  const [lookup, setLookup] = useState<{ phase: 'idle' | 'loading' | 'success' | 'warning'; message: string; blocks: boolean }>({ phase: 'idle', message: '有效 CAS 会自动查询化学信息；自配物质可留空。', blocks: false })
  const previousCandidate = useRef<CompoundLookupResult['compound']>(undefined)
  const [registerForm, setRegisterForm] = useState<RegisterFields>({ materialUuid: '', reagentInfoUuid: '', quantity: '', quantityUnit: 'mL', concentrationValue: '', concentrationUnit: '%', description: '' })
  const [historyReagent, setHistoryReagent] = useState<ReagentRecord | null>(null)
  const infosQuery = useQuery({ queryKey: ['reagent-infos'], queryFn: ({ signal }) => loadReagentInfos(signal), enabled: connected, retry: 1, refetchInterval: 30_000, refetchOnWindowFocus: true })
  const reagentsQuery = useQuery({ queryKey: ['reagents'], queryFn: ({ signal }) => loadReagents(signal), enabled: connected, retry: 1, refetchInterval: 15_000, refetchOnWindowFocus: true })
  const historyQuery = useQuery({ queryKey: ['reagent-history', historyReagent?.materialUuid], queryFn: ({ signal }) => loadReagentHistory(historyReagent?.materialUuid || '', signal), enabled: connected && Boolean(historyReagent), retry: 1 })
  const templatesQuery = useQuery({ queryKey: ['resource-templates'], queryFn: ({ signal }) => loadResourceTemplates(signal), enabled: connected, retry: 1 })
  const reagentMaterialIds = new Set((reagentsQuery.data || []).map((item) => item.materialUuid))
  // 只有模板带 container 标签的物料才能承载试剂（与后端 require_container 同一规则）；
  // 模板尚未加载时不放行任何容器，避免列表先宽后窄闪动。
  const containerTemplateIds = useMemo(() => new Set((templatesQuery.data || []).filter((template) => (template.tags || []).includes('container')).map((template) => template.uuid)), [templatesQuery.data])
  const containers = materials.filter((material) => !material.isStructural && !reagentMaterialIds.has(material.uuid) && Boolean(material.resourceTemplateUuid) && containerTemplateIds.has(material.resourceTemplateUuid as string))
  const containerFilterTags = useMemo(() => deriveContainerFilterTags(templatesQuery.data || []), [templatesQuery.data])
  const sourceMaterial = dispenseSource ? materials.find((material) => material.uuid === dispenseSource.materialUuid) : undefined
  const sourceTemplateTags = (templatesQuery.data || []).find((template) => template.uuid === sourceMaterial?.resourceTemplateUuid)?.tags || []
  const preferredContainerTag = containerFilterTags.find((tag) => sourceTemplateTags.includes(tag)) || ''
  const keyword = query.trim().toLowerCase()
  const infos = useMemo(() => (infosQuery.data || []).filter((item) => !keyword || [item.name, item.nameEn, item.cas, item.molecularFormula, ...item.aliases].some((value) => value?.toLowerCase().includes(keyword))), [infosQuery.data, keyword])
  const inventory = useMemo(() => (reagentsQuery.data || []).filter((item) => !keyword || [item.name, item.cas, item.containerName, item.containerBarcode].some((value) => value?.toLowerCase().includes(keyword))), [reagentsQuery.data, keyword])
  const catalogDetails = detailSelection?.kind === 'catalog' ? infosQuery.data?.find((item) => item.uuid === detailSelection.uuid) : undefined
  const inventoryDetails = detailSelection?.kind === 'inventory' ? reagentsQuery.data?.find((item) => item.uuid === detailSelection.uuid) : undefined

  function openDetails(kind: 'catalog' | 'inventory', uuid: string) {
    if (pendingDispense) return
    setDialog(null); setHistoryReagent(null); setDetailSelection({ kind, uuid })
  }

  function changeView(next: 'inventory' | 'catalog') {
    setView(next); setDetailSelection(null); setHistoryReagent(null)
  }

  useEffect(() => {
    if (dialog !== 'catalog') return
    const cas = identityForm.cas.trim()
    if (catalogEditTarget && cas === (catalogEditTarget.cas || '')) {
      setLookup({ phase: 'idle', message: cas ? '当前目录项的 CAS，可直接修改其他信息。' : '自配物质可留空 CAS。', blocks: false })
      return
    }
    if (!cas || !isValidCas(cas)) {
      setLookup({ phase: 'idle', message: cas ? 'CAS 号校验位不正确；请修正或留空。' : '有效 CAS 会自动查询化学信息；自配物质可留空。', blocks: false })
      return
    }
    const controller = new AbortController()
    setLookup({ phase: 'loading', message: '正在通过 Backend 查询化合物信息…', blocks: true })
    const timer = window.setTimeout(() => void lookupCompoundByCas(cas, controller.signal).then((result) => {
      if (controller.signal.aborted) return
      if (result.status === 'ok' && result.compound) {
        const next = result.compound
        const previous = previousCandidate.current
        setIdentityForm((current) => ({
          ...current,
          nameEn: replaceAutoValue(current.nameEn, previous?.name, next.name),
          molecularFormula: replaceAutoValue(current.molecularFormula, previous?.molecularFormula, next.molecularFormula),
          smiles: replaceAutoValue(current.smiles, previous?.smiles, next.smiles),
          inchiKey: replaceAutoValue(current.inchiKey, previous?.inchiKey, next.inchiKey),
          molecularWeight: replaceAutoValue(current.molecularWeight, numberText(previous?.molecularWeight), numberText(next.molecularWeight)),
        }))
        previousCandidate.current = next
        setLookup({ phase: 'success', message: '已补全可用字段，请核对后保存。', blocks: false })
      } else {
        previousCandidate.current = undefined
        setLookup({ phase: 'warning', message: result.message || lookupFallback(result.status), blocks: result.status === 'registered' })
      }
    }).catch((error) => {
      if (controller.signal.aborted) return
      setLookup({ phase: 'warning', message: error instanceof Error ? `${error.message}；仍可手工填写。` : '查询失败，仍可手工填写。', blocks: false })
    }), 500)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [dialog, identityForm.cas, catalogEditTarget])

  function openCatalogDialog() {
    if (!connected || saving || pendingDispense) return
    setDetailSelection(null)
    setCatalogEditTarget(null); setIdentityForm(emptyIdentity); setCustomParameters([]); setCustomParametersTouched(false); setAdvancedOpen(false); setFormError(''); previousCandidate.current = undefined
    setLookup({ phase: 'idle', message: '有效 CAS 会自动查询化学信息；自配物质可留空。', blocks: false }); setDialog('catalog')
  }

  function openCatalogEditDialog(item: ReagentInfoRecord) {
    if (!connected || saving || pendingDispense) return
    setDetailSelection(null)
    setCatalogEditTarget(item)
    setIdentityForm({
      name: item.name, nameEn: item.nameEn || '', cas: item.cas || '', aliases: item.aliases.join('\n'),
      molecularFormula: item.molecularFormula || '', smiles: item.smiles || '', inchiKey: item.inchiKey || '',
      molecularWeight: numberText(item.molecularWeight) || '', density: numberText(item.densityGPerMl) || '',
      physicalState: item.physicalState, description: item.description || '',
    })
    const parameters = Array.isArray(item.metadata?.custom_parameters) ? item.metadata.custom_parameters : []
    setCustomParameters(parameters.flatMap((parameter, index) => {
      if (!parameter || typeof parameter !== 'object' || !('name' in parameter) || !('value' in parameter)) return []
      return [{ id: index + 1, name: String(parameter.name ?? ''), value: String(parameter.value ?? '') }]
    }))
    setCustomParametersTouched(false); setAdvancedOpen(Boolean(item.inchiKey || item.description || parameters.length)); setFormError(''); previousCandidate.current = undefined
    setLookup({ phase: 'idle', message: '当前目录项的 CAS，可直接修改其他信息。', blocks: false }); setDialog('catalog')
  }

  const updateCustomParameters: React.Dispatch<React.SetStateAction<CustomParameter[]>> = (next) => {
    setCustomParametersTouched(true)
    setCustomParameters(next)
  }

  async function saveCatalogItem() {
    if (!connected || saving) return
    const error = validateIdentity(identityForm, customParameters)
    if (error) { setFormError(error); return }
    if (lookup.blocks || lookup.phase === 'loading') { setFormError(lookup.message); return }
    setSaving(true); setFormError('')
    try {
      const metadata = { ...catalogEditTarget?.metadata }
      if (customParametersTouched || (!catalogEditTarget && customParameters.length)) {
        metadata.custom_parameters = customParameters.map(({ name, value }) => ({ name: name.trim(), value: value.trim() }))
      }
      const payload = {
        name: identityForm.name.trim(), nameEn: textOrUndefined(identityForm.nameEn) ?? null, cas: identityForm.cas.trim(),
        aliases: catalogEditTarget && identityForm.aliases === catalogEditTarget.aliases.join('\n')
          ? catalogEditTarget.aliases
          : [...new Set(identityForm.aliases.split(/\r?\n/).map((value) => value.trim()).filter(Boolean))],
        molecularFormula: textOrUndefined(identityForm.molecularFormula) ?? null, smiles: textOrUndefined(identityForm.smiles) ?? null, inchiKey: textOrUndefined(identityForm.inchiKey) ?? null,
        molecularWeight: numberOrUndefined(identityForm.molecularWeight) ?? null, densityGPerMl: numberOrUndefined(identityForm.density) ?? null,
        physicalState: identityForm.physicalState, description: textOrUndefined(identityForm.description) ?? null, metadata,
      }
      if (catalogEditTarget) await updateReagentInfo(catalogEditTarget.uuid, payload)
      else await createReagentInfo(payload)
      await Promise.all([infosQuery.refetch(), reagentsQuery.refetch()])
      setDialog(null); setCatalogEditTarget(null); onNotify(`试剂目录“${identityForm.name.trim()}”已${catalogEditTarget ? '更新' : '创建'}`)
    } catch (error) { setFormError(error instanceof Error ? error.message : '保存失败') } finally { setSaving(false) }
  }

  async function saveRegistration() {
    if (!connected || saving) return
    const quantity = Number(registerForm.quantity)
    if (!registerForm.materialUuid || !registerForm.reagentInfoUuid || !Number.isFinite(quantity) || quantity <= 0 || !registerForm.quantityUnit) return
    const container = containers.find((item) => item.uuid === registerForm.materialUuid)
    const context = registrationContext(registerForm, infosQuery.data?.find((item) => item.uuid === registerForm.reagentInfoUuid), container)
    const error = concentrationError(registerForm.concentrationValue, registerForm.concentrationUnit) || maximumError(quantity, registerForm.quantityUnit, container?.capacity, container?.ratedCapacity, registerForm.maximum, context)
    if (!container || error) { onNotify(error || '请选择可用的试剂容器'); return }
    setSaving(true)
    try {
      await createReagent({ materialUuid: registerForm.materialUuid, reagentInfoUuid: registerForm.reagentInfoUuid, quantity, quantityUnit: registerForm.quantityUnit, concentrationValue: registerForm.concentrationValue ? Number(registerForm.concentrationValue) : undefined, concentrationUnit: registerForm.concentrationValue ? registerForm.concentrationUnit : undefined, description: textOrUndefined(registerForm.description), containerCapacity: registerForm.maximum === undefined ? undefined : parseMaximum(registerForm.maximum, registerForm.quantityUnit), expectedMaterialRevision: materials.find((item) => item.uuid === registerForm.materialUuid)?.revision })
      await reagentsQuery.refetch(); setDialog(null); onNotify('试剂已录入容器库存')
      if (registerForm.maximum !== undefined) onRefresh?.()
    } catch (error) { onNotify(`录入失败：${error instanceof Error ? error.message : '未知错误'}`) } finally { setSaving(false) }
  }

  function openEditDialog(item: ReagentRecord) {
    if (!connected || saving || pendingDispense) return
    setDetailSelection(null)
    setEditTarget(item)
    setEditForm(editFields(item)); setFormError('')
    setDialog('edit')
  }

  async function saveEdit() {
    if (!connected || saving || !editTarget) return
    const quantity = Number(editForm.quantity)
    if (!Number.isFinite(quantity) || quantity < 0) return
    const currentMaximum = editTarget.maximumCapacity ?? stricterCapacity(editTarget.containerCapacity, editTarget.loadingLimits)
    const context = { ...editTarget, concentrationValue: numberOrUndefined(editForm.concentrationValue) }
    const error = concentrationError(editForm.concentrationValue, editForm.concentrationUnit) || maximumError(quantity, editTarget.quantityUnit || '', currentMaximum, editTarget.ratedCapacity, editForm.maximum, context, legacyEditQuantity(editTarget, editForm))
    if (error) { onNotify(error); return }
    setSaving(true); setFormError('')
    try {
      await updateReagent({
        uuid: editTarget.uuid, quantity, quantityUnit: editTarget.quantityUnit || 'mL', expectedRevision: editTarget.revision,
        concentrationValue: numberOrUndefined(editForm.concentrationValue) ?? null, concentrationUnit: editForm.concentrationValue.trim() ? textOrUndefined(editForm.concentrationUnit) ?? null : null,
        description: textOrUndefined(editForm.description), metaData: editTarget.metaData,
        containerCapacity: editForm.maximum === undefined ? undefined : parseMaximum(editForm.maximum, editTarget.quantityUnit || 'mL'),
        expectedMaterialRevision: editTarget.materialRevision ?? materials.find((item) => item.uuid === editTarget.materialUuid)?.revision,
      })
      await reagentsQuery.refetch()
      if (editForm.maximum !== undefined) onRefresh?.()
      setDialog(null); setEditTarget(null)
      onNotify(`已更新 ${editTarget.containerName || editTarget.name}：${quantity} ${editTarget.quantityUnit || ''}`)
    } catch (error) {
      const message = `更新失败：${error instanceof Error ? error.message : '未知错误'}`
      const refreshed = await reagentsQuery.refetch()
      const latest = refreshed.data?.find((item) => item.uuid === editTarget.uuid)
      if (!refreshed.isError && latest && (latest.revision !== editTarget.revision || latest.materialRevision !== editTarget.materialRevision)) {
        setEditTarget(latest); setEditForm(editFields(latest))
        setFormError('库存已变更，已载入最新余量和规格。原修改未再次提交，请核对后重新填写并保存。')
      } else if (!refreshed.isError && !latest) {
        setDialog(null); setEditTarget(null)
      } else {
        setFormError(`${message}${refreshed.isError ? '；最新库存读取失败，请刷新后重试。' : ''}`)
      }
      onNotify(message)
    } finally { setSaving(false) }
  }

  async function removeReagent(item: ReagentRecord) {
    if (!connected || saving || pendingDispense) return
    if (!window.confirm(`确认删除 ${item.containerName || item.name} 里的 ${item.name} 记录？\n\n容器会变回空容器，操作历史保留一条“移除”。`)) return
    setSaving(true)
    try {
      await deleteReagent(item.uuid)
      await reagentsQuery.refetch()
      onNotify(`已移除 ${item.containerName || item.name} 里的 ${item.name}`)
    } catch (error) {
      onNotify(`删除失败：${error instanceof Error ? error.message : '未知错误'}`)
    } finally { setSaving(false) }
  }

  function openDispenseDialog(item: ReagentRecord) {
    if (!connected || saving || pendingDispense) return
    setDetailSelection(null)
    setDispenseSource(item)
    setDispenseRows([{ id: 1, materialUuid: '', quantity: '' }])
    setFormError('')
    setDialog('dispense')
  }

  async function saveDispense() {
    if (!connected || saving || dispenseInFlight.current || !dispenseSource) return
    if (!pendingDispense) {
      const summary = summariseDispense(dispenseRows, dispenseSource.quantity ?? 0)
      if (!summary.ready) return
      for (const row of dispenseRows) {
        const container = containers.find((item) => item.uuid === row.materialUuid)
        const error = maximumError(Number(row.quantity), dispenseSource.quantityUnit || '', container?.capacity, container?.ratedCapacity, row.maximum, dispenseContext(dispenseSource, container))
        if (!container || error) { onNotify(error || '请选择可用的目标容器'); return }
      }
    }
    const frozen: PendingDispense = pendingDispense || {
      source: dispenseSource, rows: dispenseRows,
      request: {
        commandId: newCommandId(), sourceReagentUuid: dispenseSource.uuid,
        expectedRevision: dispenseSource.revision, quantityUnit: dispenseSource.quantityUnit || 'mL',
        targets: dispenseRows.map((row) => ({ materialUuid: row.materialUuid, quantity: Number(row.quantity), containerCapacity: row.maximum === undefined ? undefined : parseMaximum(row.maximum, dispenseSource.quantityUnit || 'mL'), expectedMaterialRevision: materials.find((item) => item.uuid === row.materialUuid)?.revision })),
      },
    }
    // 必须先保存恢复信息，再发出写请求；存储失败时不能留下无法核对的分装。
    try { window.sessionStorage.setItem(pendingDispenseKey, JSON.stringify(frozen)) }
    catch { setFormError('无法保存分装恢复信息，本次尚未提交。请检查浏览器存储后重试。'); return }
    setPendingDispense(frozen); dispenseInFlight.current = true
    setSaving(true); setFormError('')
    try {
      const result = await dispenseReagent(frozen.request)
      window.sessionStorage.removeItem(pendingDispenseKey); setPendingDispense(null)
      await reagentsQuery.refetch()
      if (frozen.rows.some((row) => row.maximum !== undefined)) onRefresh?.()
      setDialog(null); setDispenseSource(null)
      onNotify(`已分装到 ${result.targets.length} 个容器，源瓶剩余 ${result.source.quantity} ${result.source.quantityUnit}`)
    } catch (error) {
      if (error instanceof InventoryCommandRejectedError) {
        window.sessionStorage.removeItem(pendingDispenseKey); setPendingDispense(null)
        const refreshed = await reagentsQuery.refetch()
        const latest = refreshed.data?.find((item) => item.uuid === frozen.source.uuid)
        if (!refreshed.isError && latest) setDispenseSource(latest)
        else if (!refreshed.isError) { setDialog(null); setDispenseSource(null) }
        setFormError(`分装未执行：${error.message}。请核对库存并修改后重新提交。`)
      } else {
        setFormError(`分装结果尚未确认：${error instanceof Error ? error.message : '未知错误'}。原目标和数量已锁定，请核对原请求结果后继续。`)
      }
      onNotify(error instanceof InventoryCommandRejectedError ? `分装未执行：${error.message}` : '分装结果尚未确认，请在弹窗中核对原请求结果。')
    } finally { dispenseInFlight.current = false; setSaving(false) }
  }

  async function removeCatalogItem(item: ReagentInfoRecord) {
    if (!connected || saving) return
    if (!window.confirm(`确认删除试剂目录“${item.name}”？\n\n已被试剂库存引用的目录项会由后端拒绝删除。`)) return
    setSaving(true)
    try {
      await deleteReagentInfo(item.uuid)
      await infosQuery.refetch()
      onNotify(`已删除试剂目录“${item.name}”`)
    } catch (error) {
      onNotify(`删除失败：${error instanceof Error ? error.message : '未知错误'}`)
    } finally { setSaving(false) }
  }

  const loadingError = infosQuery.error || reagentsQuery.error
  const mutationsDisabled = !connected || saving || Boolean(pendingDispense)
  const dispenseContainers = pendingDispense ? [...containers, ...materials.filter((material) =>
    !containers.some((container) => container.uuid === material.uuid)
    && pendingDispense.request.targets.some((target) => target.materialUuid === material.uuid))] : containers
  return <div className="page reagents-page">
    <PageHeader eyebrow="REAGENT AUTHORITY" title="试剂" description="试剂目录定义化学品身份；试剂库存记录具体容器、数量与浓度。" actions={<><Button icon={<BookOpen size={15} />} disabled={mutationsDisabled} onClick={openCatalogDialog}>新增试剂目录</Button><Button tone="primary" icon={<PackagePlus size={15} />} disabled={mutationsDisabled || !infosQuery.data?.length || !containers.length} onClick={() => { setDetailSelection(null); setFormError(''); setDialog('register') }}>录入试剂</Button></>} />
    <div className="reagent-summary"><div><FlaskConical size={19} /><span>试剂库存<strong>{reagentsQuery.data?.length || 0}</strong></span></div><div><BookOpen size={19} /><span>试剂目录<strong>{infosQuery.data?.length || 0}</strong></span></div><div><PackagePlus size={19} /><span>可录入容器<strong>{containers.length}</strong></span></div></div>
    <Panel className="reagent-workspace">
      <PanelHeader title={view === 'inventory' ? '试剂库存' : '试剂目录'} description={view === 'inventory' ? '容器级数量、浓度与化学身份' : 'CAS、分子式与基础理化信息'} action={<div className="segmented"><button className={view === 'inventory' ? 'active' : ''} onClick={() => changeView('inventory')}>库存</button><button className={view === 'catalog' ? 'active' : ''} onClick={() => changeView('catalog')}>目录</button></div>} />
      <label className="reagent-search"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={view === 'inventory' ? '搜索试剂、CAS、容器或条码' : '搜索名称、别名、CAS 或分子式'} /></label>
      {loadingError ? <div className="connection-alert" role="alert"><div><strong>试剂数据不可用</strong><span>{loadingError instanceof Error ? loadingError.message : '读取失败'}</span></div></div> : null}
      {view === 'inventory' ? <InventoryTable items={inventory} materials={materials} hasCatalog={Boolean(infosQuery.data?.length)} onHistory={(item) => { setDetailSelection(null); setHistoryReagent(item) }} onDetails={(item) => openDetails('inventory', item.uuid)} onDispense={openDispenseDialog}  onEdit={openEditDialog} onDelete={removeReagent} busy={mutationsDisabled} /> :<CatalogTable items={infos} disabled={mutationsDisabled} onDetails={(item) => openDetails('catalog', item.uuid)} onEdit={openCatalogEditDialog} onDelete={(item) => void removeCatalogItem(item)} />}
    </Panel>
    {catalogDetails ? <CatalogDetails item={catalogDetails} onClose={() => setDetailSelection(null)} /> : null}
    {inventoryDetails ? <InventoryDetails item={inventoryDetails} material={materials.find((material) => material.uuid === inventoryDetails.materialUuid)} onClose={() => setDetailSelection(null)} /> : null}
    {historyReagent ? <ReagentHistoryDrawer reagent={historyReagent} items={historyQuery.data || []} loading={historyQuery.isLoading || historyQuery.isFetching} error={historyQuery.error} onClose={() => setHistoryReagent(null)} /> : null}
    {dialog ? <div className="dialog-backdrop" role="presentation"><div className={`material-write-dialog reagent-dialog ${dialog === 'catalog' ? 'reagent-dialog-wide' : ''} ${dialog === 'dispense' ? 'reagent-dialog-dispense' : ''}`} role="dialog" aria-modal="true"><header><div><span>REAGENT COMMAND</span><h2>{dialog === 'catalog' ? catalogEditTarget ? '编辑试剂目录' : '新增试剂目录' : dialog === 'dispense' ? '分装' : dialog === 'edit' ? '编辑试剂' : '录入试剂'}</h2>{dialog === 'dispense' && dispenseSource ? <p>把源瓶里的试剂分到若干空容器；同一化学身份与浓度，数量守恒，一次提交。</p> : null}{dialog === 'catalog' ? <p>输入 CAS 可自动补全化学信息；无 CAS 的自配物质可直接填写名称。</p> : null}</div><button aria-label="关闭" disabled={Boolean(pendingDispense)} onClick={() => setDialog(null)}>×</button></header>
      {dialog !== 'catalog' && (formError || pendingDispense) ? <div className="reagent-form-error" role="alert">{formError || '上次分装结果尚未确认，原目标和数量已锁定。请核对原请求结果后继续。'}</div> : null}
      {dialog === 'edit' && editTarget ? <EditReagentForm target={editTarget} form={editForm} setForm={setEditForm} saving={saving || !connected} onSave={saveEdit} /> : dialog === 'catalog' ? <CatalogForm form={identityForm} setForm={setIdentityForm} lookup={lookup} error={formError} customParameters={customParameters} setCustomParameters={updateCustomParameters} advancedOpen={advancedOpen} setAdvancedOpen={setAdvancedOpen} saving={saving} connected={connected} editing={Boolean(catalogEditTarget)} onSave={() => void saveCatalogItem()} /> : dialog === 'dispense' && dispenseSource ? <DispenseForm source={dispenseSource} rows={dispenseRows} setRows={setDispenseRows} containers={dispenseContainers} templates={templatesQuery.data || []} filterTags={containerFilterTags} preferredTag={preferredContainerTag} saving={saving} pending={Boolean(pendingDispense)} connected={connected} onSave={() => void saveDispense()} /> : <RegisterForm form={registerForm} setForm={setRegisterForm} infos={infosQuery.data || []} containers={containers} templates={templatesQuery.data || []} filterTags={containerFilterTags} saving={saving} onSave={() => void saveRegistration()} />}
    </div></div> : null}
  </div>
}

function CatalogForm({ form, setForm, lookup, error, customParameters, setCustomParameters, advancedOpen, setAdvancedOpen, saving, connected, editing, onSave }: { form: IdentityForm; setForm: React.Dispatch<React.SetStateAction<IdentityForm>>; lookup: { phase: string; message: string; blocks: boolean }; error: string; customParameters: CustomParameter[]; setCustomParameters: React.Dispatch<React.SetStateAction<CustomParameter[]>>; advancedOpen: boolean; setAdvancedOpen: (value: boolean) => void; saving: boolean; connected: boolean; editing: boolean; onSave: () => void }) {
  const field = (key: keyof IdentityForm, value: string) => setForm((current) => ({ ...current, [key]: value }))
  return <div className="dialog-content reagent-catalog-form">
    {error ? <div className="reagent-form-error" role="alert">{error}</div> : null}
    <fieldset><legend>试剂信息</legend><div className="reagent-field-grid">
      <label className="form-field"><span>CAS 号</span><input autoFocus value={form.cas} onChange={(e) => field('cas', e.target.value)} placeholder="例如 64-17-5" /><small className="reagent-lookup" data-phase={lookup.phase}>{lookup.message}</small></label>
      <label className="form-field"><span>试剂名称 *</span><input value={form.name} onChange={(e) => field('name', e.target.value)} /></label>
      <label className="form-field"><span>英文名称</span><input value={form.nameEn} onChange={(e) => field('nameEn', e.target.value)} /></label>
      <label className="form-field"><span>别名</span><textarea aria-label="别名" rows={3} value={form.aliases} onChange={(e) => field('aliases', e.target.value)} /><small>每行一个别名</small></label>
    </div></fieldset>
    <fieldset><legend>化学信息 {lookup.phase === 'success' ? <small>已自动补全</small> : null}</legend><div className="reagent-field-grid">
      <label className="form-field"><span>分子式</span><input value={form.molecularFormula} onChange={(e) => field('molecularFormula', e.target.value)} /></label>
      <label className="form-field"><span>分子量（g/mol）</span><input type="number" min="0" step="any" value={form.molecularWeight} onChange={(e) => field('molecularWeight', e.target.value)} /></label>
      <label className="form-field wide"><span>SMILES</span><input className="mono-input" value={form.smiles} onChange={(e) => field('smiles', e.target.value)} /></label>
      <ChemicalStructurePreview smiles={form.smiles} />
    </div></fieldset>
    <fieldset><legend>物理属性</legend><div className="reagent-field-grid">
      <label className="form-field"><span>常温物态</span><select value={form.physicalState} onChange={(e) => field('physicalState', e.target.value)}>{Object.entries(physicalStateLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="form-field"><span>参考密度（g/mL）</span><input type="number" min="0" step="any" value={form.density} onChange={(e) => field('density', e.target.value)} /></label>
    </div></fieldset>
    <details open={advancedOpen} onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}><summary>更多信息</summary><div className="reagent-field-grid advanced-fields">
      <label className="form-field wide"><span>InChIKey</span><input className="mono-input" value={form.inchiKey} onChange={(e) => field('inchiKey', e.target.value)} /></label>
      <label className="form-field wide"><span>说明</span><textarea rows={3} value={form.description} onChange={(e) => field('description', e.target.value)} /></label>
      <div className="custom-parameters wide"><header><strong>自定义参数</strong><button type="button" onClick={() => setCustomParameters((items) => [...items, { id: Date.now(), name: '', value: '' }])}><Plus size={13} />添加参数</button></header>{customParameters.map((parameter) => <div key={parameter.id}><input aria-label="参数名称" placeholder="参数名称" value={parameter.name} onChange={(e) => setCustomParameters((items) => items.map((item) => item.id === parameter.id ? { ...item, name: e.target.value } : item))} /><input aria-label="参数值" placeholder="参数值" value={parameter.value} onChange={(e) => setCustomParameters((items) => items.map((item) => item.id === parameter.id ? { ...item, value: e.target.value } : item))} /><button aria-label="删除参数" onClick={() => setCustomParameters((items) => items.filter((item) => item.id !== parameter.id))}><Trash2 size={14} /></button></div>)}</div>
    </div></details>
    <Button tone="primary" icon={editing ? <Pencil size={15} /> : <Plus size={15} />} disabled={!connected || saving || lookup.phase === 'loading' || lookup.blocks} onClick={onSave}>{saving ? editing ? '正在保存…' : '正在创建…' : editing ? '保存修改' : '创建'}</Button>
  </div>
}

export function RegisterForm({ form, setForm, infos, containers, templates, filterTags, saving, onSave }: { form: RegisterFields; setForm: React.Dispatch<React.SetStateAction<typeof form>>; infos: ReagentInfoRecord[]; containers: MaterialRecord[]; templates: ResourceTemplateRecord[]; filterTags: string[]; saving: boolean; onSave: () => void }) {
  const [selectedTag, setSelectedTag] = useState('')
  const templateTags = useMemo(() => new Map(templates.map((template) => [template.uuid, new Set(template.tags || [])])), [templates])
  const taggedContainers = filterContainersByTag(containers, templateTags, selectedTag)
  const changeTag = (tag: string) => {
    setSelectedTag(tag)
    if (form.materialUuid && !filterContainersByTag(containers, templateTags, tag).some((item) => item.uuid === form.materialUuid)) setForm({ ...form, materialUuid: '', maximum: undefined })
  }
  const container = containers.find((item) => item.uuid === form.materialUuid)
  const info = infos.find((item) => item.uuid === form.reagentInfoUuid)
  const context = registrationContext(form, info, container)
  const units = quantityUnits(context)
  const error = concentrationError(form.concentrationValue, form.concentrationUnit) || maximumError(Number(form.quantity), form.quantityUnit, container?.capacity, container?.ratedCapacity, form.maximum, context)
  const changeUnit = (quantityUnit: string) => {
    if (quantityUnitError(quantityUnit, context)) return
    setForm({ ...form, quantityUnit, quantity: convertMaximum(form.quantity, form.quantityUnit, quantityUnit, context) ?? '', maximum: convertMaximum(form.maximum, form.quantityUnit, quantityUnit, context) })
  }
  return <div className="dialog-content reagent-form">
    <label className="form-field wide"><span>试剂目录 *</span><select value={form.reagentInfoUuid} disabled={saving} onChange={(e) => {
      const next = infos.find((item) => item.uuid === e.target.value)
      setForm({ ...form, reagentInfoUuid: e.target.value, quantityUnit: defaultQuantityUnit(next), quantity: '', maximum: undefined })
    }}><option value="">选择试剂目录项</option>{infos.map((item) => <option key={item.uuid} value={item.uuid}>{item.name} · {item.cas || '无 CAS'}</option>)}</select></label>
    <div className="reagent-container-picker wide">
      <ContainerTagFilter tags={filterTags} selectedTag={selectedTag} containers={containers} templateTags={templateTags} onChange={changeTag} />
      <div className="form-field"><label htmlFor="register-reagent-container">试剂容器 *</label><SearchableContainerSelect id="register-reagent-container" label="试剂容器" value={form.materialUuid} options={containerOptions(taggedContainers)} placeholder={taggedContainers.length ? '选择未登记试剂的容器' : '该类型暂无可用容器'} disabled={saving} onChange={(materialUuid) => setForm({ ...form, materialUuid, maximum: undefined })} /></div>
    </div>
    {container ? <ReagentCapacityFields className="wide" maximum={form.maximum} unit={form.quantityUnit} current={container.capacity} rated={container.ratedCapacity} context={context} disabled={saving} onChange={(maximum) => setForm({ ...form, maximum })} error={info ? error : undefined} /> : null}
    {info && !container && quantityUnitError(form.quantityUnit, context) ? <small className="form-error wide" role="alert">{quantityUnitError(form.quantityUnit, context)}</small> : null}
    <label className="form-field"><span>数量 *</span><input type="number" min="0" step="any" value={form.quantity} disabled={saving} onChange={(e) => setForm({ ...form, quantity: e.target.value })} /></label>
    <label className="form-field"><span>单位 *</span><select value={form.quantityUnit} disabled={saving || !units.length} onChange={(e) => changeUnit(e.target.value)}>{!units.includes(form.quantityUnit) ? <option value={form.quantityUnit} disabled>{form.quantityUnit}（当前不可用）</option> : null}{units.map((unit) => <option key={unit}>{unit}</option>)}</select></label>
    {info?.physicalState === 'liquid' ? <small className="quantity-unit-hint wide">液体默认按体积计量；密度有效且未填写浓度时，可换算为质量。</small> : null}
    {info?.physicalState === 'solid' ? <small className="quantity-unit-hint wide">固体按质量计量，实际数量不得超过最大装料量。</small> : null}
    <label className="form-field"><span>浓度</span><input type="number" min="0" step="any" value={form.concentrationValue} onChange={(e) => setForm({ ...form, concentrationValue: e.target.value })} /></label>
    <label className="form-field"><span>浓度单位</span><select value={form.concentrationUnit} onChange={(e) => setForm({ ...form, concentrationUnit: e.target.value })}>{['%', 'mol/L', 'mmol/L', 'mg/mL'].map((unit) => <option key={unit}>{unit}</option>)}</select></label>
    <label className="form-field wide"><span>说明</span><input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
    <Button tone="primary" icon={<PackagePlus size={15} />} disabled={saving || Boolean(error) || !container || !info || !Number.isFinite(Number(form.quantity)) || !(Number(form.quantity) > 0)} onClick={onSave}>确认录入</Button>
  </div>
}

export function EditReagentForm({ target, form, setForm, saving, onSave }: { target: ReagentRecord; form: EditForm; setForm: (form: EditForm) => void; saving: boolean; onSave: () => void }) {
  const reserved = target.activeWorkflowReservedQuantity ?? 0
  const quantity = Number(form.quantity)
  const quantityValid = form.quantity.trim() !== '' && Number.isFinite(quantity) && quantity >= 0
  const belowReserved = quantityValid && quantity < reserved
  const currentMaximum = target.maximumCapacity ?? stricterCapacity(target.containerCapacity, target.loadingLimits)
  const context = { ...target, concentrationValue: numberOrUndefined(form.concentrationValue) }
  const strictError = maximumError(quantity, target.quantityUnit || '', currentMaximum, target.ratedCapacity, form.maximum, context)
  const limitError = concentrationError(form.concentrationValue, form.concentrationUnit) || maximumError(quantity, target.quantityUnit || '', currentMaximum, target.ratedCapacity, form.maximum, context, legacyEditQuantity(target, form))
  return <div className="reagent-edit-form">
    <div className="reagent-edit-target"><strong>{target.name}</strong><small>{target.containerName || target.materialUuid} · 当前 {target.quantity ?? '—'} {target.quantityUnit || ''}{reserved > 0 ? ` · 预留中 ${reserved} ${target.quantityUnit || ''}` : ''}</small></div>
    <ReagentCapacityFields maximum={form.maximum} unit={target.quantityUnit || ''} current={currentMaximum} rated={target.ratedCapacity} context={context} disabled={saving} onChange={(maximum) => setForm({ ...form, maximum })} error={limitError} />
    {strictError && !limitError ? <small className="quantity-unit-hint">现有记录可按原单位减少数量；加量或修改上限前，需要补齐物态和容量规格。</small> : null}
    <div className="form-grid">
      <label className="form-field"><span>数量（{target.quantityUnit || '单位不变'}）*</span><input aria-label="数量" type="number" min={0} step="any" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} /></label>
      <label className="form-field"><span>浓度值</span><input aria-label="浓度值" type="number" step="any" value={form.concentrationValue} onChange={(e) => setForm({ ...form, concentrationValue: e.target.value })} /></label>
      <label className="form-field"><span>浓度单位</span><input aria-label="浓度单位" value={form.concentrationUnit} onChange={(e) => setForm({ ...form, concentrationUnit: e.target.value })} placeholder="如 %、mol/L" /></label>
      <label className="form-field wide"><span>说明</span><input aria-label="说明" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="为什么改：盘点、称重复核、录错更正…" /></label>
    </div>
    {belowReserved ? <small className="form-error">数量不能低于任务预留量 {reserved} {target.quantityUnit || ''}。</small> : null}
    <small className="reagent-edit-hint">单位不可改；数量和最大装料量的变化会记入操作历史（增加记“录入 / 补充”，减少记“调整”）。分装血缘保持不变。</small>
    <Button tone="primary" icon={<Pencil size={15} />} disabled={saving || !quantityValid || belowReserved || Boolean(limitError)} onClick={onSave}>保存修改</Button>
  </div>
}

export function DispenseForm({ source, rows, setRows, containers, templates, filterTags, preferredTag, saving, pending = false, connected = true, onSave }: { source: ReagentRecord; rows: DispenseRow[]; setRows: React.Dispatch<React.SetStateAction<DispenseRow[]>>; containers: MaterialRecord[]; templates: ResourceTemplateRecord[]; filterTags: string[]; preferredTag: string; saving: boolean; pending?: boolean; connected?: boolean; onSave: () => void }) {
  const [selectedTag, setSelectedTag] = useState(preferredTag)
  const locked = saving || pending || !connected
  const available = source.quantity ?? 0
  const unit = source.quantityUnit || ''
  const summary = summariseDispense(rows, available)
  const capacityErrors = rows.map((row) => {
    if (pending) return ''
    const container = containers.find((item) => item.uuid === row.materialUuid)
    return maximumError(Number(row.quantity), unit, container?.capacity, container?.ratedCapacity, row.maximum, dispenseContext(source, container))
  })
  const chosen = new Set(rows.map((row) => row.materialUuid).filter(Boolean))
  const templateTags = useMemo(() => new Map(templates.map((template) => [template.uuid, new Set(template.tags || [])])), [templates])
  const taggedContainers = filterContainersByTag(containers, templateTags, selectedTag)
  const unchosenContainerCount = taggedContainers.filter((item) => !chosen.has(item.uuid)).length
  const hasAnotherContainer = unchosenContainerCount > rows.filter((row) => !row.materialUuid).length
  const addRow = () => setRows((current) => [...current, { id: (current.at(-1)?.id || 0) + 1, materialUuid: '', quantity: '' }])
  const update = (id: number, patch: Partial<DispenseRow>) => setRows((current) => current.map((row) => (row.id === id ? { ...row, ...patch } : row)))
  const remove = (id: number) => setRows((current) => (current.length > 1 ? current.filter((row) => row.id !== id) : current))
  return <form className="dialog-content dispense-form" onSubmit={(event) => { event.preventDefault(); if (connected && !saving && (pending || summary.ready && !capacityErrors.some(Boolean))) onSave() }}>
    <section className="dispense-source" aria-label="分装源瓶">
      <span className="dispense-source-icon"><FlaskConical size={20} /></span>
      <div><small>源瓶</small><strong>{source.name}</strong><span>{source.containerName || source.materialUuid}</span></div>
      <div className="dispense-source-balance"><small>当前可分装</small><strong>{available}<em>{unit}</em></strong></div>
    </section>
    <section className="dispense-targets">
      <header>
        <div><strong>目标容器</strong><small>选择空容器并填写本次转移数量</small></div>
        <Button type="button" className="dispense-add" icon={<Plus size={15} />} onClick={addRow} disabled={locked || !hasAnotherContainer}>添加容器</Button>
      </header>
      <ContainerTagFilter tags={filterTags} selectedTag={selectedTag} containers={containers} templateTags={templateTags} onChange={setSelectedTag} disabled={locked} />
      <div className="dispense-target-list">
        {rows.map((row, index) => {
          const container = containers.find((item) => item.uuid === row.materialUuid)
          const duplicate = row.materialUuid !== '' && rows.some((other) => other.id !== row.id && other.materialUuid === row.materialUuid)
          const options = containers.filter((item) => item.uuid === row.materialUuid || (taggedContainers.includes(item) && !chosen.has(item.uuid)))
          return <article key={row.id} className={`dispense-row${duplicate ? ' dispense-row-invalid' : ''}`}>
            <span className="dispense-row-index">{String(index + 1).padStart(2, '0')}</span>
            <div className="form-field dispense-container-field"><label htmlFor={`dispense-container-${row.id}`}>目标容器 *</label><SearchableContainerSelect id={`dispense-container-${row.id}`} label={`目标容器 ${index + 1}`} value={row.materialUuid} options={containerOptions(options)} placeholder={taggedContainers.length ? '选择空容器' : '该类型暂无空容器'} disabled={locked} onChange={(materialUuid) => update(row.id, { materialUuid, maximum: undefined })} /></div>
            {container ? <div className="dispense-row-capacity"><ReagentCapacityFields label={`目标 ${index + 1} 最大装料量`} maximum={row.maximum} unit={unit} current={container.capacity} rated={container.ratedCapacity} context={source} disabled={locked} onChange={(maximum) => update(row.id, { maximum })} error={capacityErrors[index]} /></div> : null}
            <label className="form-field dispense-quantity-field"><span>分装量 *</span><div><input aria-label={`分装量 ${index + 1}`} type="number" min="0" step="any" value={row.quantity} disabled={locked} onChange={(e) => update(row.id, { quantity: e.target.value })} /><em>{unit}</em></div></label>
            <button type="button" className="dispense-remove" aria-label={`移除目标 ${index + 1}`} title="移除目标容器" disabled={locked || rows.length <= 1} onClick={() => remove(row.id)}><Trash2 size={16} /></button>
            {duplicate ? <small className="form-error">该容器已被选择，请更换一个空容器。</small> : null}
          </article>
        })}
      </div>
    </section>
    <footer className={`dispense-summary${summary.remaining < -1e-9 ? ' dispense-summary-over' : ''}`} aria-live="polite">
      <div className="dispense-summary-flow">
        <span><small>本次分装</small><strong>{summary.total} <em>{unit}</em></strong></span>
        <ArrowRight size={18} aria-hidden="true" />
        <span><small>源瓶剩余</small><strong>{summary.remaining} <em>{unit}</em></strong></span>
      </div>
      {summary.remaining < -1e-9 ? <small className="form-error">分装合计超过源瓶现有余量 {available} {unit}</small> : null}
      <Button type="submit" tone="primary" icon={<PackagePlus size={16} />} disabled={!connected || saving || !pending && (!summary.ready || capacityErrors.some(Boolean))}>{saving ? '正在核对分装…' : pending ? '核对原分装结果' : '确认分装'}</Button>
    </footer>
  </form>
}

/** 一瓶试剂在分组列表里的位置：``depth`` 为 0 是源瓶或独立瓶，大于 0 表示挂在上一级分装源瓶之下。 */
export type InventoryBottle = { item: ReagentRecord; depth: number; source?: ReagentRecord; sourceLocation?: string; orphanSource: boolean; location?: string }
export type InventoryGroup = { key: string; name: string; cas?: string; molecularFormula?: string; bottles: InventoryBottle[]; totals: Array<{ unit: string; available: number; reserved: number }>; emptyCount: number }

function inventoryGroupKey(item: ReagentRecord) { return item.reagentInfoUuid || `${item.name}|${item.cas || ''}` }
function formatQuantity(value: number) { return Number(value.toFixed(3)).toString() }
/** 图引导生成的物料把 UNILAB-GRAPH-… 写进条码字段，那是系统身份不是实物条码，列表不展示。 */
export function displayBarcode(barcode?: string) { return barcode && !barcode.startsWith('UNILAB-GRAPH-') ? barcode : undefined }
/** 库位标签形如“试剂瓶堆栈 / R3C2”，血缘标签只需要最后一段代号。 */
export function shortLocation(location?: string) { const last = location?.split('/').map((part) => part.trim()).filter(Boolean).pop(); return last || undefined }

/**
 * 把平铺的试剂记录整理成"按化学身份分组、组内按分装血缘成树"的展示结构。
 * 源瓶（有分装子瓶的）排在组内最前，子瓶紧随其源瓶并缩进；源瓶已不在库的子瓶按独立瓶处理并标记。
 * 汇总按单位分别相加，避免 mL 与 g 混算。
 */
export function groupInventory(items: ReagentRecord[], materials: MaterialRecord[]): InventoryGroup[] {
  const byUuid = new Map(items.map((item) => [item.uuid, item]))
  const locationOf = new Map(materials.map((material) => [material.uuid, material.currentLocation?.label]))
  const groups = new Map<string, InventoryGroup>()
  const members = new Map<string, ReagentRecord[]>()
  for (const item of items) {
    const key = inventoryGroupKey(item)
    if (!groups.has(key)) groups.set(key, { key, name: item.name, cas: item.cas, molecularFormula: item.molecularFormula, bottles: [], totals: [], emptyCount: 0 })
    members.set(key, [...(members.get(key) || []), item])
  }
  const byContainer = (left: ReagentRecord, right: ReagentRecord) => (left.containerName || '').localeCompare(right.containerName || '', 'zh-CN')
  for (const group of groups.values()) {
    const own = members.get(group.key) || []
    const children = new Map<string, ReagentRecord[]>()
    const roots: ReagentRecord[] = []
    for (const item of own) {
      const source = item.sourceReagentUuid ? byUuid.get(item.sourceReagentUuid) : undefined
      if (source && inventoryGroupKey(source) === group.key && source.uuid !== item.uuid) children.set(source.uuid, [...(children.get(source.uuid) || []), item])
      else roots.push(item)
    }
    roots.sort((left, right) => Number(children.has(right.uuid)) - Number(children.has(left.uuid)) || byContainer(left, right))
    const visited = new Set<string>()
    const walk = (item: ReagentRecord, depth: number) => {
      if (visited.has(item.uuid)) return
      visited.add(item.uuid)
      const source = item.sourceReagentUuid ? byUuid.get(item.sourceReagentUuid) : undefined
      group.bottles.push({ item, depth, source, sourceLocation: source ? locationOf.get(source.materialUuid) : undefined, orphanSource: Boolean(item.sourceReagentUuid) && !source, location: locationOf.get(item.materialUuid) })
      for (const child of [...(children.get(item.uuid) || [])].sort(byContainer)) walk(child, depth + 1)
    }
    roots.forEach((root) => walk(root, 0))
    // 数据成环时 walk 不会重复进入；这里兜底把没走到的瓶按独立瓶补上，保证一瓶不漏。
    own.filter((item) => !visited.has(item.uuid)).sort(byContainer).forEach((item) => walk(item, 0))
    const totals = new Map<string, { available: number; reserved: number }>()
    for (const item of own) {
      const unit = item.quantityUnit || ''
      const total = totals.get(unit) || { available: 0, reserved: 0 }
      total.available += item.quantity ?? 0
      total.reserved += item.activeWorkflowReservedQuantity ?? 0
      totals.set(unit, total)
      if (!((item.quantity ?? 0) > 0)) group.emptyCount += 1
    }
    group.totals = [...totals].map(([unit, total]) => ({ unit, ...total }))
  }
  return [...groups.values()].sort((left, right) => left.name.localeCompare(right.name, 'zh-CN'))
}

function InventoryTable({ items, materials, hasCatalog, onHistory, onDetails, onDispense, onEdit, onDelete, busy }: { items: Awaited<ReturnType<typeof loadReagents>>; materials: MaterialRecord[]; hasCatalog: boolean; onHistory: (item: ReagentRecord) => void; onDetails: (item: ReagentRecord) => void; onDispense: (item: ReagentRecord) => void; onEdit: (item: ReagentRecord) => void; onDelete: (item: ReagentRecord) => void; busy?: boolean }) {
  const groups = groupInventory(items, materials)
  return <div className="reagent-table reagent-inventory-table">
    <div className="reagent-inventory-grid">
    <header className="inventory-columns"><span>容器 · 来源</span><span>当前余量</span><span>浓度 · 物性</span><span>更新时间</span><span>操作</span></header>
    {groups.map((group) => {
      const reserved = group.totals.filter((total) => total.reserved > 0).map((total) => `${formatQuantity(total.reserved)} ${total.unit}`).join(' + ')
      return <section className="reagent-group" key={group.key} aria-label={`${group.name} 库存`}>
        <div className="reagent-group-header">
          <span className="reagent-group-icon"><FlaskConical size={15} /></span>
          <div><strong>{group.name}</strong><small>{group.cas || '无 CAS'} · {group.molecularFormula || '无分子式'}</small></div>
          <div className="reagent-group-totals">
            <strong>{group.totals.map((total) => `${formatQuantity(total.available)} ${total.unit}`).join(' + ') || '—'}</strong>
            <small>{group.bottles.length} 瓶{group.emptyCount ? ` · ${group.emptyCount} 瓶已空` : ''}{reserved ? ` · 预留中 ${reserved}` : ''}</small>
          </div>
        </div>
        {group.bottles.map(({ item, depth, source, sourceLocation, orphanSource, location }) => {
          const barcode = displayBarcode(item.containerBarcode)
          const available = item.quantity ?? 0
          const reservedHere = item.activeWorkflowReservedQuantity ?? 0
          const containerName = item.containerName || materials.find((material) => material.uuid === item.materialUuid)?.name || '未知容器'
          return <article key={item.uuid} className={depth ? 'reagent-bottle reagent-bottle-child' : 'reagent-bottle'} data-depth={depth}>
            <span className="inventory-container-cell">
              <strong className="inventory-container-name">{depth ? <i className="reagent-branch" title={`分装层级 ${depth}`} aria-label={`第 ${depth} 级分装`}>↳{depth > 1 ? depth : ''}</i> : null}{containerName}</strong>
              <small>{location || barcode || '库位未知'}{location && barcode ? ` · ${barcode}` : ''}</small>
              {source ? <small className="reagent-lineage-chip" title={`分装自 ${source.containerName || '源瓶'}${item.dispenseCommandId ? `（命令 ${item.dispenseCommandId}）` : ''}`}>分装自 {shortLocation(sourceLocation) || source.containerName || '源瓶'}</small> : orphanSource ? <small className="reagent-lineage-chip muted">分装自已不在库的源瓶</small> : null}
              {item.description ? <small className="reagent-description" title={item.description}>{item.description}</small> : null}
              <button className="reagent-detail-link" aria-label={`查看试剂详情 ${item.name} ${item.uuid}`} onClick={() => onDetails(item)}>查看详情</button>
            </span>
            <span>
              <strong>{item.quantity ?? '—'} {item.quantityUnit || ''}</strong><small>上限：{capacityText(item.maximumCapacity ?? stricterCapacity(item.containerCapacity, item.loadingLimits), item.quantityUnit, item)}</small>
              <small className={reservedHere > 0 ? 'reagent-reserved' : undefined}>{available > 0 ? (reservedHere > 0 ? `预留中 ${formatQuantity(reservedHere)} ${item.quantityUnit || ''}` : '可用') : '已空'}</small>
            </span>
            <span>{item.concentrationValue == null ? '—' : `${item.concentrationValue} ${item.concentrationUnit || ''}`}<small>{physicalStateLabels[item.physicalState as ReagentInfoRecord['physicalState']] || '未知'}</small>{item.densityGPerMl != null ? <small>{item.densityGPerMl} g/mL</small> : null}</span>
            <span className="reagent-updated">{formatHistoryTime(item.updatedAt)}</span>
            <span className="reagent-row-actions history-action">
              <button aria-label={`分装 ${item.name} ${item.uuid}`} title={available > 0 ? '分装到其他容器' : '源瓶已空，无法分装'} disabled={busy || !(available > 0)} onClick={() => onDispense(item)}><PackagePlus size={14} /></button>
              <button aria-label={`查看操作历史 ${item.name} ${item.uuid}`} title="查看操作历史" onClick={() => onHistory(item)}><History size={14} /></button>
              <button aria-label={`编辑 ${item.name} ${item.uuid}`} title="修正数量、浓度或说明" disabled={busy} onClick={() => onEdit(item)}><Pencil size={14} /></button>
              <button className="danger" aria-label={`删除 ${item.name} ${item.uuid}`} title={reservedHere > 0 ? `有任务预留 ${formatQuantity(reservedHere)} ${item.quantityUnit || ''}，不能删除` : '移除这条试剂记录，容器变回空容器'} disabled={busy || reservedHere > 0} onClick={() => onDelete(item)}><Trash2 size={14} /></button>
            </span>
          </article>
        })}
      </section>
    })}
    </div>
    {!items.length ? <EmptyState title="暂无试剂库存" description={hasCatalog ? '点击“录入试剂”，把目录项登记到具体容器。' : '请先新增试剂目录，再录入容器库存。'} /> : null}
  </div>
}

function CatalogTable({ items, disabled, onDetails, onEdit, onDelete }: { items: ReagentInfoRecord[]; disabled: boolean; onDetails: (item: ReagentInfoRecord) => void; onEdit: (item: ReagentInfoRecord) => void; onDelete: (item: ReagentInfoRecord) => void }) {
  type Column = { key: string; label: string; render: (item: ReagentInfoRecord) => ReactNode }
  const parameters = new Map(items.map((item) => [item.uuid,
    Array.isArray(item.metadata?.custom_parameters) ? item.metadata.custom_parameters.flatMap((parameter) => {
        if (!parameter || typeof parameter !== 'object' || !('name' in parameter) || !('value' in parameter)) return []
        if (!String(parameter.name ?? '').trim() || parameter.value == null || typeof parameter.value === 'object' || !String(parameter.value).trim()) return []
        const value = typeof parameter.value === 'boolean' ? (parameter.value ? '是' : '否') : String(parameter.value)
        return [{ name: String(parameter.name), value: `${value}${typeof parameter.unit === 'string' && parameter.unit.trim() ? ` ${parameter.unit}` : ''}` }]
      }) : [],
  ]))
  const columns: Column[] = [{ key: 'identity', label: '试剂目录', render: (item) => {
    const aliases = item.aliases.filter((alias) => alias.trim())
    return <div className="catalog-record-identity">
      <button className="reagent-name-link" onClick={() => onDetails(item)}><strong>{item.name}</strong></button>
      {item.nameEn?.trim() ? <span className="catalog-english-name">{item.nameEn}</span> : null}
      {aliases.length ? <small className="reagent-aliases">别名：{aliases.join('、')}</small> : null}
    </div>
  } }]
  // 每列是否出现由整份当前列表决定；行内缺值留空，不能改变后续字段的列位。
  const addTextColumn = (key: string, label: string, read: (item: ReagentInfoRecord) => string | undefined, monospace = false) => {
    if (!items.some((item) => read(item)?.trim())) return
    columns.push({ key, label, render: (item) => {
      const value = read(item)
      return value?.trim() ? monospace ? <code>{value}</code> : value : null
    } })
  }
  addTextColumn('cas', 'CAS', (item) => item.cas)
  addTextColumn('formula', '分子式', (item) => item.molecularFormula)
  addTextColumn('weight', '分子量', (item) => item.molecularWeight == null ? undefined : `${item.molecularWeight} g/mol`)
  addTextColumn('state', '物态', (item) => item.physicalState === 'unknown' ? undefined : physicalStateLabels[item.physicalState])
  addTextColumn('density', '密度', (item) => item.densityGPerMl == null ? undefined : `${item.densityGPerMl} g/mL`)
  if (items.some((item) => item.smiles?.trim())) columns.push({ key: 'structure', label: '2D 结构', render: (item) => item.smiles?.trim() ? <ChemicalStructurePreview smiles={item.smiles} compact label={`${item.name} 2D 分子结构`} /> : null })
  addTextColumn('smiles', 'SMILES', (item) => item.smiles, true)
  addTextColumn('description', '说明', (item) => item.description)
  const builtInLabels = new Set(['试剂目录', 'CAS', '分子式', '分子量', '物态', '密度', '2D 结构', 'SMILES', 'InChIKey', '说明', '更新时间', '操作'])
  const parameterNames = [...new Set([...parameters.values()].flatMap((values) => values.map(({ name }) => name)))]
  for (const name of parameterNames) {
    const label = builtInLabels.has(name) ? `${name}（自定义）` : name
    addTextColumn(`parameter:${name}`, label, (item) => parameters.get(item.uuid)?.filter((parameter) => parameter.name === name).map(({ value }) => value).join('；'))
  }
  addTextColumn('updated', '更新时间', (item) => item.updatedAt ? formatHistoryTime(item.updatedAt) : undefined)
  columns.push({ key: 'actions', label: '操作', render: (item) => <div className="reagent-catalog-actions">
    <button className="reagent-detail-link" aria-label={`查看试剂目录 ${item.name}`} onClick={() => onDetails(item)}>查看详情</button>
    <button disabled={disabled} aria-label={`编辑试剂目录 ${item.name}`} onClick={() => onEdit(item)}><Pencil size={14} />编辑</button>
    <button className="danger" disabled={disabled} aria-label={`删除试剂目录 ${item.name}`} onClick={() => onDelete(item)}><Trash2 size={14} />删除</button>
  </div> })
  return <div className="reagent-catalog-list">
    {items.length ? <table className="reagent-catalog-table" aria-label="试剂目录" style={{ minWidth: columns.length * 136 }}>
      <thead><tr>{columns.map((column) => <th key={column.key} scope="col">{column.label}</th>)}</tr></thead>
      <tbody>{items.map((item) => <tr key={item.uuid}>{columns.map((column) => <td key={column.key} data-field={column.key}>{column.render(item)}</td>)}</tr>)}</tbody>
    </table> : <EmptyState title="暂无试剂目录" description="创建化学品目录项后，才可以录入具体容器。" />}
  </div>
}

function ReagentHistoryDrawer({ reagent, items, loading, error, onClose }: { reagent: ReagentRecord; items: ReagentHistoryRecord[]; loading: boolean; error: Error | null; onClose: () => void }) {
  return <div className="reagent-history-backdrop" role="presentation"><aside className="reagent-history-drawer" role="dialog" aria-modal="true" aria-label={`${reagent.name} 操作历史`}><header><div><span>REAGENT LEDGER</span><h2>操作历史</h2><p>{reagent.name} · {reagent.containerName || reagent.materialUuid}</p><code className="reagent-uuid" title="试剂记录 UUID">{reagent.uuid}</code></div><button aria-label="关闭操作历史" onClick={onClose}>×</button></header><section className="reagent-history-summary"><div><small>当前余量</small><strong>{reagent.quantity ?? '—'} {reagent.quantityUnit || ''}</strong></div><div><small>当前修订</small><strong>r{reagent.revision}</strong></div><div><small>历史记录</small><strong>{items.length}</strong></div></section><div className="reagent-history-identities"><span>试剂 UUID<code>{reagent.uuid}</code></span><span>容器物料 UUID<code>{reagent.materialUuid}</code></span></div><section className="reagent-history-timeline">{loading ? <EmptyState title="正在读取操作历史" description="正在从本地不可变台账读取记录。" /> : error ? <EmptyState title="操作历史读取失败" description={error.message} /> : items.length ? items.map((item) => <ReagentHistoryItem key={item.uuid} item={item} />) : <EmptyState title="暂无操作历史" description="该试剂尚未产生台账记录。" />}</section></aside></div>
}

function ReagentHistoryItem({ item }: { item: ReagentHistoryRecord }) {
  const event = historyEvent(item.eventType)
  const delta = `${item.quantityDelta > 0 ? '+' : ''}${item.quantityDelta} ${item.quantityUnit}`
  return <article data-event={item.eventType}><span className="history-marker">{event.short}</span><div><header><div><strong>{event.label}</strong><time>{formatHistoryTime(item.recordedAt)}</time></div><em data-positive={item.quantityDelta > 0}>{delta}</em></header><dl><div><dt>变更后余量</dt><dd>{item.resultQuantity ?? '—'} {item.resultQuantityUnit || item.quantityUnit}</dd></div>{item.maximumCapacity !== undefined ? <div><dt>最大装料量</dt><dd>{item.previousMaximumCapacity !== undefined ? `${capacityText(item.previousMaximumCapacity, item.quantityUnit)} → ` : ''}{capacityText(item.maximumCapacity, item.quantityUnit)}</dd></div> : null}<div><dt>操作方</dt><dd>{item.operatorType}</dd></div><div><dt>修订</dt><dd>r{item.revision}</dd></div>{item.source ? <div><dt>来源</dt><dd>{item.source}</dd></div> : null}{item.eventType === 'dispense_target' && item.sourceReagentUuid ? <div><dt>分装自</dt><dd><code>{item.sourceReagentUuid}</code></dd></div> : null}{item.eventType === 'dispense_source' && item.targetReagentUuids?.length ? <div><dt>分装到</dt><dd>{item.targetReagentUuids.length} 个容器</dd></div> : null}{item.causationId ? <div><dt>分装批次</dt><dd><code>{item.causationId}</code></dd></div> : null}</dl>{item.workflowTaskUuid || item.workflowNodeJobUuid ? <p>{item.workflowTaskUuid ? <>任务 <code>{item.workflowTaskUuid}</code></> : null}{item.workflowNodeJobUuid ? <>节点作业 <code>{item.workflowNodeJobUuid}</code></> : null}</p> : null}<code className="history-uuid">{item.uuid}</code></div></article>
}

function historyEvent(type: string) {
  if (type === 'add') return { label: '录入 / 补充', short: '+' }
  if (type === 'consume') return { label: '工作流消耗', short: '−' }
  if (type === 'remove') return { label: '移除试剂', short: '×' }
  if (type === 'dispense_source') return { label: '分装出库', short: '⇢' }
  if (type === 'dispense_target') return { label: '分装入库', short: '⇠' }
  return { label: '调整余量', short: '↕' }
}

function formatHistoryTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value || '—' : date.toLocaleString('zh-CN', { hour12: false })
}

function validateIdentity(form: IdentityForm, parameters: CustomParameter[]) {
  if (!form.name.trim()) return '试剂名称不能为空'
  if (form.cas.trim() && !isValidCas(form.cas.trim())) return 'CAS 号校验位不正确，请修正或留空'
  for (const [label, value] of [['分子量', form.molecularWeight], ['参考密度', form.density]]) if (value && (!Number.isFinite(Number(value)) || Number(value) <= 0)) return `${label}必须是大于零的有限数`
  const names = new Set<string>()
  for (const parameter of parameters) { const name = parameter.name.trim().toLowerCase(); if (!name || !parameter.value.trim()) return '自定义参数的名称和值都不能为空'; if (names.has(name)) return `自定义参数名称“${parameter.name.trim()}”重复`; names.add(name) }
  return null
}
function replaceAutoValue(current: string, previous?: string, next?: string) { return !current.trim() || current === previous ? next || '' : current }
function numberText(value?: number) { return value == null ? undefined : String(value) }
function numberOrUndefined(value: string) { return value.trim() ? Number(value) : undefined }
function textOrUndefined(value: string) { return value.trim() || undefined }
function lookupFallback(status: CompoundLookupResult['status']) { return status === 'registered' ? '该 CAS 已登记，请直接选择现有试剂目录项。' : status === 'not_found' ? '未查到该 CAS，请手工填写化学信息。' : '外部化合物数据源不可用，可手工填写化学信息。' }
