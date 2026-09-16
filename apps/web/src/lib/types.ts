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

export type DocumentStatus = 'queued' | 'parsing' | 'ready' | 'error'

export interface EntryCandidate {
  xpath: string
  tag: string
  count: number
  depth: number
  score: number
  reason: string
}

export interface DocumentRecord {
  id: string
  project_id: string
  source_file_id: string
  file_name: string
  type: 'xml' | 'pdf'
  status: DocumentStatus
  entry_xpath: string | null
  headword_xpath: string | null
  entry_count: number
  page_count: number | null
  error: string | null
  parsed_at: string | null
  created_at: string
  updated_at: string
}

export interface DocumentDetail extends DocumentRecord {
  stats: {
    size_bytes?: number
    root_tag?: string
    namespaces?: Record<string, string>
    total_elements?: number
    entry_candidates?: EntryCandidate[]
    element_stats?: { tag: string; count: number; depth: number; has_text: boolean }[]
    page_count?: number
    scanned_pages?: number
    text_chars?: number
    lines?: number
    segmentation?: {
      set_id: string
      segmenter: string
      params: Record<string, unknown>
      stats: {
        pages: number
        spans: number
        continuations: number
        mode: string
        warnings: string[]
        confidence_mean: number
      }
      at: string
    }
  }
}

export type AnnotationStatus = 'proposed' | 'accepted' | 'rejected' | 'edited'

export interface AnnotationRecord {
  id: string
  document_id: string
  page_no: number
  bbox: [number, number, number, number]
  label: string
  entry_span_id: string | null
  source: 'rule' | 'ml' | 'user'
  status: AnnotationStatus
  payload: {
    bboxes?: [number, number, number, number][]
    line_ids?: string[]
    headword_guess?: string | null
    confidence?: number
    continuation?: boolean
    text?: string
    original_bbox?: [number, number, number, number]
  }
  set_id: string | null
  position: number
  created_by: string | null
  created_at: string
  updated_at: string
}

export interface AnnotationPage {
  items: AnnotationRecord[]
  total: number
  offset: number
  limit: number
}

export interface SegmentResult {
  document_id: string
  queued: boolean
  job_id: number | null
  set_id: string | null
  stats: { spans: number; mode: string; warnings: string[] } | null
}

export interface PdfPageRecord {
  id: string
  document_id: string
  page_no: number
  width: number
  height: number
  rotation: number
  text_chars: number
  is_scanned: boolean
  line_count: number
}

export interface EntryListItem {
  id: string
  document_id: string
  ordinal: number
  headword: string | null
  snippet: string
}

export interface EntryPage {
  items: EntryListItem[]
  total: number
  offset: number
  limit: number
}

export interface EntryDetail extends EntryListItem {
  source_xml: string
  source_ref: { start: number; end: number }
}

export type RunKind = 'sample' | 'full'
export type RunStatus = 'queued' | 'running' | 'done' | 'failed'

export interface RunRecord {
  id: string
  project_id: string
  document_id: string | null
  kind: RunKind
  mapping_version: number
  status: RunStatus
  scope: {
    ordinals?: number[]
    strategy?: string | null
    query?: string | null
    structurer?: string
    span_ids?: string[]
    options?: Record<string, unknown>
  }
  stats: {
    phase?: string
    done?: number
    total?: number | null
    requested?: number
    converted?: number
    skipped?: number
    with_issues?: number
    errors?: number
    warnings?: number
    resource_issues?: number
    homographs_numbered?: number
    output_files?: { file_id: string; name: string; format: string; size: number }[]
    report_file_id?: string
    timings?: Record<string, number>
  }
  output_file_id: string | null
  error: string | null
  created_at: string
  finished_at: string | null
}

export interface EntryIssue {
  severity: 'error' | 'warning'
  source: 'schema' | 'consistency' | 'writer' | 'structurer'
  code: string
  path: string
  message: string
}

export interface RunDetail extends RunRecord {
  validation_report: {
    summary?: string
    valid?: boolean
    error_count?: number
    limitation_count?: number
    resource_issues?: EntryIssue[]
    plan_warnings?: string[]
    resource_scalars?: Record<string, unknown>
    issue_codes?: Record<string, number>
    entries_with_issues?: number
    errors?: { output_index: number | null; ordinal: number | null; path: string; reason: string }[]
  }
}

export interface ConvertedEntryItem {
  id: string
  run_id: string
  entry_id: string | null
  annotation_id: string | null
  position: number
  ordinal: number | null
  headword: string | null
  issue_count: number
  issues: EntryIssue[]
  confidence: number | null
  notes: string[]
}

export interface ConvertedEntryPage {
  items: ConvertedEntryItem[]
  total: number
  offset: number
  limit: number
}

export interface ConvertedEntryDetail extends ConvertedEntryItem {
  dmlex: Record<string, unknown>
  xml: string
  mapping_version: number
  run_created_at: string
}

export interface MappingIssue {
  severity: 'error' | 'warning' | 'info'
  path: string
  message: string
}

export interface MappingSummary {
  id: string
  version: number
  note: string
  source: string
  created_by: string | null
  created_at: string
  issue_count: number
}

export interface MappingRecord extends MappingSummary {
  spec: Record<string, unknown>
  issues: MappingIssue[]
  summary: string
}

export interface MappingCheck {
  ok: boolean
  issues: MappingIssue[]
  summary: string | null
}

export interface SpecRecord {
  version: number
  markdown: string
  updated_by: string | null
  created_at: string
}

export type UiActionType =
  | 'open_file'
  | 'open_document'
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
