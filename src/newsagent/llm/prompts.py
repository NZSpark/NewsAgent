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

SEMANTIC_CLUSTER = Prompt(
    version="semantic_cluster:v1",
    system=(
        "You group AI news headlines that describe the SAME underlying event. "
        "Only merge titles that clearly refer to one event; when unsure, keep "
        "them separate. Use ONLY the given event ids. Respond with STRICT JSON."
    ),
    user_template=(
        "Events:\n{events}\n\n"
        "Return JSON: {{\"groups\": [{{\"event_ids\": [\"id1\", \"id2\"]}}]}}. "
        "Include only groups with 2+ ids. Omit singletons."
    ),
)

RESEARCH_PLANNER = Prompt(
    version="research_planner:v1",
    system=(
        "You are a research planner for an AI news desk. Given one event, "
        "propose focused follow-up questions that would verify or deepen it. "
        "Respond with STRICT JSON only."
    ),
    user_template=(
        "Event: {title}\nSummary: {summary}\n\n"
        "Return JSON: {{\"questions\": string[]}} (2-4 questions)."
    ),
)

RESEARCH_REPORT = Prompt(
    version="research_reporter:v1",
    system=(
        "You are a senior AI analyst. Write a research brief in Chinese using "
        "ONLY the given materials; never invent facts or URLs. STRICT JSON only."
    ),
    user_template=(
        "Event: {title}\nQuestions:\n{questions}\n\nMaterials:\n{materials}\n\n"
        "Return JSON: {{\"report\": string, \"key_findings\": string[], "
        "\"open_questions\": string[]}}."
    ),
)

REGISTRY: dict[str, Prompt] = {
    "classify_article": CLASSIFIER,
    "summarize_article": SUMMARIZER,
    "event_analysis": EVENT_ANALYZER,
    "evidence_review": EVIDENCE_REVIEWER,
    "cluster_review": SEMANTIC_CLUSTER,
    "research_plan": RESEARCH_PLANNER,
    "research_report": RESEARCH_REPORT,
}


def get_prompt(task: str) -> Prompt:
    return REGISTRY[task]
