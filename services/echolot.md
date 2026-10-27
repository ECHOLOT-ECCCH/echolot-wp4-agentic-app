---
name: echolot
description: Proposes Wikidata items (QID, English Wikipedia page, score) for one entity record; reconciliation candidates, not decisions
---
# echolot: entity reconciliation against Wikidata

Send one entity record, get back a ranked list of candidate Wikidata items. The service only
proposes candidates: it does not decide, verify or write anything back. A person confirms.

## Call

`ctx.service("echolot").post("/reconcile", json=entity)`: one entity per call. The runner adds
the key, retries a 502 (the upstream call failed) and limits how many calls run at once. A batch
is simply the script running over the records.

### The entity (request body)

| Field | Type | Notes |
|---|---|---|
| `uri` | string, required | The entity's id in the source system, echoed back. Use the record's id column; when there is none, build a stable one (e.g. `f"row-{record['…']}"` from a unique column). |
| `name` | string, required | The label to reconcile. **Remove `"` and `\`** first: they are escaped in the text the service builds and break the match. |
| `entity_type` | string, required | Free text: `person`, `organization`, `place`, `work`, … Not validated. |
| `identifiers` | object of strings, optional | e.g. `{"wikidata": "http://www.wikidata.org/entity/Q3777954", "VIAF": "http://viaf.org/viaf/24668337"}`. Not used for the lookup; send them for completeness and compare in the script. |
| `information` | object of strings, optional | e.g. `{"birth_year": "1928"}`. Values must be strings. Not checked against Wikidata; they only add words. |
| `context` | string, optional | **The main way to disambiguate**: a sentence about the entity (a work's title, a role, a place, dates). Build it from the record's descriptive columns. |

### The reply

`{"uri": "...", "matches": [{"url": "http://en.wikipedia.org/wiki/...", "wikidata_item_id": "Q3777954" | null, "title": "...", "score": 0.0-1.0}, ...]}`, best first.

### Errors (raised as `ServiceError` after the runner's retries)

- 422: the body is invalid (a required field missing, a non-string value in `information`).
- 502: the upstream call failed (timeout, network); retried by the runner, then the record fails.
- 401 / 503: the runner's key or the service's configuration is wrong; every record will fail.
  Stop and tell the user.

## How matching works (what to expect)

The service serializes the entity to compact JSON, sends that text to Wikifier (an entity
linker over English Wikipedia, `pageRankSqThreshold=0.8`), and keeps the Wikipedia pages whose
supporting text overlaps the `name`. The score is Wikifier's highest confidence for those
spans.

- **`matches: []` is a normal outcome**, not an error: the name was not recognised, fell below
  the threshold, or has no English Wikipedia article. Report it as "no candidate", not as a
  failure, and do not raise for it.
- **The score ranks; it does not verify.** It is the probability that the text mention refers to
  that page, not that it is the same entity. Never present 1.0 as confirmed.
- **The top match can be another person with the same name.** `information` and dates are not
  checked. Several matches can appear, e.g. one for "Einstein" and one for "Albert Einstein".
- **English only.** Entities without an English Wikipedia article cannot be found; Basque or
  Spanish context disambiguates poorly. Prefer English context sentences.
- **Coverage is uneven.** In a test run (2026-10-27), 90 of 199 well-known people from Basque art
  and letters got no candidate (Eduardo Chillida: none; Kirmen Uribe: "Uribe (surname)" at 0.38),
  while very prominent people match well (Albert Einstein: Q937 at 0.98). Say so when a sample
  shows many empty results, before proposing the full run.
- Existing identifiers are not used: if the record already has a Wikidata id, the script must
  compare it with the top match itself.

## A good script

```python
def process(record, ctx):
    name = record["name"].replace('"', "").replace("\\", "").strip()
    if not name:
        raise ValueError("the record has no name")
    entity = {
        "uri": record["uri"],
        "name": name,
        "entity_type": record.get("type") or "unknown",
        "context": record.get("description", ""),  # the descriptive column(s), as a sentence
    }
    known = record.get("wikidata", "").rsplit("/", 1)[-1] or None   # an existing QID, if any
    if known:
        entity["identifiers"] = {"wikidata": f"http://www.wikidata.org/entity/{known}"}
    reply = ctx.service("echolot").post("/reconcile", json=entity)
    matches = reply["matches"]
    top = matches[0] if matches else None
    top_qid = top["wikidata_item_id"] if top else None
    if known and top_qid:
        verdict = "agree" if known == top_qid else "conflict"
    elif top_qid:
        verdict = "new"
    else:
        verdict = "none"
    return {
        "top_qid": top_qid,
        "top_title": top["title"] if top else None,
        "top_score": top["score"] if top else None,
        "verdict": verdict,          # agree | conflict | new | none
        "candidates": len(matches),
        "matches": matches,          # the full ranked list, for review
    }
```

Adapt the column names to the file, and agree with the user which columns make the context.
Keep `matches` in the output: the review shows each candidate with its QID, Wikipedia page and
score.

## When presenting results

- Give counts: how many records got a candidate, how many agree, conflict, are new, have none.
- Show a few concrete rows of each kind, with the QID link `https://www.wikidata.org/wiki/<QID>`.
- For "none": suggest a better context sentence or checking the spelling, then a retry on those
  rows.
- A person confirms the choice per record; the candidates are proposals.
