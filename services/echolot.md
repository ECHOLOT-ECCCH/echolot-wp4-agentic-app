---
name: echolot
description: Proposes Wikidata items for one entity record, with one of three algorithms (wikifier, llm_based, rule_based); reconciliation candidates, not decisions
---
# echolot: entity reconciliation against Wikidata

Send one entity record, get back candidate Wikidata items from the algorithm you choose. The
service only proposes candidates: it does not decide, verify or write anything back. A person
confirms.

## Call

```
ctx.service("echolot").post("/reconcile", json=entity, params={"algorithm": "rule_based"})
```

One entity per call. `algorithm` is `wikifier` (the default when `params` is left out),
`llm_based` or `rule_based`, and it is the only choice a request makes: thresholds, languages,
models and data files are fixed on the server, so there is nothing else to set or tune. The
runner adds the key, waits up to three minutes, retries a 502, and limits how many calls run at
once (fewer for `llm_based`). A batch is simply the script running over the records.

Let the user choose the algorithm per run: read it from `ctx.params` (submit_run's
`script_params`, e.g. `{"algorithm": "rule_based"}`), so trying another one needs no new script.

### The entity (request body)

| Field | Type | Notes |
|---|---|---|
| `uri` | string, required | The entity's id in the source system, echoed back. Use the record's id column; when there is none, build a stable one (e.g. `f"row-{record['…']}"` from a unique column). |
| `name` | string, required | The name to reconcile. **Remove `"` and `\`** first: `wikifier` reads the entity as JSON text, where they are escaped and break the match. |
| `entity_type` | string, required | `"person"` for people, exactly: `rule_based` returns nothing for any other value, so map the file's own values ("Person", "human", "pertsona") to it. Others: `organization`, `place`, `work`, … |
| `identifiers` | object of strings, optional | e.g. `{"wikidata": "http://www.wikidata.org/entity/Q3777954", "VIAF": "…"}`. No algorithm looks them up; send them for completeness and compare in the script. |
| `information` | object, optional | Values are strings or lists of strings, e.g. `{"birth_year": "1928", "gender": "female", "publication": ["Inside the white cube"]}`. A number is rejected (422): send `"1928"`. |
| `context` | string, optional | A sentence about the entity: a work's title, a role, a place, dates. Built from the record's descriptive columns. Prefer English. |

Unknown extra fields are ignored.

### The reply

`{"uri": "...", "algorithm": "rule_based", "matches": [{"url": "...", "wikidata_item_id": "Q937" | null, "title": "...", "score": 0.97}, ...]}`, best first.
`matches: []` is a normal answer meaning "no match": report it as "no candidate", never raise
for it.

### Errors (raised as `ServiceError` after the runner's retries; `error.status` holds the code)

- 422: the body is invalid (a required field missing, a number in `information`), or the
  algorithm name is unknown.
- 502: the algorithm's external service failed (Wikifier, or the language model); retried by the
  runner, then the record fails. Those records can be retried later.
- 503 `Algorithm llm_based is not configured: …`: this ECHOLOT does not have that algorithm set
  up; every record that asks for it fails, a retry will not help, and the other algorithms
  still work. Tell the user the message and offer another algorithm.
- 401, or 503 `API key is not configured`: the runner's key or the service's configuration is
  wrong; every record will fail. Stop and tell the user.

A sample run shows all of these on its first records, before the full run.

## Choosing an algorithm

| | `wikifier` (default) | `llm_based` | `rule_based` |
|---|---|---|---|
| How | Sends the whole entity as English text to Wikifier (an entity linker over English Wikipedia) and keeps the pages linked from the name. | Searches Wikidata for the name, describes the top 5 hits, and asks a language model which one, if any, is the same person. | Looks the name up in a table of people (Getty ULAN, about 291,000 people, mostly artists, or a Wikidata dump), with phonetic blocking, biographical rules and name similarity. |
| Uses | Every field, as text; `name` and `context` most. | `name`, all of `information` (only the first 5 `publication` entries), `context`. | `name`, `entity_type`, `information.gender`. Nothing else: `birth_year` and `context` have no effect. |
| For | Any entity type | People | People only |
| Matches | 0 or more | 0 or 1 | 0 or 1; several only when the top candidates tie |
| `url` | `http://en.wikipedia.org/wiki/…` | `http://www.wikidata.org/entity/Q…` | `http://www.wikidata.org/entity/Q…`, or `http://vocab.getty.edu/ulan/…` for a person the table has no Wikidata link for |
| `wikidata_item_id` | Usually; can be `null` | Always | `null` for a ULAN-only match |
| `score` | Wikifier's link confidence, 0 to 1 | Always `1.0`: the model picks one or none; there is no confidence | 0.9 to 1; whatever comes back passed the server's threshold |
| Speed | One external call | Six Wikidata requests and a model call per entity; minutes when Wikidata is slow | Fast, except the first call after ECHOLOT starts (it indexes its table: seconds for ULAN, over a minute for the Wikidata dump) |

How to pick, when the user has no preference:

- **People with good descriptions** (dates, works, roles): `llm_based` reads them; it is slow,
  so try it on a sample first.
- **Artists and other people likely in ULAN**, or many records fast: `rule_based`. It only uses
  the name and gender, so namesakes look the same to it.
- **Anything that is not a person**, or people famous enough for an English Wikipedia
  article: `wikifier`.
- **Unsure**: compare them on a sample (the second script below) and show the user how often
  they agree.

## What to expect from each

**Scores are not comparable between algorithms.** Never sort, threshold or average matches of
different algorithms on one scale. To see whether two algorithms agree, compare
`wikidata_item_id`, not `url`: `wikifier` returns Wikipedia URLs, the others Wikidata URLs.
Link a match as `https://www.wikidata.org/wiki/<wikidata_item_id>` when it has one, its `url`
otherwise.

`wikifier`
- **The score ranks; it does not verify.** It is the probability that the text mention refers to
  that page, not that it is the same entity. Never present 1.0 as confirmed.
- **The top match can be another person with the same name, with a high score.** `information`
  and dates are not checked. Seen in the 2026-10-27 test: Rafael Ruiz Balerdi (Basque painter)
  matched Leonardo Balerdi (a footballer) at 0.93, Aurelio Arteta (Basque painter) matched Mikel
  Arteta (Q185572, a football manager) at 0.91. Compare the candidate's title with the record
  before calling it a match, and flag surname-only or different-first-name matches. Several
  matches can appear, e.g. one for "Einstein" and one for "Albert Einstein".
- **English only, uneven coverage.** Entities without an English Wikipedia article cannot be
  found. In the 2026-10-27 test, 90 of 199 well-known people from Basque art and letters got no
  candidate (Eduardo Chillida: none; Kirmen Uribe: "Uribe (surname)" at 0.38), while very
  prominent people match well (Albert Einstein: Q937 at 0.98).

`llm_based`
- **The score means nothing**: it is always 1.0. Present the match as "selected by the model",
  never as 100 % or as confirmed; leave it out of score columns.
- **"No match" is not final.** When its Wikidata lookups fail (Wikidata is slow or rate-limits),
  it answers `matches: []` without an error. Many empty results in a run, especially a large one,
  can be lookup failures: say so, and offer to run those records again later rather than
  reporting them as absent from Wikidata.
- `title` can be the bare QID when the label could not be read.

`rule_based`
- **People only, and only those in its table.** Records whose `entity_type` is not exactly
  `"person"` get no match. With ULAN, people outside the art world are mostly missing.
- **Namesakes**: it does not see dates or context. A match for a common name needs a look.
- **A match without a QID** (`wikidata_item_id: null`, a ULAN `url`) is still a candidate: keep
  it, and count it apart from Wikidata matches.
- **Answers can change after ECHOLOT restarts** (a known defect of one of its dependencies), so
  an earlier run's result is not permanent.

## A good script

One algorithm, chosen by the run's `script_params` (`{"algorithm": "llm_based"}`); `wikifier`
when none is given:

```python
def process(record, ctx):
    algorithm = ctx.params.get("algorithm", "wikifier")
    name = record["name"].replace('"', "").replace("\\", "").strip()
    if not name:
        raise ValueError("the record has no name")
    kind = (record.get("type") or "").strip().lower()
    entity = {
        "uri": record["uri"],
        "name": name,
        "entity_type": "person" if kind in ("person", "human", "persona") else kind or "unknown",
        "context": record.get("description", ""),  # the descriptive column(s), as a sentence
    }
    if record.get("birth_year"):
        entity["information"] = {"birth_year": str(record["birth_year"])}  # strings only
    known = record.get("wikidata", "").rsplit("/", 1)[-1] or None   # an existing QID, if any
    if known:
        entity["identifiers"] = {"wikidata": f"http://www.wikidata.org/entity/{known}"}
    reply = ctx.service("echolot").post("/reconcile", json=entity, params={"algorithm": algorithm})
    matches = [{**m, "algorithm": algorithm} for m in reply["matches"]]
    top = matches[0] if matches else None
    top_qid = top["wikidata_item_id"] if top else None
    if known and top_qid:
        verdict = "agree" if known == top_qid else "conflict"
    elif top_qid:
        verdict = "new"
    elif top:
        verdict = "no QID"           # a ULAN-only or Wikipedia-only match
    else:
        verdict = "none"
    return {
        "algorithm": algorithm,
        "top_qid": top_qid,
        "top_title": top["title"] if top else None,
        "top_score": top["score"] if top and algorithm != "llm_based" else None,
        "verdict": verdict,          # agree | conflict | new | no QID | none
        "candidates": len(matches),
        "matches": matches,          # the full list, for review
    }
```

Adapt the column names to the file, and agree with the user which columns make the context and
which values of the type column mean a person. Keep `matches` in the output, each tagged with
its `algorithm`: the review shows every candidate with its QID, link and score, and shows
`llm_based`'s pick as "selected" instead of a score.

Comparing the algorithms on the same records, one column of matches per algorithm (the review
offers every algorithm's candidates in one decision). Leave out an algorithm the sample shows
as not configured with `script_params` `{"algorithms": ["wikifier", "rule_based"]}`:

```python
ALGORITHMS = ["wikifier", "llm_based", "rule_based"]


def process(record, ctx):
    name = record["name"].replace('"', "").replace("\\", "").strip()
    if not name:
        raise ValueError("the record has no name")
    entity = {
        "uri": record["uri"],
        "name": name,
        "entity_type": "person",  # this file holds people only
        "context": record.get("description", ""),
    }
    found = {}
    for algorithm in ctx.params.get("algorithms", ALGORITHMS):
        reply = ctx.service("echolot").post(
            "/reconcile", json=entity, params={"algorithm": algorithm}
        )
        found[algorithm] = [{**m, "algorithm": algorithm} for m in reply["matches"]]
    tops = {a: (m[0]["wikidata_item_id"] if m else None) for a, m in found.items()}
    qids = {q for q in tops.values() if q}
    if not qids:
        agreement = "none"           # no algorithm found a Wikidata item
    elif len(qids) > 1:
        agreement = "conflict"       # different items
    elif all(tops.values()):
        agreement = "all agree"
    else:
        agreement = "some agree"     # the same item, the others found none
    return {
        "agreement": agreement,
        **{f"{a}_qid": q for a, q in tops.items()},
        **found,
    }
```

The calls for one record run one after another; the run's records run in parallel, and the
runner keeps `llm_based` to a few calls at a time. A comparison costs one call per algorithm per
record, and `llm_based` dominates the time: compare on a sample of 20 to 50 records, not the
whole file.

## When presenting results

- Name the algorithm. Give counts: how many records got a candidate, how many agree, conflict,
  are new, have none, and for `rule_based` how many matched without a QID.
- Show a few concrete rows of each kind, with the QID link `https://www.wikidata.org/wiki/<QID>`.
- Never compare or average scores across algorithms; for `llm_based` there is no score.
- For a comparison: how often the algorithms agree, and examples where they differ.
- For "none": with `wikifier`, suggest a better context sentence or checking the spelling;
  with `llm_based`, a rerun of those records later (the lookups may have failed); with
  `rule_based`, that the person may not be in its table, and another algorithm.
- A person confirms the choice per record; the candidates are proposals.
