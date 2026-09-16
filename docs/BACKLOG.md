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
- Faster full-run validation: xmlschema (XSD 1.1) needs ~2.3 ms per entry and the whole output in memory; validate structure per entry during conversion and keep only the document-wide identity constraints for the end.
- Append a job-result message to the conversation when a full run finishes so the agent reports without being asked (PLAN section 4 flow).
- Cancel a running full run from the UI and the chat.
- Converter option to insert a space at element boundaries in innerTextRec (the Oxford run-together "o otrokučebljanje" when an inline indicator is folded into the translation).
- Show cache read/write tokens and an estimated cost per turn in the UI (usage_records already store tokens).
- PDF viewer: a rotated-page fixture (/Rotate 90) to verify the overlay geometry, and text-layer highlighting of the selected span's words.
- Prices for direct-provider models: let the owner enter prices per credential (declared_capabilities) so estimates give money, not only tokens.
- PDF review panel: a "Structure sample" button (the API and tool exist; the UI relies on the agent for now).
- Review scoring: per-span "first word not in the headword font" and per-page "a flush-left line in the headword font that did not start an entry" (the segmenter must hand its evidence to the scorer); the learned segmenter (M3) as a second opinion producing disagreement flags.
- Review sample: let the lexicographer choose the sample size, redraw it, and mark sample pages as seen.
- Act on structurer verdicts: offer to split a merged span at the named headword (the line that starts with it), merge a fragment with its predecessor, reject a not_an_entry span, as one-click suggestions in the review panel and as an agent tool.
