import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChemicalStructurePreview } from './ChemicalStructurePreview'

beforeEach(() => {
  // jsdom 没有文字测量画布，使用绘图库自身的测量后备实现。
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
})
afterEach(() => vi.restoreAllMocks())

describe('2D 化学结构预览', () => {
  it('未填写结构时显示输入提示', () => {
    render(<ChemicalStructurePreview smiles="" />)
    expect(screen.getByText('输入或查询 SMILES 后显示结构式。')).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it.each([
    ['O', 'H2O', 'O'],
    ['CCO', 'C2H6O', 'O'],
    ['c1ccccc1', 'C6H6', ''],
    ['[Na+].[Cl-]', 'ClNa', 'Na'],
  ])('使用真实绘图库绘制 %s', async (smiles, formula, atom) => {
    render(<ChemicalStructurePreview smiles={smiles} />)
    const svg = await screen.findByRole('img', { name: '2D 分子结构' })
    expect(svg).toHaveAttribute('viewBox')
    expect(svg.querySelector('path, line, text')).not.toBeNull()
    if (atom) expect(svg).toHaveTextContent(atom)
    expect(screen.getByText(`分子式：${formula}`)).toBeInTheDocument()
  })

  it('语法错误的 SMILES 显示可修正提示', async () => {
    render(<ChemicalStructurePreview smiles="[C" />)
    expect(await screen.findByRole('alert')).toHaveTextContent('无法解析')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('无效输入移除旧结构，修正或清空后恢复对应状态', async () => {
    const { rerender } = render(<ChemicalStructurePreview smiles="CCO" />)
    await screen.findByRole('img', { name: '2D 分子结构' })
    rerender(<ChemicalStructurePreview smiles="C1CC" />)
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent('无法解析')
    rerender(<ChemicalStructurePreview smiles="O" />)
    expect(await screen.findByRole('img', { name: '2D 分子结构' })).toHaveTextContent('O')
    expect(screen.getByText('分子式：H2O')).toBeInTheDocument()
    rerender(<ChemicalStructurePreview smiles="" />)
    await waitFor(() => expect(screen.queryByRole('img')).not.toBeInTheDocument())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByText('分子式：H2O')).not.toBeInTheDocument()
  })
})
