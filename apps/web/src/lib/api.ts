export class ApiError extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
    this.detail = detail
  }
}

export function readCookie(name: string): string | undefined {
  const prefix = `${name}=`
  return document.cookie
    .split(';')
    .map((c) => c.trim())
    .find((c) => c.startsWith(prefix))
    ?.slice(prefix.length)
}

export function csrfToken(): string {
  return readCookie('elx_csrf') ?? ''
}

export function loginUrl(next?: string): string {
  const target = next ?? `${window.location.pathname}${window.location.search}`
  return `/auth/login?next=${encodeURIComponent(target)}`
}

export function redirectToLogin(): void {
  window.location.assign(loginUrl())
}

async function detailOf(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
    if (body.detail) return JSON.stringify(body.detail)
  } catch {
    /* not JSON */
  }
  return response.statusText || `HTTP ${response.status}`
}

export interface ApiOptions extends Omit<RequestInit, 'body'> {
  json?: unknown
  body?: BodyInit | null
  /** Skip the automatic redirect to login on 401 (used by useMe). */
  noRedirect?: boolean
}

export async function api<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const { json, noRedirect, headers, ...rest } = options
  const method = (rest.method ?? 'GET').toUpperCase()
  const init: RequestInit = {
    credentials: 'same-origin',
    ...rest,
    method,
    headers: {
      Accept: 'application/json',
      ...(json !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(method !== 'GET' && method !== 'HEAD' ? { 'X-CSRF-Token': csrfToken() } : {}),
      ...(headers as Record<string, string> | undefined),
    },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  }
  const response = await fetch(path, init)
  if (response.status === 401 && !noRedirect) {
    redirectToLogin()
    throw new ApiError(401, 'Not authenticated')
  }
  if (!response.ok) {
    throw new ApiError(response.status, await detailOf(response))
  }
  if (response.status === 204) return undefined as T
  const contentType = response.headers.get('content-type') ?? ''
  if (contentType.includes('application/json')) return (await response.json()) as T
  return (await response.text()) as unknown as T
}

export interface UploadHandle {
  promise: Promise<unknown>
  abort: () => void
}

/** Raw-body upload with progress (XHR, because fetch has no upload progress). */
export function uploadFile(
  projectId: string,
  file: File,
  onProgress: (fraction: number) => void,
  kind: 'source' | 'derived' = 'source',
): UploadHandle {
  const xhr = new XMLHttpRequest()
  const url = `/api/projects/${projectId}/files?name=${encodeURIComponent(file.name)}&kind=${kind}`
  const promise = new Promise<unknown>((resolve, reject) => {
    xhr.open('POST', url)
    xhr.setRequestHeader('X-CSRF-Token', csrfToken())
    xhr.setRequestHeader('X-File-Name', encodeURIComponent(file.name))
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream')
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total)
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress(1)
        resolve(JSON.parse(xhr.responseText))
      } else {
        let detail = xhr.statusText
        try {
          detail = (JSON.parse(xhr.responseText) as { detail?: string }).detail ?? detail
        } catch {
          /* ignore */
        }
        reject(new ApiError(xhr.status, detail))
      }
    }
    xhr.onerror = () => reject(new ApiError(0, 'Network error during upload'))
    xhr.onabort = () => reject(new ApiError(0, 'Upload cancelled'))
    xhr.send(file)
  })
  return { promise, abort: () => xhr.abort() }
}

export function fileDownloadUrl(projectId: string, fileId: string, inline = false): string {
  return `/api/projects/${projectId}/files/${fileId}/download${inline ? '?inline=true' : ''}`
}

export function fileContentUrl(projectId: string, fileId: string): string {
  return `/api/projects/${projectId}/files/${fileId}/content`
}
