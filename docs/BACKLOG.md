# Backlog

Ideas and improvements that are out of scope for the current task. One line each, newest at the bottom.
Move an item into docs/PLAN.md when it is scheduled.

- Presigned S3 download URLs instead of proxying through the API (PLAN 7.5).
- Conversation compaction when history exceeds a token budget (M4).
- OCR adapter for scanned PDFs (ocrmypdf or Tesseract).
- Push converted resources to Lexonomy.
- Per-user budgets for admin-provided shared credentials.
- Automatic homograph numbering for duplicate headwords (two-pass streaming) as a conversion option; the KKS sample needs it to validate (M1.8).
- Mapping check for duplicate sense indicators cannot be static; a conversion option to suffix or merge duplicate indicators would make such sources validate (seen in Oxford: two senses "pri kriketu").
- Trigram index on entries.headword/snippet for search on very large documents.
- Persist the partial assistant turn (tool calls already made) when the provider fails mid-run, so the tool cards survive a reload; today only the run is stored and the message is dropped.
- EntryView: collapsible entry navigator so the side-by-side view gets the whole middle pane.
