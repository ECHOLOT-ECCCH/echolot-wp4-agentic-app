import { create } from 'zustand'

import type { UiAction } from '@/lib/types'

export type PreviewTarget =
  | { kind: 'file'; fileId: string; row?: number }
  | { kind: 'task' }
  | { kind: 'script'; version?: number }
  | { kind: 'run'; runId: string; row?: number }
  | { kind: 'none' }

interface PreviewState {
  target: PreviewTarget
  /** Monotonic counter so re-opening the same target still notifies viewers. */
  revision: number
  open: (target: PreviewTarget) => void
  openFile: (fileId: string, row?: number) => void
  apply: (action: UiAction) => void
  clear: () => void
}

export const usePreview = create<PreviewState>((set) => ({
  target: { kind: 'none' },
  revision: 0,
  open: (target) => set((s) => ({ target, revision: s.revision + 1 })),
  openFile: (fileId, row) =>
    set((s) => ({
      target: row === undefined ? { kind: 'file', fileId } : { kind: 'file', fileId, row },
      revision: s.revision + 1,
    })),
  apply: (action) =>
    set((s) => {
      const next = targetFor(action)
      return next ? { target: next, revision: s.revision + 1 } : s
    }),
  clear: () => set({ target: { kind: 'none' } }),
}))

const withRow = <T extends PreviewTarget>(target: T, row: number | undefined): T =>
  row === undefined || row === null ? target : { ...target, row }

export function targetFor(action: UiAction): PreviewTarget | null {
  switch (action.type) {
    case 'open_file':
      return action.file_id
        ? withRow({ kind: 'file' as const, fileId: action.file_id }, action.row)
        : null
    case 'open_task':
      return { kind: 'task' }
    case 'open_script':
      return action.script_version
        ? { kind: 'script', version: action.script_version }
        : { kind: 'script' }
    case 'open_run':
      return action.run_id
        ? withRow({ kind: 'run' as const, runId: action.run_id }, action.row)
        : null
    default:
      return null
  }
}

export function isUiAction(value: unknown): value is UiAction {
  return typeof value === 'object' && value !== null && typeof (value as UiAction).type === 'string'
}
