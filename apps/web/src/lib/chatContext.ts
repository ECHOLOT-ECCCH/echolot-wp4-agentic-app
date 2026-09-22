import { create } from 'zustand'

/** A region of a PDF page the lexicographer selected to send with the next message. */
export interface RegionChip {
  kind: 'region'
  id: string
  label: string
  document_id: string
  file_id?: string
  page: number
  bbox: [number, number, number, number]
  text: string
  words: number
}

/** An entry as shown in the middle pane: its source, its DMLex output and the issues. */
export interface EntryChip {
  kind: 'entry'
  id: string
  label: string
  text: string // a short preview for the chip
  entry_id?: string
  document_id?: string
  ordinal?: number
  headword?: string | null
  document_name?: string
  origin?: string
  source_kind: 'xml' | 'pdf'
  page?: number
  source_text: string
  output_xml: string
  issues: string[]
}

export type ContextChip = RegionChip | EntryChip

interface ChatContextState {
  chips: ContextChip[]
  add: (chip: Omit<RegionChip, 'id' | 'label' | 'kind'>) => void
  addEntry: (chip: Omit<EntryChip, 'id' | 'label' | 'kind' | 'text'>) => void
  remove: (id: string) => void
  clear: () => void
  /** Chips to send with the next request (taken and cleared). */
  take: () => ContextChip[]
}

const MAX_CHIPS = 6
const MAX_TEXT = 4000 // what the server accepts per field

const push = (chips: ContextChip[], next: ContextChip) =>
  [...chips.filter((c) => c.id !== next.id), next].slice(-MAX_CHIPS)

export const useChatContext = create<ChatContextState>((set, get) => ({
  chips: [],
  add: (chip) =>
    set((s) => {
      const words = chip.words || chip.text.split(/\s+/).filter(Boolean).length
      const next: RegionChip = {
        ...chip,
        kind: 'region',
        words,
        id: `${chip.document_id}:${chip.page}:${chip.bbox.map((v) => Math.round(v)).join(',')}`,
        label: `page ${chip.page}, ${words} word${words === 1 ? '' : 's'}`,
      }
      return { chips: push(s.chips, next) }
    }),
  addEntry: (chip) =>
    set((s) => {
      const who = chip.ordinal !== undefined ? `entry ${chip.ordinal + 1}` : 'entry'
      const next: EntryChip = {
        ...chip,
        kind: 'entry',
        source_text: chip.source_text.slice(0, MAX_TEXT),
        output_xml: chip.output_xml.slice(0, MAX_TEXT),
        issues: chip.issues.slice(0, 40),
        id: `entry:${chip.entry_id ?? chip.headword ?? who}:${chip.origin ?? ''}`,
        label: chip.headword ? `${who} ${chip.headword}` : who,
        text: (chip.source_text || chip.output_xml).replace(/\s+/g, ' ').trim(),
      }
      return { chips: push(s.chips, next) }
    }),
  remove: (id) => set((s) => ({ chips: s.chips.filter((c) => c.id !== id) })),
  clear: () => set({ chips: [] }),
  take: () => {
    const chips = get().chips
    if (chips.length) set({ chips: [] })
    return chips
  },
}))
