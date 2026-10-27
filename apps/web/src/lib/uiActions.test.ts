import { describe, expect, it } from 'vitest'

import { isUiAction, targetFor, usePreview } from '@/lib/uiActions'

describe('ui actions', () => {
  it('maps actions to preview targets', () => {
    expect(targetFor({ type: 'open_file', file_id: 'f1' })).toEqual({ kind: 'file', fileId: 'f1' })
    expect(targetFor({ type: 'open_file', file_id: 'f1', row: 0 })).toEqual({
      kind: 'file',
      fileId: 'f1',
      row: 0,
    })
    expect(targetFor({ type: 'open_task' })).toEqual({ kind: 'task' })
    expect(targetFor({ type: 'open_script', script_version: 3 })).toEqual({
      kind: 'script',
      version: 3,
    })
    expect(targetFor({ type: 'open_run', run_id: 'r1', row: 7 })).toEqual({
      kind: 'run',
      runId: 'r1',
      row: 7,
    })
    expect(targetFor({ type: 'open_file' })).toBeNull()
  })

  it('applies actions to the store and bumps the revision', () => {
    const before = usePreview.getState().revision
    usePreview.getState().apply({ type: 'open_file', file_id: 'f9' })
    expect(usePreview.getState().target).toEqual({ kind: 'file', fileId: 'f9' })
    expect(usePreview.getState().revision).toBe(before + 1)
    usePreview.getState().apply({ type: 'open_file', file_id: 'f9' })
    expect(usePreview.getState().revision).toBe(before + 2)
  })

  it('recognises action payloads', () => {
    expect(isUiAction({ type: 'open_file' })).toBe(true)
    expect(isUiAction({})).toBe(false)
    expect(isUiAction(null)).toBe(false)
  })
})
