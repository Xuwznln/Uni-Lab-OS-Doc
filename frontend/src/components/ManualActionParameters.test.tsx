import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ManualActionParameters, parseManualParameters } from './ManualActionParameters'

describe('无参数单点动作', () => {
  const action = { parameter_schema: { type: 'object', properties: {} }, defaults: {} }
  it('明确为空的参数合同不展示 JSON 输入框，并忽略残留的原始参数', () => {
    render(<ManualActionParameters action={action} drafts={{}} raw={'{"old":1}'} disabled={false} onDraft={vi.fn()} onRaw={vi.fn()} />)
    expect(screen.getByText('此动作无需输入参数。')).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(parseManualParameters(action, {}, '{"old":1}')).toEqual({})
  })
  it('未提供字段合同仍保留原始参数解析', () => {
    expect(parseManualParameters({ parameter_schema: { type: 'object' }, defaults: {} }, {}, '{"value":2}')).toEqual({ value: 2 })
  })
})
