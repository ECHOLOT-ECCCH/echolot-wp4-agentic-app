/** Shapes of the API's responses (apps/server/src/echolot_app/schemas.py). */

export interface Workspace {
  id: string
  name: string
  description: string | null
  created_at: string
  updated_at: string
}

export type FileKind = 'input' | 'result' | 'export'

export interface FileMeta {
  format?: 'csv' | 'tsv' | 'json' | 'jsonl' | null
  record_count?: number
  columns?: string[]
  encoding?: string
  dialect?: { delimiter: string; quotechar: string }
  json_path?: string
  ragged_rows?: number
  invalid_count?: number
  error?: string
  [key: string]: unknown
}

export interface FileRecord {
  id: string
  workspace_id: string
  kind: FileKind
  name: string
  size: number
  sha256: string | null
  mime: string | null
  meta: FileMeta
  created_at: string
}

export interface RecordRow {
  row: number
  record: Record<string, unknown>
}

export interface RecordsPage {
  total: number
  offset: number
  columns: string[]
  records: RecordRow[]
}

export interface TaskNotes {
  markdown: string
  updated_at: string
}

export interface Conversation {
  id: string
  workspace_id: string
  title: string
  created_at: string
  updated_at: string
}

export type UiActionType = 'open_file' | 'open_task' | 'open_script' | 'open_run'

export interface UiAction {
  type: UiActionType
  file_id?: string
  script_version?: number
  run_id?: string
  row?: number
}

export interface ToolResultPayload {
  summary?: string
  data?: Record<string, unknown>
  artifacts?: { file_id: string; name: string; kind: string }[]
  cost?: Record<string, unknown>
}

export function isRecordsFile(file: FileRecord): boolean {
  return Boolean(file.meta.format) && typeof file.meta.record_count === 'number'
}
