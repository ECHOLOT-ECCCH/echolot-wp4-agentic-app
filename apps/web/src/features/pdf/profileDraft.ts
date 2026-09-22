import type { StructureProfile } from '@/lib/types'

/** The form's text fields and their reading as a profile (languages comma-separated). */
export interface Draft {
  source_language: string
  target_languages: string
  contents: string
  entry_id_prefix: string
}

export const toDraft = (profile: StructureProfile): Draft => ({
  source_language: profile.source_language === 'unknown' ? '' : profile.source_language,
  target_languages: profile.target_languages.join(', '),
  contents: profile.contents,
  entry_id_prefix: profile.entry_id_prefix,
})

export const fromDraft = (draft: Draft): Omit<StructureProfile, 'stored'> => ({
  source_language: draft.source_language.trim() || 'unknown',
  target_languages: draft.target_languages
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean),
  contents: draft.contents.trim(),
  entry_id_prefix: draft.entry_id_prefix.trim() || 'e',
})
