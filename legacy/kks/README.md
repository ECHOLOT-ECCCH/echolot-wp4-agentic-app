# KKS (Karjalan kielen sanakirja) inputs

Local copies of the Karelian dictionary XML (Kotimaisten kielten keskus, CC BY 4.0,
http://kaino.kotus.fi/kks/lataa/kksxml.zip), the owner's mapping (`spec.json`) and earlier DMLex
conversions. The XML and conversions are large (about 400 MB together) and are not tracked in git.
A 300-entry excerpt lives in `packages/pipeline/src/elexifier_pipeline/sample/kks-sample.xml`.

Expected files: `dict.xml` (149 MB, ~83k entries), `kks_out.xml`, `kks_out.json`, `kksxml/` (per-volume
files and `lueminut.txt`).
