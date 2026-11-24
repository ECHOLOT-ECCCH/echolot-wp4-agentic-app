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

export interface ScriptSummary {
  id: string
  version: number
  note: string | null
  author: 'agent' | 'user'
  services: string[]
  sha256: string
  created_at: string
}

export interface ScriptRecord extends ScriptSummary {
  workspace_id: string
  code: string
}

export type RunStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

export interface RunRecord {
  id: string
  workspace_id: string
  script_version: number | null
  services: string[]
  input_file_id: string
  input_file_name: string | null
  parent_run_id: string | null
  record_limit: number | null
  selected_rows: number | null
  concurrency: number
  script_params: Record<string, unknown>
  purpose: string | null
  status: RunStatus
  executor: string
  progress: { done?: number; total?: number; ok?: number; error?: number }
  counts: { total?: number; ok?: number; error?: number }
  result_file_id: string | null
  error: string | null
  submitted_by: 'agent' | 'user'
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface RunRow {
  row: number
  status: 'ok' | 'error' | null
  error: string | null
  ms: number | null
  input: Record<string, unknown>
  output: Record<string, unknown>
  decision: { qid: string | null; note?: string } | null
}

export interface RunRowsPage {
  total: number
  offset: number
  run_status: RunStatus
  input_columns: string[]
  output_columns: string[]
  rows: RunRow[]
}

export function isTerminal(status: RunStatus): boolean {
  return status === 'done' || status === 'failed' || status === 'cancelled'
}

// --- accounts and models (docs/PLAN.md 7.11) ---------------------------------------------------

export interface User {
  id: string
  email: string
  name: string
  is_admin: boolean
  is_active: boolean
  created_at: string
  last_login_at: string | null
}

/** A new account or a reset: the temporary password is shown once. */
export interface UserWithPassword {
  user: User
  password: string | null
}

export interface Vendor {
  name: string
  label: string
  needs_key: boolean
  needs_base_url: boolean
  key_url: string
  suggested: { id: string; label: string }[]
  has_key: boolean
  key_hint: string
  base_url: string | null
  key_updated_at: string | null
  key_last_used_at: string | null
}

export interface ModelInfo {
  id: string
  label: string
  suggested: boolean
}

export interface ModelListing {
  models: ModelInfo[]
  error: string | null
}

export interface ModelChoice {
  provider: string
  model_id: string
  source: 'server' | 'own'
  server_model: { provider: string; model_id: string } | null
  ready: boolean
  problem: string | null
}

export interface ProbeResult {
  ok: boolean
  model_id: string
  latency_ms: number
  message: string
}
