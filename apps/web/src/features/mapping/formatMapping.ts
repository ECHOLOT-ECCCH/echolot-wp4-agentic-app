const ELEMENT_KEYS = [
  'inSelector',
  'outElement',
  'jsonPlural',
  'namespaces',
  'textVals',
  'children',
  'copyToOutElt',
]
const TEXT_KEYS = [
  'outElement',
  'attribute',
  'inSelector',
  'constant',
  'regex',
  'regexGroup',
  'xlat',
  'excludeSelector',
  'normalizeWhitespace',
  'namespaces',
  'markers',
]

type Obj = Record<string, unknown>
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)

function order(value: Obj, keys: string[]): Obj {
  const out: Obj = {}
  for (const key of keys) if (key in value) out[key] = value[key]
  for (const key of Object.keys(value)) if (!(key in out)) out[key] = value[key]
  return out
}

function element(node: Obj): Obj {
  const ordered = order(node, ELEMENT_KEYS)
  if (Array.isArray(ordered.textVals)) {
    ordered.textVals = ordered.textVals.map((t: unknown) => (isObj(t) ? text(t) : t))
  }
  if (Array.isArray(ordered.children)) {
    ordered.children = ordered.children.map((c: unknown) => (isObj(c) ? element(c) : c))
  }
  return ordered
}

function text(node: Obj): Obj {
  const ordered = order(node, TEXT_KEYS)
  if (Array.isArray(ordered.markers)) {
    ordered.markers = ordered.markers.map((m: unknown) => (isObj(m) ? order(m, TEXT_KEYS) : m))
  }
  return ordered
}

/** JSONB storage sorts keys by length; show descriptors in reading order instead. */
export function formatMapping(spec: Obj): string {
  return JSON.stringify(element(spec), null, 2)
}
