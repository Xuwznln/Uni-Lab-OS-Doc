import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { demoMaterials } from '../data/demo'
import type { ResourceTemplateRecord } from '../types'
import { deriveContainerFilterTags, DispenseForm, ReagentsPage, RegisterForm, summariseDispense } from './ReagentsPage'

function row(id: number, materialUuid: string, quantity: string) {
  return { id, materialUuid, quantity }
}

function template(uuid: string, tags: string[]): ResourceTemplateRecord {
  return { uuid, name: uuid, displayName: uuid, description: '', resourceType: 'resource', tags, availableSites: [] }
}

function containerOptions(trigger: HTMLElement) {
  if (trigger.getAttribute('aria-expanded') !== 'true') fireEvent.click(trigger)
  return screen.getByRole('listbox')
}

describe('deriveContainerFilterTags', () => {
  it('keeps discriminating container tags and removes shared package tags', () => {
    const tags = deriveContainerFilterTags([
      template('liquid', ['szlab_poly_studio', 'container', 'liquid_reagent']),
      template('powder', ['szlab_poly_studio', 'container', 'powder_reagent']),
      template('device', ['szlab_poly_studio', 'device']),
    ])
    expect(tags).toEqual(expect.arrayContaining(['liquid_reagent', 'powder_reagent']))
    expect(tags).not.toContain('container')
    expect(tags).not.toContain('szlab_poly_studio')
  })
})

describe('DispenseForm container filters', () => {
  it('defaults to the source container tag and hides long barcodes from options', () => {
    const templates = [
      template('liquid-template', ['szlab_poly_studio', 'container', 'liquid_reagent']),
      template('powder-template', ['szlab_poly_studio', 'container', 'powder_reagent']),
    ]
    const containers = [
      { ...demoMaterials[0], uuid: 'liquid-1', name: '液体瓶 R1C2', barcode: 'UNILAB-GRAPH-s10-liquid-R1C2', resourceTemplateUuid: 'liquid-template', isStructural: false, capacity: { max_volume_ul: 100000 }, ratedCapacity: { max_volume_ul: 100000 } },
      { ...demoMaterials[0], uuid: 'powder-1', name: '注粉瓶 L1C2', barcode: 'UNILAB-GRAPH-powder-L1C2', resourceTemplateUuid: 'powder-template', isStructural: false, capacity: { max_volume_ul: 100000, max_mass_g: 100 }, ratedCapacity: { max_volume_ul: 100000, max_mass_g: 100 } },
    ]
    render(<DispenseForm
      source={{ uuid: 'source-reagent', materialUuid: 'source-material', reagentInfoUuid: 'info', name: '乙醇', physicalState: 'liquid', densityGPerMl: 0.789, quantity: 100, quantityUnit: 'mL', revision: 1, updatedAt: '' }}
      rows={[row(1, '', '')]}
      setRows={vi.fn()}
      containers={containers}
      templates={templates}
      filterTags={deriveContainerFilterTags(templates)}
      preferredTag="liquid_reagent"
      saving={false}
      onSave={vi.fn()}
    />)

    const targetSelect = screen.getByRole('combobox', { name: '目标容器 1' })
    expect(screen.getByRole('button', { name: /液体试剂瓶/ })).toHaveAttribute('aria-pressed', 'true')
    const initialOptions = containerOptions(targetSelect)
    expect(within(initialOptions).getByRole('option', { name: /^液体瓶 R1C2/ })).toBeInTheDocument()
    expect(within(initialOptions).queryByRole('option', { name: /^注粉瓶 L1C2/ })).not.toBeInTheDocument()
    expect(initialOptions.textContent).not.toContain('UNILAB-GRAPH')

    fireEvent.click(screen.getByRole('button', { name: /粉末试剂瓶/ }))
    const filteredOptions = containerOptions(targetSelect)
    expect(within(filteredOptions).getByRole('option', { name: /^注粉瓶 L1C2/ })).toBeInTheDocument()
    expect(within(filteredOptions).queryByRole('option', { name: /^液体瓶 R1C2/ })).not.toBeInTheDocument()
  })
})

describe('RegisterForm container filters', () => {
  it('shows tag filters before choosing a reagent container and omits barcodes', () => {
    const templates = [
      template('liquid-template', ['szlab_poly_studio', 'container', 'liquid_reagent']),
      template('beaker-template', ['szlab_poly_studio', 'container', 'beaker']),
    ]
    const containers = [
      { ...demoMaterials[0], uuid: 'liquid-1', name: '液体瓶 R1C2', barcode: 'UNILAB-GRAPH-s10-liquid-R1C2', resourceTemplateUuid: 'liquid-template', isStructural: false, capacity: { max_volume_ul: 100000 }, ratedCapacity: { max_volume_ul: 100000 } },
      { ...demoMaterials[0], uuid: 'beaker-1', name: '烧杯 L1A1', barcode: 'UNILAB-GRAPH-s3-beaker-L1A1', resourceTemplateUuid: 'beaker-template', isStructural: false, capacity: { max_volume_ul: 100000 }, ratedCapacity: { max_volume_ul: 100000 } },
    ]
    render(<RegisterForm
      form={{ materialUuid: '', reagentInfoUuid: 'water', quantity: '', quantityUnit: 'mL', concentrationValue: '', concentrationUnit: '%', description: '' }}
      setForm={vi.fn()}
      infos={[{ uuid: 'water', name: '水', physicalState: 'liquid', densityGPerMl: 1, aliases: [], updatedAt: '' }]}
      containers={containers}
      templates={templates}
      filterTags={deriveContainerFilterTags(templates)}
      saving={false}
      onSave={vi.fn()}
    />)

    const containerSelect = screen.getByRole('combobox', { name: '试剂容器' })
    expect(screen.getByRole('group', { name: '按容器标签筛选' })).toBeInTheDocument()
    const initialOptions = containerOptions(containerSelect)
    expect(within(initialOptions).getByRole('option', { name: /^液体瓶 R1C2/ })).toBeInTheDocument()
    expect(within(initialOptions).getByRole('option', { name: /^烧杯 L1A1/ })).toBeInTheDocument()
    expect(initialOptions.textContent).not.toContain('UNILAB-GRAPH')

    fireEvent.click(screen.getByRole('button', { name: /液体试剂瓶/ }))
    const filteredOptions = containerOptions(containerSelect)
    expect(within(filteredOptions).getByRole('option', { name: /^液体瓶 R1C2/ })).toBeInTheDocument()
    expect(within(filteredOptions).queryByRole('option', { name: /^烧杯 L1A1/ })).not.toBeInTheDocument()
  })
})

describe('summariseDispense', () => {
  it('sums valid rows and reports the remaining source quantity', () => {
    const summary = summariseDispense([row(1, 'a', '100'), row(2, 'b', '150')], 500)
    expect(summary.total).toBe(250)
    expect(summary.remaining).toBe(250)
    expect(summary.duplicated).toBe(false)
    expect(summary.ready).toBe(true)
  })

  it('allows dispensing the whole bottle down to zero', () => {
    const summary = summariseDispense([row(1, 'a', '100')], 100)
    expect(summary.remaining).toBe(0)
    expect(summary.ready).toBe(true)
  })

  it('blocks submission when the total exceeds the source quantity', () => {
    const summary = summariseDispense([row(1, 'a', '60'), row(2, 'b', '50')], 100)
    expect(summary.total).toBe(110)
    expect(summary.remaining).toBeLessThan(0)
    expect(summary.ready).toBe(false)
  })

  it('blocks submission when the same container is chosen twice', () => {
    const summary = summariseDispense([row(1, 'a', '10'), row(2, 'a', '10')], 100)
    expect(summary.duplicated).toBe(true)
    expect(summary.ready).toBe(false)
  })

  it('blocks submission while any row is incomplete or non-positive', () => {
    expect(summariseDispense([row(1, '', '10')], 100).ready).toBe(false)
    expect(summariseDispense([row(1, 'a', '')], 100).ready).toBe(false)
    expect(summariseDispense([row(1, 'a', '0')], 100).ready).toBe(false)
    expect(summariseDispense([row(1, 'a', '-5')], 100).ready).toBe(false)
    expect(summariseDispense([], 100).ready).toBe(false)
  })
})

describe('ReagentsPage dispense entry', () => {
  it('renders the reagent workspace without a connected edge', () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <ReagentsPage materials={demoMaterials} connected={false} onNotify={vi.fn()} />
      </QueryClientProvider>,
    )
    expect(screen.getByRole('heading', { name: '试剂' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '录入试剂' })).toBeDisabled()
  })
})
