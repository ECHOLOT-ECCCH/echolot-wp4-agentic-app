import { create } from 'zustand'

import type { UiAction } from '@/lib/types'

export type PreviewTarget =
  | { kind: 'file'; fileId: string; page?: number; bbox?: [number, number, number, number] }
  | { kind: 'document'; documentId: string; page?: number; overlaySetId?: string }
  | { kind: 'entry'; entryId: string; documentId?: string; runId?: string }
  | { kind: 'run'; runId: string }
  | { kind: 'span'; runId: string; convertedId: string; documentId: string | null }
  | { kind: 'mapping'; version?: number }
  | { kind: 'spec' }
  | { kind: 'none' }

interface PreviewState {
  target: PreviewTarget
  /** Monotonic counter so re-opening the same target still notifies viewers. */
  revision: number
  openFile: (
    fileId: string,
    page?: number,
    bbox?: PreviewTarget extends { bbox?: infer B } ? B : never,
  ) => void
  openDocument: (documentId: string, page?: number) => void
  openEntry: (entryId: string, documentId?: string, runId?: string) => void
  openRun: (runId: string) => void
  openSpan: (runId: string, convertedId: string, documentId: string | null) => void
  openMapping: (version?: number) => void
  openSpec: () => void
  apply: (action: UiAction) => void
  clear: () => void
}

export const usePreview = create<PreviewState>((set) => ({
  target: { kind: 'none' },
  revision: 0,
  openFile: (fileId, page, bbox) =>
    set((s) => ({ target: { kind: 'file', fileId, page, bbox }, revision: s.revision + 1 })),
  openDocument: (documentId, page) =>
    set((s) => ({ target: { kind: 'document', documentId, page }, revision: s.revision + 1 })),
  openEntry: (entryId, documentId, runId) =>
    set((s) => ({
      target: { kind: 'entry', entryId, documentId, runId },
      revision: s.revision + 1,
    })),
  openRun: (runId) => set((s) => ({ target: { kind: 'run', runId }, revision: s.revision + 1 })),
  openSpan: (runId, convertedId, documentId) =>
    set((s) => ({
      target: { kind: 'span', runId, convertedId, documentId },
      revision: s.revision + 1,
    })),
  openMapping: (version) =>
    set((s) => ({ target: { kind: 'mapping', version }, revision: s.revision + 1 })),
  openSpec: () => set((s) => ({ target: { kind: 'spec' }, revision: s.revision + 1 })),
  apply: (action) =>
    set((s) => {
      const next = targetFor(action)
      return next ? { target: next, revision: s.revision + 1 } : s
    }),
  clear: () => set({ target: { kind: 'none' } }),
}))

export function targetFor(action: UiAction): PreviewTarget | null {
  switch (action.type) {
    case 'open_file':
    case 'open_artifact':
      return action.file_id ? { kind: 'file', fileId: action.file_id } : null
    case 'open_page':
      return action.file_id
        ? { kind: 'file', fileId: action.file_id, page: action.page, bbox: action.bbox }
        : null
    case 'open_document':
      return action.document_id
        ? { kind: 'document', documentId: action.document_id, page: action.page }
        : null
    case 'show_overlay':
      return action.document_id
        ? {
            kind: 'document',
            documentId: action.document_id,
            page: action.page,
            overlaySetId: action.overlay_set_id,
          }
        : null
    case 'open_entry':
      return action.entry_id
        ? {
            kind: 'entry',
            entryId: action.entry_id,
            documentId: action.document_id,
            runId: action.run_id,
          }
        : null
    case 'open_run':
      return action.run_id ? { kind: 'run', runId: action.run_id } : null
    case 'open_mapping':
      return { kind: 'mapping', version: action.mapping_version }
    case 'open_spec':
      return { kind: 'spec' }
    default:
      return null
  }
}

export function isUiAction(value: unknown): value is UiAction {
  return typeof value === 'object' && value !== null && typeof (value as UiAction).type === 'string'
}
