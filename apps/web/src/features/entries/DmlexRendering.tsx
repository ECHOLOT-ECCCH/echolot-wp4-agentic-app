import type { ReactNode } from 'react'

type Obj = Record<string, unknown>

const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)
const isObjList = (v: unknown): v is Obj[] => Array.isArray(v) && v.length > 0 && v.every(isObj)
const scalar = (v: unknown): string =>
  typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean' ? String(v) : ''

/** DMLex names the XML uses (singular), for the plural keys of the JSON serialisation. */
const SINGULAR: Record<string, string> = {
  partsOfSpeech: 'partOfSpeech',
  labels: 'label',
  pronunciations: 'pronunciation',
  transcriptions: 'transcription',
  inflectedForms: 'inflectedForm',
  senses: 'sense',
  definitions: 'definition',
  examples: 'example',
  headwordTranslations: 'headwordTranslation',
  headwordExplanations: 'headwordExplanation',
  exampleTranslations: 'exampleTranslation',
  relations: 'relation',
  members: 'member',
  etymologies: 'etymology',
  etymons: 'etymon',
  etymonUnits: 'etymonUnit',
  placeholderMarkers: 'placeholderMarker',
  headwordMarkers: 'headwordMarker',
  itemMarkers: 'itemMarker',
}
const singular = (key: string): string =>
  SINGULAR[key] ?? (key.endsWith('ies') ? `${key.slice(0, -3)}y` : key.replace(/s$/, ''))

/** Fields shown first, in this order; anything else follows in the order of the data. */
const FIRST = [
  'headword',
  'homographNumber',
  'indicator',
  'text',
  'tag',
  'partsOfSpeech',
  'labels',
  'pronunciations',
  'transcriptions',
  'inflectedForms',
  'definitions',
  'headwordTranslations',
  'headwordExplanations',
  'examples',
  'exampleTranslations',
  'senses',
]
/** Scalars that qualify the object's text and sit next to it as small tags. */
const QUALIFIERS = new Set([
  'langCode',
  'scheme',
  'definitionType',
  'listingOrder',
  'sourceIdentity',
  'sourceElaboration',
  'soundFile',
  'type',
])

function orderedKeys(obj: Obj): string[] {
  const keys = Object.keys(obj).filter((k) => k !== 'id')
  const rank = (k: string) => {
    const i = FIRST.indexOf(k)
    return i < 0 ? FIRST.length : i
  }
  return keys.sort((a, b) => rank(a) - rank(b) || keys.indexOf(a) - keys.indexOf(b))
}

function Id({ value }: { value: unknown }) {
  const id = scalar(value)
  if (!id) return null
  return (
    <code className="text-muted-foreground ml-auto shrink-0 text-[11px]" data-testid="dmlex-id">
      {id}
    </code>
  )
}

function Name({ children }: { children: ReactNode }) {
  return (
    <span
      className="text-muted-foreground w-36 shrink-0 text-[11px] leading-5"
      data-testid="dmlex-field"
    >
      {children}
    </span>
  )
}

function Qualifier({ name, value }: { name: string; value: string }) {
  return (
    <span className="bg-muted text-muted-foreground rounded px-1 text-[10px]" title={name}>
      {value}
    </span>
  )
}

/** One field of an object: a scalar as "name value", a list of objects as one line or one
 *  block per object, named in the singular the way the XML names them. */
function Field({ name, value, depth }: { name: string; value: unknown; depth: number }) {
  if (isObjList(value)) {
    const one = singular(name)
    return (
      <>
        {value.map((item, i) => (
          <Item
            key={i}
            name={one}
            index={value.length > 1 ? i + 1 : undefined}
            item={item}
            depth={depth}
          />
        ))}
      </>
    )
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return null
    return (
      <div className="flex gap-2">
        <Name>{name}</Name>
        <span>{value.map(scalar).join(', ')}</span>
      </div>
    )
  }
  if (isObj(value)) return <Item name={name} item={value} depth={depth} />
  const text = scalar(value)
  if (!text) return null
  return (
    <div className="flex gap-2">
      <Name>{name}</Name>
      <span className="min-w-0 break-words">{text}</span>
    </div>
  )
}

/** A DMLex object. One with nothing but a text/tag and its qualifiers takes one line; one
 *  with more inside (a sense, an example with translations) becomes a block with its id. */
function Item({
  name,
  index,
  item,
  depth,
}: {
  name: string
  index?: number
  item: Obj
  depth: number
}) {
  const keys = orderedKeys(item)
  const main = scalar(item.text) || scalar(item.tag)
  const mainKey = scalar(item.text) ? 'text' : scalar(item.tag) ? 'tag' : null
  const rest = keys.filter((k) => k !== mainKey)
  const qualifiers = rest.filter(
    (k) => (QUALIFIERS.has(k) || (k === 'tag' && mainKey === 'text')) && scalar(item[k]),
  )
  const others = rest.filter((k) => !qualifiers.includes(k))
  const label = index !== undefined ? `${name} ${index}` : name
  if (mainKey && others.length === 0) {
    return (
      <div className="flex flex-wrap items-baseline gap-2">
        <Name>{label}</Name>
        <span
          className={`min-w-0 break-words ${name.endsWith('Translation') ? 'font-medium' : ''}`}
        >
          {main}
        </span>
        {qualifiers.map((k) => (
          <Qualifier key={k} name={k} value={scalar(item[k])} />
        ))}
        <Id value={item.id} />
      </div>
    )
  }
  return (
    <section
      className={`flex flex-col gap-0.5 ${depth > 0 ? 'border-border ml-2 border-l pl-3' : ''}`}
      data-testid={`dmlex-${name}`}
    >
      <header className="flex flex-wrap items-baseline gap-2">
        <span className="text-xs font-semibold">{label}</span>
        {mainKey && <span className="min-w-0 break-words">{main}</span>}
        {qualifiers.map((k) => (
          <Qualifier key={k} name={k} value={scalar(item[k])} />
        ))}
        <Id value={item.id} />
      </header>
      {others.map((k) => (
        <Field key={k} name={k} value={item[k]} depth={depth + 1} />
      ))}
    </section>
  )
}

/**
 * One DMLex entry with every field named the way DMLex names it and every id visible: the
 * headword on top, then each field as "name value", nested objects (senses, examples,
 * pronunciations) as blocks with their ids. Generic over the JSON, so extensions and
 * unexpected fields show too; nothing is hidden.
 */
export function DmlexRendering({ entry }: { entry: Obj }) {
  const keys = orderedKeys(entry).filter((k) => k !== 'headword' && k !== 'homographNumber')
  return (
    <article className="flex flex-col gap-1 p-3 text-sm leading-5">
      <header className="flex flex-wrap items-baseline gap-2">
        <Name>entry</Name>
        <span className="text-base font-semibold">{scalar(entry.headword) || '(no headword)'}</span>
        {scalar(entry.homographNumber) && (
          <sup title="homographNumber">{scalar(entry.homographNumber)}</sup>
        )}
        <Id value={entry.id} />
      </header>
      {keys.map((k) => (
        <Field key={k} name={k} value={entry[k]} depth={0} />
      ))}
      {keys.length === 0 && (
        <p className="text-muted-foreground text-xs">No fields besides the headword.</p>
      )}
    </article>
  )
}
