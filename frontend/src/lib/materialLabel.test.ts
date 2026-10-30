import { describe, expect, it } from 'vitest'
import { isUuidLike, materialLabel } from './materialLabel'

describe('materialLabel', () => {
  it('name 就是 UUID 时，主行用条码、副行用模板显示名', () => {
    const label = materialLabel(
      { uuid: 'f76eefbc-fdae-5bda-9ecf-79ce13e3254c', name: 'f76eefbc-fdae-5bda-9ecf-79ce13e3254c', barcode: 'bottle-6-hole-6' },
      'pTLC 收集瓶',
    )
    expect(label).toEqual({ primary: 'bottle-6-hole-6', secondary: 'pTLC 收集瓶', text: 'bottle-6-hole-6 · pTLC 收集瓶' })
  })

  it('name 可读时保持原名，模板名与主行不同才作副行', () => {
    expect(materialLabel({ uuid: 'u1', name: 'control', barcode: 'UNILAB-GRAPH-control' }, 'control'))
      .toEqual({ primary: 'control', secondary: '', text: 'control' })
    expect(materialLabel({ uuid: 'u2', name: '试剂瓶 100 mL', barcode: 'VIAL-1' }, '试剂瓶'))
      .toEqual({ primary: '试剂瓶 100 mL', secondary: '试剂瓶', text: '试剂瓶 100 mL · 试剂瓶' })
  })

  it('既无可读名也无条码时退回 UUID 前 8 位；占位条码“—”视为空', () => {
    expect(materialLabel({ uuid: '34a3176e-9f47-5f20-a17b-05ad8e72c700', name: '34a3176e-9f47-5f20-a17b-05ad8e72c700', barcode: '—' }))
      .toEqual({ primary: '34a3176e', secondary: '', text: '34a3176e' })
  })

  it('isUuidLike 只认完整 UUID', () => {
    expect(isUuidLike('34a3176e-9f47-5f20-a17b-05ad8e72c700')).toBe(true)
    expect(isUuidLike('34a3176e')).toBe(false)
    expect(isUuidLike('bottle-1')).toBe(false)
    expect(isUuidLike('')).toBe(false)
  })
})
