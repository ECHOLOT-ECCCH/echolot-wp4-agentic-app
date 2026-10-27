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

export function formatCount(n: number, noun: string): string {
  return `${n.toLocaleString()} ${noun}${n === 1 ? '' : 's'}`
}

export function languageFor(file: {
  mime: string | null
  name: string
}): 'json' | 'markdown' | 'python' | 'text' {
  const name = file.name.toLowerCase()
  if (file.mime === 'application/json' || name.endsWith('.json')) return 'json'
  if (name.endsWith('.md')) return 'markdown'
  if (file.mime === 'text/x-python' || name.endsWith('.py')) return 'python'
  return 'text' // .txt, .jsonl, .csv and anything else
}

/** One cell of a records table: strings as they are, everything else as compact JSON. */
export function cellText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}
