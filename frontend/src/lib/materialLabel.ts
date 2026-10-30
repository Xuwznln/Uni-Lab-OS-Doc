/**
 * 物料的展示标签。
 *
 * 实例登记出来的物料在 material 表里 name 就是自己的 UUID（触发器镜像所致），
 * 直接显示毫无辨识度。展示层统一按下面的规则合成两行标签，name 字段本身不动：
 *   主行：name 可读时用 name；否则用条码；都没有才退回 UUID 前 8 位
 *   副行：资源模板的显示名（与主行相同时省略）
 * `text` 是单行形式，供排序、搜索、aria 与导出使用。
 */
export interface MaterialLabel {
  primary: string
  secondary: string
  text: string
}

const UUID_LIKE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** 判断一个字符串是否只是 UUID（不可读）。 */
export function isUuidLike(value: string | null | undefined): boolean {
  return Boolean(value && UUID_LIKE.test(value.trim()))
}

/**
 * 合成物料展示标签。
 *
 * @param material 至少含 uuid；name / barcode 可缺省。
 * @param templateName 资源模板显示名；来自快照的 resource_template.display_name 或模板列表。
 */
export function materialLabel(
  material: { uuid: string; name?: string | null; barcode?: string | null },
  templateName?: string | null,
): MaterialLabel {
  const uuid = String(material.uuid || '')
  const name = String(material.name || '').trim()
  const barcode = String(material.barcode || '').trim()
  const template = String(templateName || '').trim()
  const readableName = Boolean(name) && !isUuidLike(name) && name !== uuid
  const readableBarcode = Boolean(barcode) && barcode !== '—'
  const primary = readableName ? name : readableBarcode ? barcode : uuid.slice(0, 8)
  const secondary = template && template !== primary ? template : ''
  return { primary, secondary, text: secondary ? `${primary} · ${secondary}` : primary }
}
