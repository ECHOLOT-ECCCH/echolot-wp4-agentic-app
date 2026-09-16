import { describe, expect, it } from 'vitest'

import { isUiAction, targetFor, usePreview } from '@/lib/uiActions'

describe('ui actions', () => {
  it('maps actions to preview targets', () => {
    expect(targetFor({ type: 'open_file', file_id: 'f1' })).toEqual({ kind: 'file', fileId: 'f1' })
    expect(targetFor({ type: 'open_page', file_id: 'f1', page: 3, bbox: [1, 2, 3, 4] })).toEqual({
      kind: 'file',
      fileId: 'f1',
      page: 3,
      bbox: [1, 2, 3, 4],
    })
    expect(targetFor({ type: 'open_entry', entry_id: 'e1' })).toEqual({
      kind: 'entry',
      entryId: 'e1',
    })
    expect(
      targetFor({ type: 'open_entry', entry_id: 'e1', document_id: 'd1', run_id: 'r1' }),
    ).toEqual({ kind: 'entry', entryId: 'e1', documentId: 'd1', runId: 'r1' })
    expect(targetFor({ type: 'open_run', run_id: 'r1' })).toEqual({ kind: 'run', runId: 'r1' })
    expect(
      targetFor({ type: 'show_overlay', document_id: 'd1', page: 3, overlay_set_id: 's1' }),
    ).toEqual({ kind: 'document', documentId: 'd1', page: 3, overlaySetId: 's1' })
    expect(targetFor({ type: 'open_spec' })).toEqual({ kind: 'spec' })
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
