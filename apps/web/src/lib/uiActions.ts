import { create } from 'zustand'

import type { UiAction } from '@/lib/types'

export type PreviewTarget =
  | { kind: 'file'; fileId: string; page?: number; bbox?: [number, number, number, number] }
  | { kind: 'document'; documentId: string }
  | { kind: 'entry'; entryId: string; documentId?: string; runId?: string }
  | { kind: 'run'; runId: string }
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
  openDocument: (documentId: string) => void
  openEntry: (entryId: string, documentId?: string, runId?: string) => void
  openRun: (runId: string) => void
  apply: (action: UiAction) => void
  clear: () => void
}

export const usePreview = create<PreviewState>((set) => ({
  target: { kind: 'none' },
  revision: 0,
  openFile: (fileId, page, bbox) =>
    set((s) => ({ target: { kind: 'file', fileId, page, bbox }, revision: s.revision + 1 })),
  openDocument: (documentId) =>
    set((s) => ({ target: { kind: 'document', documentId }, revision: s.revision + 1 })),
  openEntry: (entryId, documentId, runId) =>
    set((s) => ({
      target: { kind: 'entry', entryId, documentId, runId },
      revision: s.revision + 1,
    })),
  openRun: (runId) => set((s) => ({ target: { kind: 'run', runId }, revision: s.revision + 1 })),
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
      return action.document_id ? { kind: 'document', documentId: action.document_id } : null
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
