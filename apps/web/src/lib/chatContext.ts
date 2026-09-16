import { create } from 'zustand'

/** A region of a PDF page the lexicographer selected to send with the next message. */
export interface ContextChip {
  id: string
  document_id: string
  file_id?: string
  page: number
  bbox: [number, number, number, number]
  text: string
  words: number
  label: string
}

interface ChatContextState {
  chips: ContextChip[]
  add: (chip: Omit<ContextChip, 'id' | 'label'>) => void
  remove: (id: string) => void
  clear: () => void
  /** Chips to send with the next request (taken and cleared). */
  take: () => ContextChip[]
}

const MAX_CHIPS = 6

export const useChatContext = create<ChatContextState>((set, get) => ({
  chips: [],
  add: (chip) =>
    set((s) => {
      const words = chip.words || chip.text.split(/\s+/).filter(Boolean).length
      const next: ContextChip = {
        ...chip,
        words,
        id: `${chip.document_id}:${chip.page}:${chip.bbox.map((v) => Math.round(v)).join(',')}`,
        label: `page ${chip.page}, ${words} word${words === 1 ? '' : 's'}`,
      }
      const others = s.chips.filter((c) => c.id !== next.id)
      return { chips: [...others, next].slice(-MAX_CHIPS) }
    }),
  remove: (id) => set((s) => ({ chips: s.chips.filter((c) => c.id !== id) })),
  clear: () => set({ chips: [] }),
  take: () => {
    const chips = get().chips
    if (chips.length) set({ chips: [] })
    return chips
  },
}))
