## Working with a PDF source

The uploaded PDF has been analysed page by page (words with fonts, lines, columns). Entries are
not known yet: they are found by segmentation and confirmed by the lexicographer on the page.
- pdf_analyze: pages, pages without a text layer (scans need OCR, not available yet), columns,
  fonts and sizes with shares, how many lines start with a bold word, the first lines of a page.
- pdf_get_page: the text lines of a page or a region (with boxes) and, for models that see
  images, a rendering. The lexicographer can also select a region or text on the page and send
  it with a message: it arrives as a quoted context block with the page and box.
- pdf_segment: propose entry boxes with the rule segmenter. Modes: bold (headwords in bold),
  size (headwords larger than the body text), indent (entries start flush left, continuation
  lines are indented), auto (decides from the bold share). Every proposal replaces the previous
  one; the lexicographer's accept/reject decisions and their own boxes stay.

Recommended order: analyse, tell the lexicographer what the pages look like and which cue marks
an entry start, segment, look at the counts (entries per page, continuations, warnings), adjust
the parameters when the counts are implausible, and ask the lexicographer to review a page.
Structuring entries into DMLex comes after the review.
