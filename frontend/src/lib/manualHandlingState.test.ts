import { beforeEach, describe, expect, it } from 'vitest'
import { loadManualHandlingDraft, manualDraftStorageKey, saveManualHandlingDraft } from './manualHandlingState'

beforeEach(() => sessionStorage.clear())

describe('人工处置输入草稿', () => {
  const draft = { deviceId: 'device-1', actionId: 'action-1', drafts: { duration: '12' }, raw: '{"duration":12}', asJson: true, summary: '等待动作结束' }

  it('草稿按 API 地址和人工处置会话隔离', () => {
    sessionStorage.setItem(manualDraftStorageKey('session-1', 'http://another-station/api/v1'), JSON.stringify(draft))
    expect(loadManualHandlingDraft('session-1', '服务器说明').summary).toBe('服务器说明')
    saveManualHandlingDraft('session-1', draft)
    expect(loadManualHandlingDraft('session-1', '')).toEqual(draft)
    expect(loadManualHandlingDraft('session-2', '另一会话说明')).toMatchObject({ deviceId: '', actionId: '', summary: '另一会话说明' })
  })

  it('损坏的存储使用服务端说明，不影响打开处置面板', () => {
    for (const value of ['{', JSON.stringify({ ...draft, drafts: { duration: 12 } })]) {
      sessionStorage.setItem(manualDraftStorageKey('session-1'), value)
      expect(loadManualHandlingDraft('session-1', '服务器说明')).toMatchObject({ deviceId: '', summary: '服务器说明' })
    }
  })

  it('恢复数据仅包含输入字段，不带回持久化的人工确认', () => {
    sessionStorage.setItem(manualDraftStorageKey('session-1'), JSON.stringify({ ...draft, operator_confirmed: true, confirmed: true }))
    expect(loadManualHandlingDraft('session-1', '')).toEqual(draft)
  })
})
