export interface User {
  id: string
  email: string
  name: string
  role: 'user' | 'admin'
  created_at: string
  last_login_at: string | null
}

export type SourceType = 'xml' | 'pdf'
export type ProjectRole = 'owner' | 'editor' | 'viewer'

export interface ModelSpec {
  provider: string
  model_id: string
  credential_id?: string | null
}

export interface Project {
  id: string
  owner_id: string
  name: string
  description: string | null
  source_type: SourceType
  default_model: ModelSpec | null
  settings: Record<string, unknown>
  created_at: string
  updated_at: string
  my_role: ProjectRole
}

export type FileKind = 'source' | 'derived' | 'output' | 'artifact'

export interface FileRecord {
  id: string
  project_id: string
  kind: FileKind
  name: string
  size: number
  sha256: string
  mime: string
  meta: Record<string, unknown>
  created_by: string | null
  created_at: string
}

export interface Member {
  user_id: string
  email: string
  name: string
  role: ProjectRole
}

export interface Provider {
  name: string
  label: string
  needs_api_key: boolean
  needs_base_url: boolean
  docs_url: string
}

export interface Credential {
  id: string
  provider: string
  label: string
  base_url: string | null
  key_hint: string
  declared_capabilities: Record<string, unknown>
  last_used_at: string | null
  created_at: string
}

export interface ModelInfo {
  id: string
  label: string
  provider: string
  supports_tools: boolean | null
  supports_vision: boolean | null
  context_length: number | null
  price_in_per_m: number | null
  price_out_per_m: number | null
  recommended: boolean
}

export interface ProbeResult {
  ok: boolean
  model_id: string
  latency_ms: number
  message: string
  input_tokens: number
  output_tokens: number
  reply: string
}

export interface Conversation {
  id: string
  project_id: string
  title: string
  created_by: string | null
  created_at: string
  updated_at: string
}

export type UiActionType =
  | 'open_file'
  | 'open_entry'
  | 'open_page'
  | 'show_overlay'
  | 'open_mapping'
  | 'open_run'
  | 'open_spec'
  | 'open_artifact'

export interface UiAction {
  type: UiActionType
  file_id?: string
  entry_id?: string
  document_id?: string
  page?: number
  bbox?: [number, number, number, number]
  overlay_set_id?: string
  mapping_version?: number
  run_id?: string
}

export interface ToolResultPayload {
  summary?: string
  data?: Record<string, unknown>
  artifacts?: { file_id: string; name: string; kind: string }[]
  cost?: Record<string, unknown>
}
