import { useEffect, useRef, useState } from 'react'

type PreviewResult = { smiles: string; phase: 'ready' | 'error'; formula?: string; message?: string }
type SmilesNode = { ringbonds?: Array<{ id: number }>; branches?: SmilesNode[]; next?: SmilesNode | null }

function assertClosedRings(tree: SmilesNode) {
  // 绘图库会忽略未配对的环标号；先检查解析树，避免把未闭合环画成链。
  const open = new Set<number>()
  const nodes = [tree]
  while (nodes.length) {
    const node = nodes.pop()!
    for (const ring of node.ringbonds || []) {
      if (open.has(ring.id)) open.delete(ring.id)
      else open.add(ring.id)
    }
    nodes.push(...(node.branches || []))
    if (node.next) nodes.push(node.next)
  }
  if (open.size) throw new Error('SMILES 环标号未闭合')
}

export function ChemicalStructurePreview({ smiles, compact = false, label = '2D 分子结构' }: { smiles: string; compact?: boolean; label?: string }) {
  const value = smiles.trim()
  const svgRef = useRef<SVGSVGElement>(null)
  const [result, setResult] = useState<PreviewResult | null>(null)
  const phase = !value ? 'empty' : result?.smiles === value ? result.phase : 'loading'

  useEffect(() => {
    const svg = svgRef.current
    svg?.replaceChildren()
    if (!value || !svg) return
    let cancelled = false
    // 输入停顿后再解析；绘图库按需加载，保持其他库存页面的加载体积。
    const timer = window.setTimeout(() => {
      void import('smiles-drawer').then(({ default: SmilesDrawer }) => {
        if (cancelled) return
        const drawer = new SmilesDrawer.SvgDrawer({
          width: 560, height: 220, padding: 18, scale: 1.5,
          bondLength: 30, fontSizeLarge: 12, fontSizeSmall: 7,
          fontFamily: 'Arial, Helvetica, sans-serif',
        })
        SmilesDrawer.parse(value, (graph: unknown) => {
          if (cancelled) return
          assertClosedRings(graph as unknown as SmilesNode)
          drawer.draw(graph, svg, 'light')
          setResult({ smiles: value, phase: 'ready', formula: drawer.getMolecularFormula() })
        }, () => {
          if (cancelled) return
          svg.replaceChildren()
          setResult({ smiles: value, phase: 'error', message: '无法解析这段 SMILES，请检查后重试。' })
        })
      }).catch(() => {
        if (!cancelled) setResult({ smiles: value, phase: 'error', message: '结构预览加载失败，请刷新页面重试。' })
      })
    }, 200)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [value])

  return <div className={`reagent-structure ${compact ? 'chemical-structure-compact' : 'wide'}`} data-state={phase}>
    {!compact ? <span>2D 结构</span> : null}
    <div className="chemical-structure-canvas" aria-busy={phase === 'loading'}>
      <svg ref={svgRef} role="img" aria-label={label} aria-hidden={phase !== 'ready'} />
      {phase === 'empty' ? <small>输入或查询 SMILES 后显示结构式。</small> : null}
      {phase === 'loading' ? <small role="status">正在绘制结构式…</small> : null}
      {phase === 'error' ? <small role="alert">{result?.message}</small> : null}
    </div>
    {!compact && phase === 'ready' && result?.formula ? <small className="chemical-structure-formula">分子式：{result.formula}</small> : null}
  </div>
}
