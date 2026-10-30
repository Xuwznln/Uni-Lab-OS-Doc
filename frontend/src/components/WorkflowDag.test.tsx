import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { WorkflowGraphEdge, WorkflowGraphNode } from '../types'
import { WorkflowDag } from './WorkflowDag'

function action(uuid: string, name: string, order: number, parentUuid?: string): WorkflowGraphNode {
  return {
    uuid,
    name,
    type: 'ILab',
    kind: 'action',
    authoringOrder: order,
    parentUuid,
    disabled: false,
  }
}

function group(
  uuid: string,
  name: string,
  order: number,
  parallelScope?: string,
): WorkflowGraphNode {
  return {
    uuid,
    name,
    type: 'group',
    kind: 'group',
    authoringOrder: order,
    parallelScope,
    disabled: false,
  }
}

function material(uuid: string, name: string, order: number): WorkflowGraphNode {
  return {
    uuid,
    name,
    type: 'material_source',
    kind: 'material_source',
    authoringOrder: order,
    disabled: false,
  }
}

function edge(uuid: string, sourceNodeUuid: string, targetNodeUuid: string): WorkflowGraphEdge {
  return { uuid, sourceNodeUuid, targetNodeUuid }
}

function control(
  uuid: string,
  name: string,
  type: 'condition' | 'repeat_until',
  order: number,
  param: Record<string, unknown> = {},
  parentUuid?: string,
): WorkflowGraphNode {
  return {
    uuid,
    name,
    type,
    // 模拟当前接口适配结果：修复前控制节点会被降级成普通动作。
    kind: 'action',
    authoringOrder: order,
    parentUuid,
    param,
    disabled: false,
  }
}

describe('WorkflowDag', () => {
  it('默认显示物料连线，将实验操作内部动作收在调用节点中', () => {
    const call = action('returned', '归还实验操作', 1)
    call.meta_data = { unilab: { composite: { child_workflow_uuid: 'child-workflow' } } }
    const { container } = render(<WorkflowDag
      nodes={[material('current', '当前瓶子', 0), call, action('moved', '内部搬运', 2, 'returned')]}
      edges={[edge('input', 'current', 'returned')]}
      loading={false} error={false}
    />)
    expect(screen.getByRole('button', { name: '物料输入：当前瓶子 → 归还实验操作' })).toBeInTheDocument()
    expect(container.querySelector('[data-node-uuid="moved"]')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '查看实验操作：归还实验操作' })).toHaveAttribute('href', '?page=workflows&workflow=child-workflow')
  })
  it('offers an explicit retry when the graph request fails', () => {
    const onRetry = vi.fn()
    render(<WorkflowDag nodes={[]} edges={[]} loading={false} error onRetry={onRetry} />)

    fireEvent.click(screen.getByRole('button', { name: '重新读取工作流图' }))

    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('projects a condition region and labels both scheduler-only branches', () => {
    const conditionUuid = 'condition'
    const nodes = [
      action('observed', '观察布尔条件', 0),
      control(conditionUuid, '条件', 'condition', 1, {
        predecessor_node_uuids: ['observed'],
        bindings: {
          observed: { kind: 'node_result', node_uuid: 'observed', result_path: ['value'] },
        },
        branches: [
          {
            label: 'if',
            condition: { field: { var: 'observed' }, name: 'value' },
            node_uuids: ['true-branch'],
            entry_node_uuids: ['true-branch'],
            exit_node_uuids: ['true-branch'],
          },
          {
            label: 'else',
            condition: null,
            node_uuids: ['false-branch'],
            entry_node_uuids: ['false-branch'],
            exit_node_uuids: ['false-branch'],
          },
        ],
      }),
      action('true-branch', '记录真分支', 2, conditionUuid),
      action('false-branch', '记录假分支', 3, conditionUuid),
    ]

    const { container } = render(<WorkflowDag nodes={nodes} edges={[]} loading={false} error={false} />)

    expect(screen.getByRole('group', { name: '条件控制域：条件' })).toHaveTextContent('记录真分支')
    expect(screen.getByRole('group', { name: '条件控制域：条件' })).toHaveTextContent('记录假分支')
    expect(screen.getByRole('button', { name: 'True 分支：观察布尔条件 → 记录真分支' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'False 分支：观察布尔条件 → 记录假分支' })).toBeInTheDocument()
    expect(container.querySelector('.workflow-dag-summary')).toHaveTextContent('0 条权威连线')
    expect(container.querySelector('.workflow-dag-summary')).toHaveTextContent('2 条调度关系')
  })

  it('projects RepeatUntil as a region with a non-authoritative continue relation', () => {
    const repeatUuid = 'repeat'
    const nodes = [
      control(repeatUuid, '重复直到', 'repeat_until', 0, {
        predecessor_node_uuids: [],
        successor_node_uuids: [],
        max_iterations: 10,
        initial_carry: { iteration: { kind: 'literal', value: 1 } },
        next_carry: {
          iteration: { kind: 'node_result', node_uuid: 'evaluate', result_path: ['next_iteration'] },
        },
        until: { field: { var: 'decision' }, name: 'done' },
        bindings: {
          decision: { kind: 'node_result', node_uuid: 'evaluate' },
        },
        node_uuids: ['prepare', 'execute', 'evaluate'],
        entry_node_uuids: ['prepare'],
        exit_node_uuids: ['evaluate'],
      }),
      action('prepare', '准备循环轮次', 1, repeatUuid),
      action('execute', '执行循环轮次', 2, repeatUuid),
      action('evaluate', '判断循环退出', 3, repeatUuid),
    ]
    const edges = [
      edge('prepare-execute', 'prepare', 'execute'),
      edge('execute-evaluate', 'execute', 'evaluate'),
    ]

    const { container } = render(<WorkflowDag nodes={nodes} edges={edges} loading={false} error={false} />)

    const region = screen.getByRole('group', { name: '循环控制域：重复直到' })
    expect(region).toHaveTextContent('最多 10 轮')
    expect(region).toHaveTextContent('准备循环轮次')
    expect(region).toHaveTextContent('执行循环轮次')
    expect(region).toHaveTextContent('判断循环退出')
    expect(screen.getByRole('button', {
      name: '继续下一轮：判断循环退出 → 准备循环轮次',
    })).toBeInTheDocument()
    expect(container.querySelector('.workflow-dag-summary')).toHaveTextContent('2 条权威连线')
    expect(container.querySelector('.workflow-dag-summary')).toHaveTextContent('1 条调度关系')
  })

  it('keeps a nested condition inside its RepeatUntil control region', () => {
    const nodes = [
      control('repeat', '重复直到', 'repeat_until', 0, {
        predecessor_node_uuids: [],
        successor_node_uuids: [],
        max_iterations: 8,
        initial_carry: { iteration: { kind: 'literal', value: 1 } },
        next_carry: {
          iteration: { kind: 'node_result', node_uuid: 'decision', result_path: ['next_iteration'] },
        },
        until: { field: { var: 'decision' }, name: 'done' },
        bindings: { decision: { kind: 'node_result', node_uuid: 'decision' } },
        node_uuids: ['decision', 'condition', 'even', 'odd'],
        entry_node_uuids: ['decision'],
        exit_node_uuids: ['condition'],
      }),
      action('decision', '判断轮次', 1, 'repeat'),
      control('condition', '判断奇偶分支', 'condition', 2, {
        predecessor_node_uuids: ['decision'],
        bindings: { is_even: { kind: 'node_result', node_uuid: 'decision', result_path: ['is_even'] } },
        branches: [
          {
            label: 'if', condition: { var: 'is_even' }, node_uuids: ['even'],
            entry_node_uuids: ['even'], exit_node_uuids: ['even'],
          },
          {
            label: 'else', condition: null, node_uuids: ['odd'],
            entry_node_uuids: ['odd'], exit_node_uuids: ['odd'],
          },
        ],
      }, 'repeat'),
      action('even', '记录偶数分支', 3, 'condition'),
      action('odd', '记录奇数分支', 4, 'condition'),
    ]

    render(<WorkflowDag nodes={nodes} edges={[]} loading={false} error={false} />)

    const repeatRegion = screen.getByRole('group', { name: '循环控制域：重复直到' })
    expect(repeatRegion).toHaveTextContent('判断轮次')
    expect(repeatRegion).toHaveTextContent('记录偶数分支')
    expect(repeatRegion).toHaveTextContent('记录奇数分支')
    expect(screen.getByRole('group', { name: '条件控制域：判断奇偶分支' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'True 分支：判断轮次 → 记录偶数分支' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'False 分支：判断轮次 → 记录奇数分支' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /继续下一轮：记录.*分支 → 判断轮次/ })).toHaveLength(2)
  })

  it('用条件节点和循环节点的业务名称展示结构控制节点', () => {
    render(<WorkflowDag nodes={[control('condition-1', '按结果分支', 'condition', 0), control('repeat-1', '重试循环', 'repeat_until', 1)]} edges={[]} loading={false} error={false} />)

    expect(screen.getByRole('group', { name: '条件控制域：按结果分支' })).toBeInTheDocument()
    expect(screen.getByRole('group', { name: '循环控制域：重试循环' })).toBeInTheDocument()
  })

  it('labels a parallel control edge and highlights both endpoints when selected', () => {
    const nodes = [
      action('setup-beaker', '烧杯准备完成', 0),
      action('setup-vial', '样品瓶准备完成', 1),
      group('photo-group', '烧杯在 S05 拍照', 2, 'parallel-scope'),
      action('photo', 'S05 烧杯拍照检测', 3, 'photo-group'),
      group('cap-group', '样品瓶在 S08 开盖', 4, 'parallel-scope'),
      action('cap', 'S08 样品瓶开关盖', 5, 'cap-group'),
      action('join', '原子取料倒液放料', 6),
    ]
    const edges = [
      edge('entry-beaker', 'setup-beaker', 'photo'),
      edge('entry-barrier', 'setup-vial', 'photo'),
      edge('entry-vial', 'setup-vial', 'cap'),
      edge('join-photo', 'photo', 'join'),
      edge('join-cap', 'cap', 'join'),
    ]
    const { container } = render(
      <WorkflowDag nodes={nodes} edges={edges} loading={false} error={false} />,
    )

    const selectedEdge = screen.getByRole('button', {
      name: '并行入口：样品瓶准备完成 → S05 烧杯拍照检测',
    })
    fireEvent.click(selectedEdge)

    expect(selectedEdge).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('article', { name: /样品瓶准备完成.*已选连线起点/ })).toBeInTheDocument()
    expect(screen.getByRole('article', { name: /S05 烧杯拍照检测.*已选连线终点/ })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('并行入口')
    expect(screen.getByRole('status')).toHaveTextContent('样品瓶准备完成 → S05 烧杯拍照检测')

    fireEvent.click(screen.getByRole('article', { name: /样品瓶准备完成.*已选连线起点/ }))
    expect(selectedEdge).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(container.querySelector('.workflow-dag-group-frame') as HTMLElement)
    expect(selectedEdge).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(container.querySelector('.workflow-dag-edges') as SVGElement)
    expect(selectedEdge).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.getByRole('article', { name: /^样品瓶准备完成/ })).not.toHaveClass('edge-source')
    expect(screen.getByRole('article', { name: /^S05 烧杯拍照检测/ })).not.toHaveClass('edge-target')

    fireEvent.click(selectedEdge)
    fireEvent.click(container.querySelector('.workflow-dag-scroll') as HTMLElement)
    expect(selectedEdge).toHaveAttribute('aria-pressed', 'false')
  })

  it('moves every member with its group and can restore automatic layout', () => {
    const nodes = [
      group('transfer-group', '组合动作', 0),
      action('pick', '取料', 1, 'transfer-group'),
      action('place', '放料', 2, 'transfer-group'),
      action('finish', '完成', 3),
    ]
    const edges = [edge('pick-place', 'pick', 'place'), edge('place-finish', 'place', 'finish')]
    const { container } = render(
      <WorkflowDag nodes={nodes} edges={edges} loading={false} error={false} />,
    )
    const pick = screen.getByRole('article', { name: /^取料/ })
    const place = screen.getByRole('article', { name: /^放料/ })
    const finish = screen.getByRole('article', { name: /^完成/ })
    const frame = container.querySelector('.workflow-dag-group-frame') as HTMLElement
    const path = container.querySelector('[data-edge-uuid="pick-place"] .workflow-dag-edge') as SVGPathElement
    const initial = {
      pickX: Number.parseFloat(pick.style.left),
      pickY: Number.parseFloat(pick.style.top),
      placeX: Number.parseFloat(place.style.left),
      placeY: Number.parseFloat(place.style.top),
      finishX: Number.parseFloat(finish.style.left),
      finishY: Number.parseFloat(finish.style.top),
      frameX: Number.parseFloat(frame.style.left),
      frameY: Number.parseFloat(frame.style.top),
      path: path.getAttribute('d'),
    }

    fireEvent.pointerDown(pick, { pointerId: 1, button: 0, clientX: 100, clientY: 100 })
    fireEvent.pointerMove(pick, { pointerId: 1, buttons: 1, clientX: 110, clientY: 110 })
    fireEvent.pointerMove(pick, { pointerId: 1, buttons: 1, clientX: 140, clientY: 125 })
    fireEvent.pointerUp(pick, { pointerId: 1, clientX: 140, clientY: 125 })

    expect(Number.parseFloat(pick.style.left)).toBe(initial.pickX + 40)
    expect(Number.parseFloat(pick.style.top)).toBe(initial.pickY + 25)
    expect(Number.parseFloat(place.style.left)).toBe(initial.placeX + 40)
    expect(Number.parseFloat(place.style.top)).toBe(initial.placeY + 25)
    expect(Number.parseFloat(finish.style.left)).toBe(initial.finishX)
    expect(Number.parseFloat(finish.style.top)).toBe(initial.finishY)
    expect(Number.parseFloat(frame.style.left)).toBe(initial.frameX + 40)
    expect(Number.parseFloat(frame.style.top)).toBe(initial.frameY + 25)
    expect(path.getAttribute('d')).not.toBe(initial.path)

    fireEvent.click(screen.getByRole('button', { name: '恢复自动布局' }))
    expect(Number.parseFloat(pick.style.left)).toBe(initial.pickX)
    expect(Number.parseFloat(place.style.left)).toBe(initial.placeX)
    expect(Number.parseFloat(frame.style.left)).toBe(initial.frameX)
  })

  it('uses the visible canvas as the drag boundary for a compact graph', () => {
    const { container } = render(
      <WorkflowDag nodes={[action('single', '单个节点', 0)]} edges={[]} loading={false} error={false} />,
    )
    const scroll = container.querySelector('.workflow-dag-scroll') as HTMLElement
    Object.defineProperty(scroll, 'clientWidth', { configurable: true, value: 800 })
    Object.defineProperty(scroll, 'clientHeight', { configurable: true, value: 360 })
    const node = screen.getByRole('article', { name: /^单个节点/ })
    const initialX = Number.parseFloat(node.style.left)
    const initialY = Number.parseFloat(node.style.top)

    fireEvent.pointerDown(node, { pointerId: 4, button: 0, clientX: 100, clientY: 100 })
    fireEvent.pointerMove(node, { pointerId: 4, buttons: 1, clientX: 1_000, clientY: 1_000 })
    fireEvent.pointerUp(node, { pointerId: 4, clientX: 1_000, clientY: 1_000 })

    expect(Number.parseFloat(node.style.left)).toBe(800 - 164)
    expect(Number.parseFloat(node.style.top)).toBe(360 - 82)
    expect(Number.parseFloat(node.style.left)).toBeGreaterThan(initialX)
    expect(Number.parseFloat(node.style.top)).toBeGreaterThan(initialY)
  })

  it('renders adjacent members of one presentation group inside one labelled frame', () => {
    const nodes = [
      group('return-group', '主物料返回 S03 起始库位', 0),
      action('return-beaker', '返回烧杯', 1, 'return-group'),
      action('return-vial', '返回样品瓶', 2, 'return-group'),
    ]
    const { container } = render(
      <WorkflowDag
        nodes={nodes}
        edges={[edge('return-beaker-vial', 'return-beaker', 'return-vial')]}
        loading={false}
        error={false}
      />,
    )

    expect(container.querySelectorAll('.workflow-dag-group-frame')).toHaveLength(1)
    expect(container.querySelectorAll('.workflow-dag-group-title')).toHaveLength(1)
    expect(screen.getByRole('group', { name: '分组：主物料返回 S03 起始库位' })).toHaveTextContent('返回烧杯')
    expect(screen.getByRole('group', { name: '分组：主物料返回 S03 起始库位' })).toHaveTextContent('返回样品瓶')
  })

  it('moves an empty group frame', () => {
    const { container } = render(
      <WorkflowDag nodes={[group('empty-group', '空分组', 0)]} edges={[]} loading={false} error={false} />,
    )
    const frame = container.querySelector('.workflow-dag-group-frame') as HTMLElement
    const initialX = Number.parseFloat(frame.style.left)
    const initialY = Number.parseFloat(frame.style.top)

    fireEvent.pointerDown(frame, { pointerId: 2, button: 0, clientX: 100, clientY: 100 })
    fireEvent.pointerMove(frame, { pointerId: 2, buttons: 1, clientX: 130, clientY: 120 })
    fireEvent.pointerUp(frame, { pointerId: 2, clientX: 130, clientY: 120 })

    expect(Number.parseFloat(frame.style.left)).toBe(initialX + 30)
    expect(Number.parseFloat(frame.style.top)).toBe(initialY + 20)
  })

  it('highlights referenced material cards only while the material badge is pressed', () => {
    const nodes = [
      material('powder', '粉料', 0),
      material('solvent', '溶剂', 1),
      material('unused', '未引用物料', 2),
      action('dose', 'S07 双粉桶注粉', 3),
    ]
    const edges = [
      edge('powder-dose', 'powder', 'dose'),
      edge('solvent-dose', 'solvent', 'dose'),
    ]
    render(<WorkflowDag nodes={nodes} edges={edges} loading={false} error={false} />)

    fireEvent.click(screen.getByRole('button', { name: '查看主流程' }))
    const badge = screen.getByRole('button', { name: '按住查看 2 个物料输入：粉料、溶剂' })
    const powder = screen.getByRole('article', { name: '粉料，物料源' })
    const solvent = screen.getByRole('article', { name: '溶剂，物料源' })
    const unused = screen.getByRole('article', { name: '未引用物料，物料源' })

    fireEvent.pointerDown(badge, { pointerId: 3, button: 0 })
    expect(powder).toHaveClass('material-preview')
    expect(solvent).toHaveClass('material-preview')
    expect(unused).not.toHaveClass('material-preview')

    fireEvent.pointerUp(badge, { pointerId: 3 })
    expect(powder).not.toHaveClass('material-preview')
    expect(solvent).not.toHaveClass('material-preview')

    fireEvent.pointerDown(badge, { pointerId: 4, button: 0 })
    fireEvent.pointerMove(badge, { pointerId: 4, buttons: 1, clientX: 100, clientY: 100 })
    expect(powder).not.toHaveClass('material-preview')
    expect(solvent).not.toHaveClass('material-preview')

    fireEvent.pointerDown(badge, { pointerId: 5, button: 0 })
    fireEvent.pointerCancel(badge, { pointerId: 5 })
    expect(powder).not.toHaveClass('material-preview')
    expect(solvent).not.toHaveClass('material-preview')
  })
})
