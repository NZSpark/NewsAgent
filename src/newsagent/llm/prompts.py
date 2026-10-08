"""Prompt registry with versions (TASK-021)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Prompt:
    version: str
    system: str
    user_template: str

    def render(self, **kwargs) -> str:
        return self.user_template.format(**kwargs)


CLASSIFIER = Prompt(
    version="article_classifier:v1",
    system=(
        "You are an AI news editor. Decide whether an article is about artificial "
        "intelligence. Respond with STRICT JSON only, no prose, no markdown fences."
    ),
    user_template=(
        "Title: {title}\nSource: {source}\nExcerpt: {excerpt}\n\n"
        "Return JSON with keys: is_ai_related (bool), topics (string[]), "
        "entities (string[]), confidence (0-1)."
    ),
)

SUMMARIZER = Prompt(
    version="article_summarizer:v1",
    system=(
        "You are a careful AI news editor. Summarize ONLY from the given text. "
        "Never invent facts or URLs. Respond with STRICT JSON only."
    ),
    user_template=(
        "Title: {title}\nSource: {source}\nText: {content}\n\n"
        "Return JSON with keys: summary (2-3 sentences, Chinese), importance (0-1), "
        "confidence (0-1)."
    ),
)

EVENT_ANALYZER = Prompt(
    version="event_analyzer:v1",
    system=(
        "You are a senior AI tech journalist. Combine multiple reports about ONE "
        "event. Use only the given material; never invent facts or URLs. "
        "Respond with STRICT JSON only."
    ),
    user_template=(
        "Event: {title}\n\nMaterials:\n{materials}\n\n"
        "Return JSON with keys: summary (Chinese, 3-5 sentences), why_it_matters "
        "(Chinese), importance (0-1), novelty (0-1), confidence (0-1), claims "
        "(array of {{text, evidence_type, source_urls}}).\n"
        "evidence_type must be one of: confirmed, reported, discussed, inferred."
    ),
)

EVIDENCE_REVIEWER = Prompt(
    version="evidence_reviewer:v1",
    system=(
        "You compare news sources and flag conflicts. Use only given material. "
        "Respond with STRICT JSON only."
    ),
    user_template=(
        "Event: {title}\nSources:\n{materials}\n\n"
        "Return JSON with keys: conflict (bool), notes (Chinese string)."
    ),
)

REGISTRY: dict[str, Prompt] = {
    "classify_article": CLASSIFIER,
    "summarize_article": SUMMARIZER,
    "event_analysis": EVENT_ANALYZER,
    "evidence_review": EVIDENCE_REVIEWER,
}


def get_prompt(task: str) -> Prompt:
    return REGISTRY[task]
