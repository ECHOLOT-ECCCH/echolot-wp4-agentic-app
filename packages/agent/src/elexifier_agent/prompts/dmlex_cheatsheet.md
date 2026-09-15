## DMLex in brief (OASIS LEXIDMA DMLex 1.0)
A lexicographic resource is a list of entries. Core objects and their main members:
- entry: id, headword (required), homographNumber, partOfSpeech[], label[], pronunciation[],
  inflectedForm[], sense[]
- partOfSpeech: tag, listingOrder. label: tag, listingOrder
- pronunciation: soundFile, transcription[] (text, scheme), label[]
- inflectedForm: text, tag, label[], pronunciation[], listingOrder
- sense: id, indicator, label[], definition[] (text, definitionType), example[] (text,
  sourceIdentity, sourceElaboration, label[], soundFile)
- listingOrder members give the order of repeated items.
Optional modules: crosslingual (translations, headword translations), linking (relations between
entries or senses), etymology, annotation (inline markup), values (controlled vocabularies for
partOfSpeech, labels, definition types). DMLex is a data model; the project exports it as XML and
JSON (and NVH). Source material that does not fit core goes into a documented extension or a
label with a controlled tag, never silently dropped.
