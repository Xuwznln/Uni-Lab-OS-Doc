import type { CapacityLimits } from '../types'
import { canConvertLiquid, capacityBasisError, capacityText, capacityValue, type QuantityContext } from '../lib/capacity'

export function CapacityField({ value, unit, onChange, label = '最大装料量', defaultValue, disabled = false, hint }: { value: string; unit: string; onChange: (value: string) => void; label?: string; defaultValue?: string; disabled?: boolean; hint?: string }) {
  return <div className="capacity-field">
    <label className="form-field"><span>{label}（{unit}）</span><input aria-label={`${label}（${unit}）`} type="number" min="0" step="any" placeholder={defaultValue || '请输入最大装料量'} value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)} /></label>
    <small>{hint || (defaultValue ? `留空使用默认值 ${defaultValue} ${unit}。` : '可留空，已知最大装料量时填写。')}</small>
  </div>
}

export function ReagentCapacityFields({ maximum, unit, current, rated, context, onChange, error, disabled, label = '最大装料量', className = '' }: {
  maximum?: string; unit: string; current?: CapacityLimits; rated?: CapacityLimits; context: QuantityContext;
  onChange: (value: string) => void; error?: string; disabled?: boolean; label?: string; className?: string;
}) {
  const unavailable = capacityBasisError(unit, rated, context)
  const defaultValue = capacityValue(rated, unit, context)
  const massUnit = ['mg', 'g', 'kg'].includes(unit.trim().toLowerCase())
  const solid = context.physicalState === 'solid'
  const hint = unavailable ? '请先确认试剂物态和可用的计量单位。'
    : !defaultValue ? solid && rated?.max_volume_ul != null
      ? '容器容积不能直接换算为粉体质量，请手动填写最大装料量。'
      : '未提供对应单位的额定规格，请手动填写最大装料量。'
    : undefined
  return <section className={`capacity-section reagent-capacity-section ${className}`} aria-label={`${label}设置`}>
    {solid ? <>
      {rated?.max_volume_ul != null ? <div className="capacity-rated"><span>容器容积</span><strong>{capacityText({ max_volume_ul: rated.max_volume_ul })}</strong></div> : null}
      {rated?.max_mass_g != null || rated?.max_volume_ul == null ? <div className="capacity-rated"><span>额定装料质量</span><strong>{rated?.max_mass_g != null ? capacityText({ max_mass_g: rated.max_mass_g }) : '未提供'}</strong></div> : null}
    </> : <div className="capacity-rated"><span>容器额定容量</span><strong>{capacityText(rated)}</strong></div>}
    <CapacityField value={maximum ?? capacityValue(current ?? rated, unit, context)} unit={unit} defaultValue={defaultValue} label={label} onChange={onChange} disabled={disabled || Boolean(unavailable)} hint={hint} />
    {massUnit && canConvertLiquid(context) ? <small className="capacity-conversion-note">按密度 {context.densityGPerMl} g/mL 换算，容器额定规格保持不变。</small> : null}
    {error ? <small className="form-error" role="alert">{error}</small> : null}
  </section>
}
