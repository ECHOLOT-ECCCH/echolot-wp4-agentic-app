export class ApiError extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
    this.detail = detail
  }
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
  /** Return the body as text whatever its content type (file contents: a .json artifact must
   *  not be parsed into an object). */
  asText?: boolean
}

export async function api<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const { json, asText, headers, ...rest } = options
  const method = (rest.method ?? 'GET').toUpperCase()
  const init: RequestInit = {
    credentials: 'same-origin',
    ...rest,
    method,
    headers: {
      Accept: asText ? '*/*' : 'application/json',
      ...(json !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(headers as Record<string, string> | undefined),
    },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  }
  const response = await fetch(path, init)
  if (!response.ok) {
    throw new ApiError(response.status, await detailOf(response))
  }
  if (response.status === 204) return undefined as T
  if (asText) return (await response.text()) as unknown as T
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
  workspaceId: string,
  file: File,
  onProgress: (fraction: number) => void,
): UploadHandle {
  const xhr = new XMLHttpRequest()
  const url = `/api/workspaces/${workspaceId}/files?name=${encodeURIComponent(file.name)}`
  const promise = new Promise<unknown>((resolve, reject) => {
    xhr.open('POST', url)
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

export function fileDownloadUrl(workspaceId: string, fileId: string, inline = false): string {
  return `/api/workspaces/${workspaceId}/files/${fileId}/download${inline ? '?inline=true' : ''}`
}

export function fileContentUrl(workspaceId: string, fileId: string): string {
  return `/api/workspaces/${workspaceId}/files/${fileId}/content`
}
