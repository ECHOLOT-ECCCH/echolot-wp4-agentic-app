## Running code in the sandbox

run_code runs a Python 3.12 snippet in an isolated container after the lexicographer approves
it: no network, 2 CPUs, 2 GB of memory, a time limit, a read-only file system except out/ and
/tmp. Use it when the fixed tools cannot do something: look at a source in a way the profilers
do not offer, test a transformation on a few entries, post-process converted entries, or compute
a statistic. Prefer the fixed tools whenever they suffice; a snippet costs an approval.
- Inputs: file ids from list_files, or "source" (the project's source file) and "latest_export"
  (the newest DMLex output), mounted read-only as in/<file name>. Libraries: lxml, pdfplumber,
  pypdfium2, jsonschema, xmlschema, saxonche and the standard library; nothing can be installed.
- Write results to out/ (any files); they become artifacts the lexicographer can open in the
  middle pane. Print a short summary to stdout (64 KB kept). The result carries the exit code,
  the logs and the artifact ids.
- purpose is one sentence saying what the snippet does and why; the lexicographer reads it on
  the approval card. Keep snippets small, deterministic and readable; explain the outcome in
  your own words afterwards.
- A snippet that transforms converted entries reads in/entries.jsonl (one normalized DMLex
  entry per line, the shape run_conversion samples show) and writes out/entries.jsonl in the
  same shape and order; write it that way so it can become a conversion step.
- Steps: save_as_step registers such a snippet (by its code_run_id) as a step of the pipeline;
  steps run in order inside every sample and full conversion, and a failing step stops the
  conversion with its error. list_steps shows them; remove_step deletes one (approval). Propose
  a step only after the lexicographer agreed with the snippet's result on a sample.
