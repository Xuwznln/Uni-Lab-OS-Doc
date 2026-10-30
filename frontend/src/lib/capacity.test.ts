import { describe, expect, it } from 'vitest'
import { capacityBasisError, capacityValue, convertMaximum, maximumError, parseMaximum, quantityUnits } from './capacity'

describe('最大装料量单位与校验', () => {
  const current = { max_volume_ul: 80000 }
  const rated = { max_volume_ul: 100000 }
  const liquid = { physicalState: 'liquid' }
  const solid = { physicalState: 'solid' }
  const ethanol = { physicalState: 'liquid', densityGPerMl: 0.789 }

  it('按数量单位读取容积和质量，不跨维度推算', () => {
    expect(capacityValue(rated, 'mL')).toBe('100')
    expect(capacityValue(rated, 'L')).toBe('0.1')
    expect(capacityValue({ max_mass_g: 2 }, 'mg')).toBe('2000')
    expect(capacityValue(rated, 'g')).toBe('')
    expect(capacityValue({ max_mass_g: 2 }, 'mL')).toBe('')
    expect(capacityValue(undefined, 'mL')).toBe('')
  })

  it.each([
    ['100', 'mL', { max_volume_ul: 100000 }],
    ['0.1', 'L', { max_volume_ul: 100000 }],
    ['100', 'μL', { max_volume_ul: 100 }],
    ['100', 'µL', { max_volume_ul: 100 }],
    ['50', 'g', { max_mass_g: 50 }],
    ['1000', 'mg', { max_mass_g: 1 }],
    ['0.1', 'kg', { max_mass_g: 100 }],
  ])('把 %s %s 转为同维度容量', (value, unit, expected) => {
    expect(parseMaximum(value, unit)).toEqual(expected)
  })

  it.each(['0', '-1', 'Infinity', 'NaN', '1e309', 'invalid'])('拒绝非法最大装料量 %s', (value) => {
    expect(() => parseMaximum(value, 'mL')).toThrow()
    expect(maximumError(1, 'mL', current, rated, value, liquid)).not.toBe('')
  })

  it('未修改时保留现有限制，空白恢复领域包默认容量', () => {
    expect(maximumError(90, 'mL', current, rated, undefined, liquid)).not.toBe('')
    expect(maximumError(101, 'mL', undefined, rated, undefined, liquid)).not.toBe('')
    expect(parseMaximum('  ', 'mL')).toEqual({})
    expect(maximumError(90, 'mL', current, rated, '', liquid)).toBe('')
    expect(maximumError(101, 'mL', current, rated, '', liquid)).not.toBe('')
    expect(maximumError(100, 'mL', current, rated, '100', liquid)).toBe('')
    expect(maximumError(50, 'mL', current, rated, '101', liquid)).not.toBe('')
  })

  it('在同一维度内换算输入，切换到另一维度时恢复对应默认值', () => {
    expect(convertMaximum('80', 'mL', 'L')).toBe('0.08')
    expect(convertMaximum('2', 'g', 'mg')).toBe('2000')
    expect(convertMaximum('0.5', 'L', 'μL')).toBe('500000')
    expect(convertMaximum('80', 'mL', 'g')).toBeUndefined()
    expect(convertMaximum('2', 'g', 'mL')).toBeUndefined()
    expect(convertMaximum('', 'mL', 'L')).toBe('')
  })

  it('粉体容积不推算质量，缺额定质量时按填写的最大装料量校验', () => {
    const volumeOnly = { max_volume_ul: 300000 }
    const context = { ...solid, densityGPerMl: 2 }
    expect(capacityValue(volumeOnly, 'g', context)).toBe('')
    expect(capacityBasisError('g', volumeOnly, context)).toBe('')
    expect(maximumError(30, 'g', volumeOnly, volumeOnly, undefined, context)).toContain('填写最大装料量')
    expect(maximumError(30, 'g', volumeOnly, volumeOnly, '', context)).toContain('填写最大装料量')
    expect(maximumError(30, 'g', volumeOnly, volumeOnly, '50', context)).toBe('')
    expect(maximumError(50, 'g', volumeOnly, volumeOnly, '50', context)).toBe('')
    expect(maximumError(51, 'g', volumeOnly, volumeOnly, '50', context)).toContain('最大装料量 50 g')
    expect(maximumError(1000, 'mg', { max_mass_g: 1 }, { max_mass_g: 1 }, undefined, solid)).toBe('')
    expect(maximumError(1001, 'mg', { max_mass_g: 1 }, { max_mass_g: 1 }, undefined, solid)).not.toBe('')
    expect(maximumError(500, 'mg', { max_mass_g: 1 }, { max_mass_g: 1 }, '1001', solid)).toContain('额定容量')
  })

  it('不能给不支持的计量单位设置容量上限', () => {
    expect(() => parseMaximum('10', 'mmol')).toThrow()
    expect(maximumError(1, 'mmol', undefined, undefined, '10', liquid)).not.toBe('')
    expect(maximumError(1, 'mmol', rated, rated, undefined, liquid)).not.toBe('')
  })

  it('液体按适用密度换算全部单位，数量与自填质量上限都受固定容积约束', () => {
    expect(capacityValue(rated, 'g', ethanol)).toBe('78.9')
    expect(capacityValue(rated, 'mg', ethanol)).toBe('78900')
    expect(convertMaximum('100', 'mL', 'g', ethanol)).toBe('78.9')
    expect(convertMaximum('78.9', 'g', 'L', ethanol)).toBe('0.1')
    expect(convertMaximum('0.0789', 'kg', 'μL', ethanol)).toBe('100000')
    expect(maximumError(78.9, 'g', rated, rated, undefined, ethanol)).toBe('')
    expect(maximumError(79, 'g', rated, rated, undefined, ethanol)).toContain('78.9 g')
    expect(maximumError(50, 'g', rated, rated, '200', ethanol)).toContain('额定容量 78.9 g')
    expect(maximumError(50, 'g', rated, rated, '50', ethanol)).toBe('')
  })

  it('同时有质量与体积约束时取更严者，不能只检查当前单位', () => {
    const context = { physicalState: 'liquid', densityGPerMl: 0.5 }
    const both = { max_volume_ul: 100000, max_mass_g: 30 }
    expect(capacityValue(both, 'mL', context)).toBe('60')
    expect(capacityValue(both, 'g', context)).toBe('30')
    expect(maximumError(61, 'mL', both, both, undefined, context)).toContain('60 mL')
    expect(maximumError(60, 'mL', both, both, undefined, context)).toBe('')
    expect(maximumError(40, 'mL', both, both, '61', context)).toContain('额定容量 60 mL')
    expect(maximumError(5, 'mL', both, both, undefined, liquid)).toContain('密度')
    expect(maximumError(5, 'mL', { max_mass_g: 10 }, rated, undefined, liquid)).toContain('密度')
  })

  it.each([undefined, 0, -1, NaN, Infinity])('密度 %s 不允许液体跨维度计量', (densityGPerMl) => {
    const context = { ...liquid, densityGPerMl }
    expect(quantityUnits(context)).not.toContain('g')
    expect(convertMaximum('100', 'mL', 'g', context)).toBeUndefined()
    expect(maximumError(1, 'g', rated, rated, undefined, context)).toContain('密度')
  })

  it('浓度和粉体密度都不被用作质量体积换算依据', () => {
    const solution = { ...ethanol, concentrationValue: 0 }
    expect(quantityUnits(solution)).not.toContain('g')
    expect(maximumError(1, 'g', rated, rated, undefined, solution)).toContain('浓度')
    expect(convertMaximum('1', 'g', 'mL', { ...solid, densityGPerMl: 2 })).toBeUndefined()
    expect(maximumError(1, 'mL', { max_mass_g: 10 }, { max_mass_g: 10 }, undefined, solid)).toContain('固体')
  })

  it('无物态仍不能录入，无额定规格时可以手填有效最大装料量', () => {
    expect(maximumError(1, 'mL', rated, rated, '10')).toContain('物态')
    expect(capacityBasisError('mL', undefined, liquid)).toBe('')
    expect(maximumError(1, 'mL', undefined, undefined, undefined, liquid)).toContain('填写最大装料量')
    expect(maximumError(1, 'mL', undefined, undefined, '', liquid)).toContain('填写最大装料量')
    expect(maximumError(1, 'mL', undefined, undefined, '10', liquid)).toBe('')
    expect(maximumError(11, 'mL', undefined, undefined, '10', liquid)).toContain('最大装料量 10 mL')
    expect(maximumError(1, 'g', { max_mass_g: 10 }, {}, '10', solid)).toBe('')
    expect(capacityBasisError('mL', { max_mass_g: 10 }, liquid)).toContain('密度')
  })

  it('无额定质量时保留已有手工质量上限，清空后须重新填写', () => {
    const volumeOnly = { max_volume_ul: 300000 }
    const current = { ...volumeOnly, max_mass_g: 50 }
    const context = { ...solid, configuredCapacity: { max_mass_g: 50 } }
    expect(maximumError(30, 'g', current, volumeOnly, undefined, context)).toBe('')
    expect(maximumError(51, 'g', current, volumeOnly, undefined, context)).toContain('最大装料量 50 g')
    expect(maximumError(30, 'g', current, volumeOnly, '', context)).toContain('填写最大装料量')
    for (const invalid of ['0', '-1', 'Infinity', 'NaN']) {
      expect(maximumError(30, 'g', volumeOnly, volumeOnly, invalid, solid)).not.toBe('')
    }
  })

  it('额定几何容积不会当作粉体手工体积限制，旧限制须显式修正为质量', () => {
    const both = { max_volume_ul: 100000, max_mass_g: 50 }
    expect(maximumError(40, 'g', both, both, undefined, solid)).toBe('')
    for (const context of [
      { ...solid, configuredCapacity: { max_volume_ul: 50000 } },
      { ...solid, loadingLimits: { max_volume_ul: 50000 } },
    ]) {
      expect(maximumError(40, 'g', both, both, undefined, context)).toContain('手工上限使用体积')
      expect(maximumError(40, 'g', both, both, '45', context)).toBe('')
    }
  })

  it('已有手工上限即使被另一维度掩盖，也不能高于对应的额定规格', () => {
    const context = { ...ethanol, configuredCapacity: { max_volume_ul: 50000, max_mass_g: 200 } }
    expect(maximumError(10, 'mL', { max_volume_ul: 50000, max_mass_g: 200 }, rated, undefined, context)).toContain('现有手工上限超过')
    expect(maximumError(10, 'mL', current, rated, '50', context)).toBe('')
  })

  it('旧记录可按原单位减量，但不能加量或借修改上限绕过新规则', () => {
    expect(maximumError(10, 'g', {}, {}, undefined, {}, 20)).toBe('')
    expect(maximumError(21, 'g', {}, {}, undefined, {}, 20)).toContain('物态')
    expect(maximumError(10, 'g', {}, {}, '20', {}, 20)).toContain('物态')
  })

  it('所有微升写法按同一维度换算，不能保留溢出的输入', () => {
    for (const unit of ['uL', 'μL', 'µL']) expect(convertMaximum('100000', unit, 'mL')).toBe('100')
    expect(convertMaximum('1e308', 'L', 'μL')).toBeUndefined()
  })
})
