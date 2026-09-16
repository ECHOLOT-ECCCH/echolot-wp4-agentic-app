"""Turn extracted entry spans into DMLex entries (PLAN 7.7 ``Structurer``).

``RuleStructurer`` is the cheap baseline (headword plus one definition). ``LlmStructurer`` asks
a model for a structured draft (``DmlexEntryDraft``) per entry, with the page crop when the model
can see images, bounded concurrency, retries, and per-entry usage. Drafts are converted to the
normalized DMLex JSON the writers and validator expect (the same shape the XML converter yields).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from elexifier_pipeline.pdf.models import BBox, EntrySpan

# --- what the model fills in ------------------------------------------------------------------


class DraftTranscription(BaseModel):
    text: str
    scheme: str | None = None


class DraftExample(BaseModel):
    text: str
    translations: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)


class DraftSense(BaseModel):
    indicator: str | None = None
    labels: list[str] = Field(default_factory=list)
    definitions: list[str] = Field(default_factory=list)
    translations: list[str] = Field(default_factory=list)  # headword translations
    explanations: list[str] = Field(default_factory=list)  # headword explanations
    examples: list[DraftExample] = Field(default_factory=list)


class DmlexEntryDraft(BaseModel):
    """One entry as the model reads it off the page. Keep the source wording; do not invent."""

    headword: str = Field(
        description="The headword exactly as printed, without the homograph number"
    )
    homograph_number: int | None = Field(
        default=None, description="Superscript or index number if printed"
    )
    parts_of_speech: list[str] = Field(
        default_factory=list, description="As printed, e.g. 'n.', 'v.'"
    )
    labels: list[str] = Field(
        default_factory=list, description="Usage or domain labels on the entry"
    )
    transcriptions: list[DraftTranscription] = Field(default_factory=list)
    inflected_forms: list[str] = Field(default_factory=list)
    senses: list[DraftSense] = Field(default_factory=list)
    confidence: float = Field(
        default=0.7, ge=0, le=1, description="How sure you are the reading is right"
    )
    notes: list[str] = Field(
        default_factory=list, description="Doubts, unreadable parts, things that did not fit"
    )


class StructureProfile(BaseModel):
    """What the dictionary is, told once by the lexicographer; goes into every prompt."""

    source_language: str = "unknown"
    target_languages: list[str] = Field(default_factory=list)
    contents: str = ""  # free text: what an entry contains, conventions, abbreviations
    entry_id_prefix: str = "e"


class ExtractedEntry(BaseModel):
    span_id: str
    page: int
    bbox: BBox
    text: str
    page_image_png: bytes | None = None


class StructuredEntry(BaseModel):
    span_id: str
    entry: dict[str, Any]  # normalized DMLex JSON
    confidence: float
    notes: list[str] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    error: str | None = None


class CostEstimate(BaseModel):
    entries: int
    input_tokens: int
    output_tokens: int
    usd_estimate: float | None
    with_images: bool
    per_entry_input: int
    per_entry_output: int
    notes: list[str] = Field(default_factory=list)


# --- draft -> DMLex JSON ------------------------------------------------------------------------


def draft_to_dmlex(
    draft: DmlexEntryDraft, *, entry_id: str, target_language: str | None = None
) -> dict[str, Any]:
    """Normalized DMLex JSON (plural keys as the writers expect). Translations always carry a
    langCode: the XSD's assertion cannot see the resource's translationLanguage list (XSD 1.1
    assertions are evaluated on the element subtree), so "und" stands in when none is known."""
    target_language = target_language or "und"
    entry: dict[str, Any] = {"id": entry_id, "headword": draft.headword.strip()}
    if draft.homograph_number is not None:
        entry["homographNumber"] = draft.homograph_number
    if draft.parts_of_speech:
        entry["partsOfSpeech"] = [{"tag": t.strip()} for t in draft.parts_of_speech if t.strip()]
    if draft.labels:
        entry["labels"] = [{"tag": t.strip()} for t in draft.labels if t.strip()]
    if draft.transcriptions:
        entry["pronunciations"] = [
            {
                "transcriptions": [
                    {"text": t.text.strip(), **({"scheme": t.scheme} if t.scheme else {})}
                    for t in draft.transcriptions
                    if t.text.strip()
                ]
            }
        ]
    if draft.inflected_forms:
        entry["inflectedForms"] = [{"text": f.strip()} for f in draft.inflected_forms if f.strip()]
    senses = []
    for index, sense in enumerate(draft.senses, start=1):
        out: dict[str, Any] = {"id": f"{entry_id}.s{index}"}
        if sense.indicator:
            out["indicator"] = sense.indicator.strip()
        if sense.labels:
            out["labels"] = [{"tag": t.strip()} for t in sense.labels if t.strip()]
        if sense.definitions:
            out["definitions"] = [{"text": d.strip()} for d in sense.definitions if d.strip()]
        if sense.translations:
            out["headwordTranslations"] = [
                {"text": t.strip(), "langCode": target_language}
                for t in sense.translations
                if t.strip()
            ]
        if sense.explanations:
            out["headwordExplanations"] = [
                {"text": t.strip(), "langCode": target_language}
                for t in sense.explanations
                if t.strip()
            ]
        examples = []
        for example in sense.examples:
            if not example.text.strip():
                continue
            ex: dict[str, Any] = {"text": example.text.strip()}
            if example.labels:
                ex["labels"] = [{"tag": t.strip()} for t in example.labels if t.strip()]
            if example.translations:
                ex["exampleTranslations"] = [
                    {
                        "text": t.strip(),
                        **({"langCode": target_language} if target_language else {}),
                    }
                    for t in example.translations
                    if t.strip()
                ]
            examples.append(ex)
        if examples:
            out["examples"] = examples
        senses.append(out)
    if senses:
        entry["senses"] = senses
    return entry


# --- structurers --------------------------------------------------------------------------------


class RuleStructurer:
    """Headword = first word (bold when known), everything else one definition. A baseline that
    costs nothing and shows the pipeline works before a model is involved."""

    name = "rule"

    def __init__(self, profile: StructureProfile | None = None) -> None:
        self.profile = profile or StructureProfile()

    async def structure(
        self, entries: Sequence[ExtractedEntry], **_: Any
    ) -> AsyncIterator[StructuredEntry]:
        for index, extracted in enumerate(entries, start=1):
            words = extracted.text.split()
            headword = words[0].rstrip(",.;:") if words else ""
            rest = " ".join(words[1:]).strip()
            draft = DmlexEntryDraft(
                headword=headword,
                senses=[DraftSense(definitions=[rest])] if rest else [],
                confidence=0.3,
                notes=["rule structurer: headword and one definition only"],
            )
            yield StructuredEntry(
                span_id=extracted.span_id,
                entry=draft_to_dmlex(draft, entry_id=f"{self.profile.entry_id_prefix}{index}"),
                confidence=draft.confidence,
                notes=draft.notes,
            )

    async def estimate(
        self, entry_count: int, avg_chars: int, with_images: bool, **_: Any
    ) -> CostEstimate:
        return CostEstimate(
            entries=entry_count,
            input_tokens=0,
            output_tokens=0,
            usd_estimate=0.0,
            with_images=False,
            per_entry_input=0,
            per_entry_output=0,
            notes=["the rule structurer calls no model"],
        )


INSTRUCTIONS = """You read one dictionary entry from a scanned or typeset page and transcribe its
structure faithfully. Rules:
- Copy the source wording; never invent, translate or normalise text that is not on the page.
- The headword is the lemma as printed (without a homograph number, which goes to its own field).
- Parts of speech, labels and abbreviations stay as printed ("n.", "adj.", "fam.").
- Each numbered or clearly separated meaning is one sense; a definition, gloss or translation
  belongs to the sense it follows; example sentences go to the sense they illustrate.
- In bilingual dictionaries, equivalents in the target language are translations, not definitions.
- Lower the confidence when the text is garbled, truncated or ambiguous, and say why in notes.
- If the text is not a dictionary entry at all (a running head, a note), return an empty
  headword and explain in notes.
"""


@dataclass
class LlmStructurer:
    """A Pydantic AI agent per entry with structured output; the model is the project's."""

    model: Any  # pydantic_ai Model
    profile: StructureProfile = field(default_factory=StructureProfile)
    concurrency: int = 4
    retries: int = 2
    name: str = "llm"

    def _agent(self) -> Any:
        from pydantic_ai import (
            Agent,
        )

        profile = self.profile
        languages = ", ".join(profile.target_languages) or "none (monolingual)"
        instructions = (
            INSTRUCTIONS
            + f"\nDictionary: source language {profile.source_language}; target languages: "
            f"{languages}."
            + (
                f"\nWhat entries contain and how they are printed: {profile.contents}"
                if profile.contents
                else ""
            )
        )
        return Agent(
            self.model,
            output_type=DmlexEntryDraft,
            instructions=instructions,
            retries=self.retries,
            name="elexifier-structurer",
        )

    async def structure(
        self, entries: Sequence[ExtractedEntry], **_: Any
    ) -> AsyncIterator[StructuredEntry]:
        from pydantic_ai import BinaryContent

        agent = self._agent()
        semaphore = asyncio.Semaphore(self.concurrency)
        target = self.profile.target_languages[0] if self.profile.target_languages else None

        async def one(index: int, extracted: ExtractedEntry) -> StructuredEntry:
            text = f"Entry text (page {extracted.page}):\n{extracted.text.strip()}"
            prompt: str | list[Any] = text
            if extracted.page_image_png:
                prompt = [
                    text + "\n\nThe image shows the same region of the page.",
                    BinaryContent(data=extracted.page_image_png, media_type="image/png"),
                ]
            async with semaphore:
                try:
                    result = await agent.run(prompt)
                except Exception as exc:  # the batch goes on; the failure is recorded
                    return StructuredEntry(
                        span_id=extracted.span_id,
                        entry={},
                        confidence=0.0,
                        notes=[f"model error: {exc}"[:300]],
                        error=f"{type(exc).__name__}: {exc}"[:500],
                    )
            draft: DmlexEntryDraft = result.output
            usage = result.usage
            return StructuredEntry(
                span_id=extracted.span_id,
                entry=draft_to_dmlex(
                    draft, entry_id=f"{self.profile.entry_id_prefix}{index}", target_language=target
                ),
                confidence=draft.confidence,
                notes=draft.notes,
                usage={
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_tokens": getattr(usage, "cache_read_tokens", 0) or 0,
                    "requests": usage.requests,
                },
            )

        tasks = [asyncio.create_task(one(i, e)) for i, e in enumerate(entries, start=1)]
        for task in tasks:  # keep the input order
            yield await task

    async def estimate(
        self,
        entry_count: int,
        avg_chars: int,
        with_images: bool,
        price_in_per_m: float | None = None,
        price_out_per_m: float | None = None,
    ) -> CostEstimate:
        return estimate_cost(
            entry_count,
            avg_chars,
            with_images,
            price_in_per_m=price_in_per_m,
            price_out_per_m=price_out_per_m,
        )


PROMPT_OVERHEAD_TOKENS = 700  # instructions, schema and profile per request
IMAGE_TOKENS = 1100  # a span crop at 110 dpi (Anthropic: width*height/750)
OUTPUT_TOKENS_PER_ENTRY = 260


def estimate_cost(
    entry_count: int,
    avg_chars: int,
    with_images: bool,
    *,
    price_in_per_m: float | None = None,
    price_out_per_m: float | None = None,
) -> CostEstimate:
    per_in = PROMPT_OVERHEAD_TOKENS + max(1, avg_chars) // 4 + (IMAGE_TOKENS if with_images else 0)
    per_out = OUTPUT_TOKENS_PER_ENTRY + max(1, avg_chars) // 6
    total_in = per_in * entry_count
    total_out = per_out * entry_count
    usd = None
    notes = []
    if price_in_per_m is not None and price_out_per_m is not None:
        usd = round(total_in / 1e6 * price_in_per_m + total_out / 1e6 * price_out_per_m, 4)
    else:
        notes.append("no prices known for this model; tokens only")
    if with_images:
        notes.append("image tokens assume a span crop at 110 dpi")
    return CostEstimate(
        entries=entry_count,
        input_tokens=total_in,
        output_tokens=total_out,
        usd_estimate=usd,
        with_images=with_images,
        per_entry_input=per_in,
        per_entry_output=per_out,
        notes=notes,
    )


def renumber_entry(entry: dict[str, Any], entry_id: str) -> dict[str, Any]:
    """Give an entry (and its senses) ids derived from ``entry_id``; batches and cached results
    number from 1, so ids are assigned by position when a run is assembled."""
    out = dict(entry)
    out["id"] = entry_id
    senses = []
    for index, sense in enumerate(entry.get("senses") or [], start=1):
        if isinstance(sense, dict):
            senses.append({**sense, "id": f"{entry_id}.s{index}"})
    if senses:
        out["senses"] = senses
    return out


def extracted_from_span(span: EntrySpan, image: bytes | None = None) -> ExtractedEntry:
    return ExtractedEntry(
        span_id=span.id, page=span.page, bbox=span.bbox, text=span.text, page_image_png=image
    )


__all__ = [
    "CostEstimate",
    "DmlexEntryDraft",
    "DraftExample",
    "DraftSense",
    "ExtractedEntry",
    "LlmStructurer",
    "RuleStructurer",
    "StructureProfile",
    "StructuredEntry",
    "draft_to_dmlex",
    "estimate_cost",
    "extracted_from_span",
    "renumber_entry",
]
