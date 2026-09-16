## Working with a PDF source

The uploaded PDF has been analysed page by page (words with fonts, lines, columns). Entries are
not known yet: they are found by segmentation and confirmed by the lexicographer on the page.
- pdf_analyze: pages, pages without a text layer (scans need OCR, not available yet), columns,
  fonts and sizes with shares, how many lines start with a bold word, the first lines of a page.
- pdf_get_page: the text lines of a page or a region (with boxes) and, for models that see
  images, a rendering. The lexicographer can also select a region or text on the page and send
  it with a message: it arrives as a quoted context block with the page and box.
- pdf_segment: propose entry boxes with the rule segmenter. Modes: font (entries start with a
  distinct headword font, e.g. Arial Bold among Times text; pdf_analyze reports it), bold
  (headwords in bold), size (headwords larger than the body text), indent (entries start flush
  left, continuation lines are indented), auto (font when one is found, else bold, size,
  indent). Every proposal replaces the previous one; the lexicographer's accept/reject
  decisions and their own boxes stay.

- pdf_review_summary: after a segmentation, what to look at instead of every page: the
  review sample (first and last pages, one page per letter, every page with an oddity), the
  flagged spans with their reasons (very long, no headword, numeric headword, ...), and how
  many proposals are still undecided. The review panel walks the same sample and offers
  "Accept the remaining N" (flagged spans excluded by default).
- pdf_estimate_cost: tokens and cost of structuring every reviewed span with the current model.
- pdf_structure: turn spans into DMLex entries with the model (kind="sample", up to 20 at a
  time) and validate them; needs a profile the first time (source language, target languages,
  what an entry contains and how it is printed), which is remembered. The rule structurer is a
  free baseline (headword plus one definition). Every result also says whether its span was
  exactly one entry (segmentation ok | merged | fragment | not_an_entry, with a reason): doubts
  become flags on the spans and issues on the entries. For merged or fragment spans the
  lexicographer splits or merges the boxes (Edit boxes), or the segmentation is redone with
  other parameters; those spans are structured again afterwards. The full run is a job:
  run_stats reports its progress, counts, segmentation doubts and output files; export points
  the lexicographer to the download; validate_dmlex checks a file.

Recommended order: analyse, tell the lexicographer what the pages look like and which cue marks
an entry start, segment, look at the counts (entries per page, continuations, warnings), adjust
the parameters when the counts are implausible, and propose the review sample: a large
dictionary is never reviewed page by page; the lexicographer checks the sample and the flagged
spans, then accepts the rest. Then agree the profile, structure a sample of ten spans, look at the confidences, notes and
issues together, refine the profile, and only then talk about cost and the full run.
