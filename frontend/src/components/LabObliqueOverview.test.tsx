import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { LabObliqueOverview } from './LabObliqueOverview'
import type { MaterialRecord } from '../types'

function material(overrides: Partial<MaterialRecord>): MaterialRecord {
  return {
    uuid: 'material-1', name: '物料', category: 'beaker',
    currentLocation: { kind: 'unassigned', label: '未分配' }, configuredSource: '',
    taskReferences: [], barcode: 'CODE', className: 'resource', updatedAt: '',
    isStructural: false, siteCount: 0, sites: [], revision: 1,
    position: [0, 0, 0], size: [80, 80, 100], ...overrides,
  }
}

function center(viewBox: string) {
  const [x, y, width, height] = viewBox.split(' ').map(Number)
  return [x + width / 2, y + height / 2]
}

describe('LabObliqueOverview', () => {
  it('moves the camera center to the selected object when focusing', () => {
    const materials = [
      material({ uuid: 'left', name: '左侧物料', position: [0, 0, 0] }),
      material({ uuid: 'right', name: '右侧物料', position: [1200, 400, 0] }),
    ]
    render(<LabObliqueOverview materials={materials} selectedId="right" onSelect={vi.fn()} />)
    const scene = screen.getByRole('group', { name: '实验室整体 2.5D 场景' })
    const before = center(scene.getAttribute('viewBox') || '')
    fireEvent.click(screen.getByRole('button', { name: '聚焦已选物料' }))
    const after = center(scene.getAttribute('viewBox') || '')
    expect(after).not.toEqual(before)
  })

  it('opens device templates without hiding equipment from the scene', () => {
    const materials = [
      material({ uuid: 'robot', name: '机械臂', category: 'rail_robot' }),
      material({ uuid: 'beaker', name: '烧杯', category: 'beaker' }),
    ]
    render(<LabObliqueOverview materials={materials} templates={[{ uuid: 'robot-template', name: 'robot', displayName: '机械臂模板', description: '', resourceType: 'device', availableSites: [] }]} onSelect={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: '仪器设备' }))
    expect(screen.getByLabelText('设备模板')).toHaveTextContent('机械臂模板')
    expect(screen.getByRole('button', { name: '机械臂' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '烧杯' })).toBeInTheDocument()
  })

  it('clears selection with Escape', () => {
    const onSelect = vi.fn()
    render(<LabObliqueOverview materials={[material({})]} selectedId="material-1" onSelect={onSelect} />)
    fireEvent.keyDown(screen.getByRole('group', { name: '实验室整体 2.5D 场景' }), { key: 'Escape' })
    expect(onSelect).toHaveBeenCalledWith('')
  })
})
