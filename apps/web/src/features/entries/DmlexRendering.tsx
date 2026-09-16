import type { ReactNode } from 'react'

type Obj = Record<string, unknown>

const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)
const list = (v: unknown): Obj[] => (Array.isArray(v) ? v.filter(isObj) : [])
const text = (v: unknown): string =>
  typeof v === 'string' || typeof v === 'number' ? String(v) : ''

function Tags({ items, field = 'tag' }: { items: Obj[]; field?: string }) {
  if (items.length === 0) return null
  return (
    <>
      {items.map((item, i) => (
        <span key={i} className="bg-muted text-muted-foreground rounded px-1 text-[11px]">
          {text(item[field])}
        </span>
      ))}
    </>
  )
}

function Sense({ sense, index }: { sense: Obj; index: number }) {
  const definitions = list(sense.definitions)
  const examples = list(sense.examples)
  const translations = list(sense.headwordTranslations)
  const explanations = list(sense.headwordExplanations)
  return (
    <li className="flex flex-col gap-0.5">
      <div className="flex flex-wrap items-baseline gap-1">
        <span className="text-muted-foreground text-xs">{index + 1}.</span>
        {text(sense.indicator) && <span className="italic">({text(sense.indicator)})</span>}
        <Tags items={list(sense.labels)} />
        {definitions.map((d, i) => (
          <span key={i}>{text(d.text)}</span>
        ))}
        {translations.map((t, i) => (
          <span key={i} className="font-medium">
            {text(t.text)}
            {i < translations.length - 1 ? ';' : ''}
          </span>
        ))}
        {explanations.map((e, i) => (
          <span key={i} className="text-muted-foreground">
            [{text(e.text)}]
          </span>
        ))}
      </div>
      {examples.length > 0 && (
        <ul className="ml-4 flex flex-col gap-0.5">
          {examples.map((example, i) => {
            const exampleTranslations = list(example.exampleTranslations)
            return (
              <li key={i} className="text-[13px]">
                <span className="italic">{text(example.text)}</span>
                <Tags items={list(example.labels)} />
                {exampleTranslations.map((t, j) => (
                  <span key={j} className="text-muted-foreground">
                    {' '}
                    — {text(t.text)}
                  </span>
                ))}
              </li>
            )
          })}
        </ul>
      )}
    </li>
  )
}

/** A dictionary-style rendering of one DMLex entry (core + crosslingual, best effort). */
export function DmlexRendering({ entry }: { entry: Obj }) {
  const pronunciations = list(entry.pronunciations)
  const transcriptions = pronunciations.flatMap((p) => list(p.transcriptions))
  const inflected = list(entry.inflectedForms)
  const senses = list(entry.senses)
  const extras: ReactNode[] = []
  if (transcriptions.length > 0)
    extras.push(
      <span key="pr" className="text-muted-foreground">
        /{transcriptions.map((t) => text(t.text)).join(', ')}/
      </span>,
    )
  return (
    <article className="flex flex-col gap-2 p-3 text-sm leading-relaxed">
      <header className="flex flex-wrap items-baseline gap-2">
        <span className="text-base font-semibold">{text(entry.headword) || '(no headword)'}</span>
        {text(entry.homographNumber) && <sup>{text(entry.homographNumber)}</sup>}
        {extras}
        <Tags items={list(entry.partsOfSpeech)} />
        <Tags items={list(entry.labels)} />
      </header>
      {inflected.length > 0 && (
        <p className="text-muted-foreground text-xs">
          {inflected.map((f, i) => (
            <span key={i} className="mr-2">
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
      {text(entry.id) && <p className="text-muted-foreground text-[11px]">id {text(entry.id)}</p>}
    </article>
  )
}
