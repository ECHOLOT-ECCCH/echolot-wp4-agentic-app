import type { ReactNode } from 'react'

type Obj = Record<string, unknown>

const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)
const list = (v: unknown): Obj[] => (Array.isArray(v) ? v.filter(isObj) : [])
const text = (v: unknown): string =>
  typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean' ? String(v) : ''

/** The DMLex name of a value, for the tooltip: "headwordTranslation · langCode sl". */
function label(name: string, item: Obj, qualifiers: string[] = [], id?: unknown): string {
  const parts = [name]
  for (const q of qualifiers) if (text(item[q])) parts.push(`${q} ${text(item[q])}`)
  if (text(id)) parts.push(`id ${text(id)}`)
  return parts.join(' · ')
}

const ENTRY_KEYS = new Set([
  'id',
  'headword',
  'homographNumber',
  'partsOfSpeech',
  'labels',
  'pronunciations',
  'inflectedForms',
  'senses',
])
const SENSE_KEYS = new Set([
  'id',
  'indicator',
  'listingOrder',
  'labels',
  'definitions',
  'headwordTranslations',
  'headwordExplanations',
  'examples',
])
const EXAMPLE_KEYS = new Set([
  'text',
  'labels',
  'exampleTranslations',
  'sourceIdentity',
  'sourceElaboration',
  'soundFile',
  'listingOrder',
])

function Tags({ items, name }: { items: Obj[]; name: string }) {
  if (items.length === 0) return null
  return (
    <>
      {items.map((item, i) => (
        <span
          key={i}
          className="bg-muted text-muted-foreground rounded px-1 text-[11px]"
          title={label(name, item, ['listingOrder'])}
        >
          {text(item.tag)}
        </span>
      ))}
    </>
  )
}

/** Fields outside DMLex core and crosslingual (extensions, surprises): shown compactly with
 *  their name, so nothing the mapping produced is hidden. */
function Extras({ obj, known }: { obj: Obj; known: Set<string> }) {
  const keys = Object.keys(obj).filter((k) => !known.has(k))
  if (keys.length === 0) return null
  return (
    <span className="text-muted-foreground text-xs" data-testid="dmlex-extras">
      {keys.map((k) => (
        <span key={k} className="mr-2" title={k}>
          {k} {compact(obj[k])}
        </span>
      ))}
    </span>
  )
}

function compact(value: unknown): string {
  if (Array.isArray(value)) return value.map(compact).join('; ')
  if (isObj(value))
    return Object.entries(value)
      .map(([k, v]) => `${k} ${compact(v)}`)
      .join(', ')
  return text(value)
}

function Example({ example }: { example: Obj }) {
  const translations = list(example.exampleTranslations)
  return (
    <li className="text-[13px]">
      <span
        className="italic"
        title={label('example', example, ['sourceIdentity', 'listingOrder'])}
      >
        {text(example.text)}
      </span>
      <Tags items={list(example.labels)} name="label" />
      {translations.map((t, j) => (
        <span
          key={j}
          className="text-muted-foreground"
          title={label('exampleTranslation', t, ['langCode'])}
        >
          {' '}
          — {text(t.text)}
        </span>
      ))}
      <Extras obj={example} known={EXAMPLE_KEYS} />
    </li>
  )
}

function Sense({ sense, index }: { sense: Obj; index: number }) {
  const definitions = list(sense.definitions)
  const examples = list(sense.examples)
  const translations = list(sense.headwordTranslations)
  const explanations = list(sense.headwordExplanations)
  return (
    <li className="flex flex-col gap-0.5" data-testid="dmlex-sense">
      <div className="flex flex-wrap items-baseline gap-1">
        <span className="text-muted-foreground text-xs" title={label('sense', sense, [], sense.id)}>
          {index + 1}.
        </span>
        {text(sense.indicator) && (
          <span className="italic" title="indicator">
            ({text(sense.indicator)})
          </span>
        )}
        <Tags items={list(sense.labels)} name="label" />
        {definitions.map((d, i) => (
          <span key={i} title={label('definition', d, ['definitionType'])}>
            {text(d.text)}
          </span>
        ))}
        {translations.map((t, i) => (
          <span
            key={i}
            className="font-medium"
            title={label('headwordTranslation', t, ['langCode'])}
          >
            {text(t.text)}
            {i < translations.length - 1 ? ';' : ''}
          </span>
        ))}
        {explanations.map((e, i) => (
          <span
            key={i}
            className="text-muted-foreground"
            title={label('headwordExplanation', e, ['langCode'])}
          >
            [{text(e.text)}]
          </span>
        ))}
        <Extras obj={sense} known={SENSE_KEYS} />
      </div>
      {examples.length > 0 && (
        <ul className="ml-4 flex flex-col gap-0.5">
          {examples.map((example, i) => (
            <Example key={i} example={example} />
          ))}
        </ul>
      )}
    </li>
  )
}

/**
 * A dictionary-style rendering of one DMLex entry. Hover over any value to see which DMLex
 * field it is (and its language, scheme or type); the headword and the sense numbers show
 * the entry's and the senses' ids on hover. Fields outside the core and crosslingual modules
 * are shown compactly with their names.
 */
export function DmlexRendering({ entry }: { entry: Obj }) {
  const pronunciations = list(entry.pronunciations)
  const inflected = list(entry.inflectedForms)
  const senses = list(entry.senses)
  const extras: ReactNode[] = []
  pronunciations.forEach((p, i) =>
    list(p.transcriptions).forEach((t, j) =>
      extras.push(
        <span
          key={`pr-${i}-${j}`}
          className="text-muted-foreground"
          title={label('pronunciation › transcription', t, ['scheme'])}
        >
          /{text(t.text)}/
        </span>,
      ),
    ),
  )
  return (
    <article className="flex flex-col gap-2 p-3 text-sm leading-relaxed">
      <header className="flex flex-wrap items-baseline gap-2">
        <span className="text-base font-semibold" title={label('headword', entry, [], entry.id)}>
          {text(entry.headword) || '(no headword)'}
        </span>
        {text(entry.homographNumber) && (
          <sup title="homographNumber">{text(entry.homographNumber)}</sup>
        )}
        {extras}
        <Tags items={list(entry.partsOfSpeech)} name="partOfSpeech" />
        <Tags items={list(entry.labels)} name="label" />
      </header>
      {inflected.length > 0 && (
        <p className="text-muted-foreground text-xs">
          {inflected.map((f, i) => (
            <span key={i} className="mr-2" title={label('inflectedForm', f, ['tag'])}>
              {text(f.tag) && <span>{text(f.tag)} </span>}
              <span className="text-foreground">{text(f.text)}</span>
            </span>
          ))}
        </p>
      )}
      {senses.length > 0 ? (
        <ol className="flex flex-col gap-1.5">
          {senses.map((sense, i) => (
            <Sense key={i} sense={sense} index={i} />
          ))}
        </ol>
      ) : (
        <p className="text-muted-foreground text-xs">No senses.</p>
      )}
      <Extras obj={entry} known={ENTRY_KEYS} />
    </article>
  )
}
