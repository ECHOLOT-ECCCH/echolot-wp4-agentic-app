import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/lib/api'
import type {
  Conversation,
  Credential,
  DocumentDetail,
  DocumentRecord,
  EntryDetail,
  EntryPage,
  FileRecord,
  Member,
  ModelInfo,
  ModelSpec,
  ProbeResult,
  Project,
  Provider,
  SourceType,
  User,
} from '@/lib/types'

export const keys = {
  me: ['me'] as const,
  projects: ['projects'] as const,
  project: (id: string) => ['projects', id] as const,
  files: (projectId: string) => ['projects', projectId, 'files'] as const,
  documents: (projectId: string) => ['projects', projectId, 'documents'] as const,
  document: (projectId: string, documentId: string) =>
    ['projects', projectId, 'documents', documentId] as const,
  entries: (projectId: string, documentId: string, offset: number, limit: number, q: string) =>
    ['projects', projectId, 'documents', documentId, 'entries', { offset, limit, q }] as const,
  entry: (projectId: string, entryId: string) =>
    ['projects', projectId, 'entries', entryId] as const,
  members: (projectId: string) => ['projects', projectId, 'members'] as const,
  conversations: (projectId: string) => ['projects', projectId, 'conversations'] as const,
  messages: (projectId: string, conversationId: string) =>
    ['projects', projectId, 'conversations', conversationId, 'messages'] as const,
  providers: ['providers'] as const,
  credentials: ['credentials'] as const,
  models: (credentialId: string) => ['models', credentialId] as const,
}

export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: () => api<User>('/api/me', { noRedirect: true }),
    retry: false,
    staleTime: 5 * 60_000,
  })
}

export function useProjects() {
  return useQuery({ queryKey: keys.projects, queryFn: () => api<Project[]>('/api/projects') })
}

export function useProject(projectId: string) {
  return useQuery({
    queryKey: keys.project(projectId),
    queryFn: () => api<Project>(`/api/projects/${projectId}`),
  })
}

export function useCreateProject() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { name: string; source_type: SourceType; description?: string }) =>
      api<Project>('/api/projects', { method: 'POST', json: body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.projects }),
  })
}

export function useUpdateProject(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (
      body: Partial<Pick<Project, 'name' | 'description' | 'default_model' | 'settings'>>,
    ) => api<Project>(`/api/projects/${projectId}`, { method: 'PATCH', json: body }),
    onSuccess: (project) => {
      queryClient.setQueryData(keys.project(projectId), project)
      void queryClient.invalidateQueries({ queryKey: keys.projects })
    },
  })
}

export function useFiles(projectId: string) {
  return useQuery({
    queryKey: keys.files(projectId),
    queryFn: () => api<FileRecord[]>(`/api/projects/${projectId}/files`),
  })
}

export function useDeleteFile(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (fileId: string) =>
      api<void>(`/api/projects/${projectId}/files/${fileId}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.files(projectId) }),
  })
}

export function useDocuments(projectId: string) {
  return useQuery({
    queryKey: keys.documents(projectId),
    queryFn: () => api<DocumentRecord[]>(`/api/projects/${projectId}/documents`),
    refetchInterval: (query) =>
      query.state.data?.some((d) => d.status === 'queued' || d.status === 'parsing') ? 3000 : false,
  })
}

export function useDocument(projectId: string, documentId: string | null) {
  return useQuery({
    queryKey: keys.document(projectId, documentId ?? ''),
    queryFn: () => api<DocumentDetail>(`/api/projects/${projectId}/documents/${documentId}`),
    enabled: Boolean(documentId),
  })
}

export function useEntries(
  projectId: string,
  documentId: string | null,
  params: { offset: number; limit: number; q: string },
) {
  const search = new URLSearchParams({
    offset: String(params.offset),
    limit: String(params.limit),
  })
  if (params.q) search.set('q', params.q)
  return useQuery({
    queryKey: keys.entries(projectId, documentId ?? '', params.offset, params.limit, params.q),
    queryFn: () =>
      api<EntryPage>(`/api/projects/${projectId}/documents/${documentId}/entries?${search}`),
    enabled: Boolean(documentId),
    placeholderData: (previous) => previous,
  })
}

export function useEntry(projectId: string, entryId: string | null) {
  return useQuery({
    queryKey: keys.entry(projectId, entryId ?? ''),
    queryFn: () => api<EntryDetail>(`/api/projects/${projectId}/entries/${entryId}`),
    enabled: Boolean(entryId),
    staleTime: 5 * 60_000,
  })
}

export function useReparseDocument(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      documentId,
      entry_xpath,
      headword_xpath,
    }: {
      documentId: string
      entry_xpath?: string | null
      headword_xpath?: string | null
    }) =>
      api<DocumentRecord>(`/api/projects/${projectId}/documents/${documentId}/reparse`, {
        method: 'POST',
        json: { entry_xpath, headword_xpath },
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.documents(projectId) }),
  })
}

export function useMembers(projectId: string) {
  return useQuery({
    queryKey: keys.members(projectId),
    queryFn: () => api<Member[]>(`/api/projects/${projectId}/members`),
  })
}

export function useConversations(projectId: string) {
  return useQuery({
    queryKey: keys.conversations(projectId),
    queryFn: () => api<Conversation[]>(`/api/projects/${projectId}/conversations`),
  })
}

export function useCreateConversation(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (title?: string) =>
      api<Conversation>(`/api/projects/${projectId}/conversations`, {
        method: 'POST',
        json: { title: title ?? null },
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.conversations(projectId) }),
  })
}

export function useProviders() {
  return useQuery({
    queryKey: keys.providers,
    queryFn: () => api<Provider[]>('/api/providers'),
    staleTime: Infinity,
  })
}

export function useCredentials() {
  return useQuery({
    queryKey: keys.credentials,
    queryFn: () => api<Credential[]>('/api/me/credentials'),
  })
}

export function useCreateCredential() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: {
      provider: string
      label: string
      api_key?: string
      base_url?: string
      declared_capabilities?: Record<string, unknown>
    }) => api<Credential>('/api/me/credentials', { method: 'POST', json: body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.credentials }),
  })
}

export function useDeleteCredential() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api<void>(`/api/me/credentials/${id}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.credentials }),
  })
}

export function useTestCredential() {
  return useMutation({
    mutationFn: ({ id, modelId }: { id: string; modelId?: string }) =>
      api<ProbeResult>(`/api/me/credentials/${id}/test`, {
        method: 'POST',
        json: { model_id: modelId ?? null },
      }),
  })
}

export function useModels(credentialId: string | null | undefined) {
  return useQuery({
    queryKey: keys.models(credentialId ?? ''),
    queryFn: () => api<ModelInfo[]>(`/api/models?credential_id=${credentialId}`),
    enabled: Boolean(credentialId),
    staleTime: 30 * 60_000,
  })
}

export function describeModel(spec: ModelSpec | null | undefined): string {
  if (!spec) return 'No model selected'
  return spec.model_id
}
