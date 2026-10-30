import type { CapacityLimits } from '../types'

export type MaximumFields = { maximum?: string }
export type QuantityContext = { physicalState?: string; densityGPerMl?: number; concentrationValue?: number | null; configuredCapacity?: CapacityLimits; loadingLimits?: CapacityLimits }

const unitScale: Record<string, [keyof CapacityLimits, number]> = {
  ul: ['max_volume_ul', 1], 'μl': ['max_volume_ul', 1], 'µl': ['max_volume_ul', 1], ml: ['max_volume_ul', 1000], l: ['max_volume_ul', 1e6],
  mg: ['max_mass_g', 0.001], g: ['max_mass_g', 1], kg: ['max_mass_g', 1000],
}

const volumeUnits = ['μL', 'mL', 'L']
const massUnits = ['mg', 'g', 'kg']
const rounded = (value: number) => String(Number(value.toPrecision(12)))

export function configuredCapacity(value: unknown): CapacityLimits | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const record = value as Record<string, unknown>
  const result: CapacityLimits = {}
  for (const key of ['max_volume_ul', 'max_mass_g'] as const) {
    if (typeof record[key] === 'number') result[key] = record[key]
  }
  return result
}

export function canConvertLiquid(context?: QuantityContext): boolean {
  return context?.physicalState === 'liquid' && context.concentrationValue == null
    && typeof context.densityGPerMl === 'number' && Number.isFinite(context.densityGPerMl) && context.densityGPerMl > 0
}

export function quantityUnits(context?: QuantityContext): string[] {
  if (context?.physicalState === 'solid') return massUnits
  if (context?.physicalState === 'liquid') return canConvertLiquid(context) ? [...volumeUnits, ...massUnits] : volumeUnits
  return []
}

export function defaultQuantityUnit(context?: QuantityContext): string {
  return context?.physicalState === 'solid' ? 'g' : 'mL'
}

export function quantityUnitError(unit: string, context?: QuantityContext): string {
  if (!context?.physicalState || context.physicalState === 'unknown') return '请先在试剂目录中确认物态，再录入库存。'
  if (!['solid', 'liquid'].includes(context.physicalState)) return '当前仅支持液体和固体试剂录入，请核对物态。'
  const dimension = unitScale[unit.trim().toLowerCase()]
  if (!dimension) return '请选择体积或质量单位；暂不支持物质的量计量。'
  if (context.physicalState === 'solid' && dimension[0] !== 'max_mass_g') return '固体试剂请使用 mg、g 或 kg 计量，不能从容积推算质量。'
  if (context.physicalState === 'liquid' && dimension[0] === 'max_mass_g' && !canConvertLiquid(context)) {
    return context.concentrationValue != null
      ? '有浓度的液体暂不支持质量与体积换算，请使用体积单位。'
      : '液体按质量计量需要有效的密度，请补充密度或使用体积单位。'
  }
  return ''
}

/** 与工作流使用相同的液体换算前提；粉体参考密度不参与容量换算。 */
function convertAmount(value: number, fromUnit: string, toUnit: string, context?: QuantityContext): number | undefined {
  const from = unitScale[fromUnit.trim().toLowerCase()]
  const to = unitScale[toUnit.trim().toLowerCase()]
  if (!from || !to || !Number.isFinite(value)) return undefined
  if (from[0] === to[0]) return value * from[1] / to[1]
  if (!canConvertLiquid(context)) return undefined
  const base = value * from[1]
  return from[0] === 'max_mass_g'
    ? base / context!.densityGPerMl! * 1000 / to[1]
    : base / 1000 * context!.densityGPerMl! / to[1]
}

function capacityLimit(capacity: CapacityLimits | undefined, unit: string, context?: QuantityContext): number | undefined {
  const dimension = unitScale[unit.trim().toLowerCase()]
  if (!dimension || !capacity) return undefined
  const direct = capacity[dimension[0]]
  if (context?.physicalState !== 'liquid') return direct == null ? undefined : direct / dimension[1]
  if (quantityUnitError(unit, context)) return undefined
  const values: number[] = []
  for (const [key, sourceUnit] of [['max_volume_ul', 'μL'], ['max_mass_g', 'g']] as const) {
    const limit = capacity[key]
    if (limit == null) continue
    const converted = convertAmount(limit, sourceUnit, unit, context)
    if (converted == null || !Number.isFinite(converted)) return undefined
    values.push(converted)
  }
  return values.length ? Math.min(...values) : undefined
}

export function capacityValue(capacity: CapacityLimits | undefined, unit: string, context?: QuantityContext): string {
  const value = capacityLimit(capacity, unit, context)
  return value == null || !Number.isFinite(value) ? '' : rounded(value)
}

export function parseMaximum(value: string, unit: string): CapacityLimits {
  if (!value.trim()) return {}
  const dimension = unitScale[unit.trim().toLowerCase()]
  if (!dimension) throw new Error('最大装料量需要使用体积或质量单位。')
  const maximum = Number(value) * dimension[1]
  if (!Number.isFinite(maximum) || maximum <= 0) throw new Error('最大装料量须为大于零的有限数值。')
  return { [dimension[0]]: maximum }
}

export function convertMaximum(value: string | undefined, fromUnit: string, toUnit: string, context?: QuantityContext): string | undefined {
  if (value === undefined) return undefined
  const converted = convertAmount(Number(value), fromUnit, toUnit, context)
  if (converted == null || !Number.isFinite(converted)) return undefined
  return value.trim() ? rounded(converted) : ''
}

export function capacityText(capacity?: CapacityLimits, unit?: string, context?: QuantityContext) {
  const raw = [capacity?.max_volume_ul == null ? '' : `${capacity.max_volume_ul / 1000} mL`, capacity?.max_mass_g == null ? '' : `${capacity.max_mass_g} g`].filter(Boolean).join(' / ')
  if (unit) { const value = capacityValue(capacity, unit, context); return value ? `${value} ${unit}` : raw ? `${raw}（未换算）` : '未配置' }
  return raw || '未配置'
}

export function stricterCapacity(...capacities: Array<CapacityLimits | null | undefined>): CapacityLimits {
  const result: CapacityLimits = {}
  for (const key of ['max_volume_ul', 'max_mass_g'] as const) {
    const values = capacities.flatMap((capacity) => capacity?.[key] == null ? [] : [capacity[key]!])
    if (values.length) result[key] = Math.min(...values)
  }
  return result
}

export function capacityBasisError(unit: string, ratedCapacity?: CapacityLimits, context?: QuantityContext): string {
  const unitError = quantityUnitError(unit, context)
  if (unitError) return unitError
  if (context?.physicalState === 'liquid' && Object.values(ratedCapacity ?? {}).some((value) => value != null) && capacityLimit(ratedCapacity, unit, context) == null) return '容器容量与数量的计量维度不同，缺少适用的密度，无法完成容量校验。'
  return ''
}

export function maximumError(quantity: number, unit: string, currentCapacity?: CapacityLimits, ratedCapacity?: CapacityLimits, maximum?: string, context?: QuantityContext, previousQuantity?: number): string {
  // 旧记录只减量或修改说明时沿用原单位；加量和修改上限必须满足当前规则。
  if (previousQuantity != null && maximum === undefined && Number.isFinite(quantity) && quantity >= 0 && quantity <= previousQuantity) return ''
  const basisError = capacityBasisError(unit, ratedCapacity, context)
  if (basisError) return basisError
  const rated = capacityLimit(ratedCapacity, unit, context)
  let effective = stricterCapacity(ratedCapacity, currentCapacity)
  if (maximum === undefined) {
    // 几何额定容积与用户主动设置的体积限制不同；后者不能在粉体入库时被忽略。
    for (const manual of [context?.configuredCapacity, context?.loadingLimits]) {
      for (const [key, sourceUnit] of [['max_volume_ul', 'μL'], ['max_mass_g', 'g']] as const) {
        const value = manual?.[key]
        if (value == null) continue
        if (context?.physicalState === 'solid' && key === 'max_volume_ul') return '现有手工上限使用体积单位，请重新设置质量最大装料量。'
        const manualUnitError = quantityUnitError(sourceUnit, context)
        if (manualUnitError) return manualUnitError
        const permitted = capacityLimit(ratedCapacity, sourceUnit, context)
        if (!Number.isFinite(value) || value <= 0) return '现有手工上限无效，请重新设置大于零的最大装料量。'
        if (permitted != null && value > permitted + Math.max(1e-9, permitted * 1e-12)) return '现有手工上限超过容器额定容量，请重新设置最大装料量。'
      }
    }
  }
  if (maximum !== undefined) {
    let configured: CapacityLimits
    try { configured = parseMaximum(maximum, unit) } catch (error) { return (error as Error).message }
    const configuredLimit = capacityLimit(configured, unit, context)
    if (rated != null && configuredLimit != null && configuredLimit > rated + Math.max(1e-9, rated * 1e-12)) return `最大装料量不能超过容器额定容量 ${rounded(rated)} ${unit}。`
    effective = stricterCapacity(ratedCapacity, configured)
  }
  const limit = capacityLimit(effective, unit, context)
  if (limit == null) return context?.physicalState === 'liquid' && Object.values(effective).some((value) => value != null)
    ? '现有装料限制涉及不同计量维度，缺少适用的密度，无法完成容量校验。'
    : `请填写最大装料量（${unit}）。`
  if (quantity > limit + Math.max(1e-9, limit * 1e-12)) return `数量超过最大装料量 ${rounded(limit)} ${unit}。`
  return ''
}
