export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`
}

export function formatDate(iso: string): string {
  const date = new Date(iso)
  return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function formatPrice(perMillion: number | null): string {
  if (perMillion === null) return ''
  if (perMillion === 0) return 'free'
  return `$${perMillion < 1 ? perMillion.toFixed(2) : perMillion.toFixed(1)}/M`
}

export function isPdf(file: { mime: string; name: string }): boolean {
  return file.mime === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')
}

export function languageFor(file: {
  mime: string
  name: string
}): 'xml' | 'json' | 'markdown' | 'text' {
  const name = file.name.toLowerCase()
  if (file.mime.includes('xml') || name.endsWith('.xml') || name.endsWith('.tei')) return 'xml'
  if (file.mime.includes('json') || name.endsWith('.json')) return 'json'
  if (name.endsWith('.md')) return 'markdown'
  return 'text'
}
