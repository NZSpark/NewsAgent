"""JSON schemas + validation (TASK-020).

LLM returns JSON; we validate structurally. No external jsonschema dep to keep
footprint small, but the checks mirror the design schemas.
"""
from __future__ import annotations

from ..logging_setup import SchemaError

# --- schema field specs: name -> (required, type) ------------------------- #

ARTICLE_ANALYSIS = {
    "is_ai_related": (True, bool),
    "topics": (False, list),
    "entities": (False, list),
    "summary": (False, str),
    "importance": (False, (int, float)),
    "confidence": (False, (int, float)),
}

EVENT_ANALYSIS = {
    "summary": (True, str),
    "why_it_matters": (False, str),
    "importance": (False, (int, float)),
    "novelty": (False, (int, float)),
    "confidence": (False, (int, float)),
    "claims": (False, list),
}

CLAIM = {
    "text": (True, str),
    "evidence_type": (True, str),
    "source_urls": (False, list),
}

EVIDENCE_TYPES = {"confirmed", "reported", "discussed", "inferred"}


def validate(data: dict, schema: dict, name: str = "output") -> dict:
    """Raise SchemaError when required fields are missing or mistyped."""
    if not isinstance(data, dict):
        raise SchemaError(f"{name}: expected object, got {type(data).__name__}")
    for field, (required, expected) in schema.items():
        if field not in data or data[field] is None:
            if required:
                raise SchemaError(f"{name}: missing required field '{field}'")
            continue
        value = data[field]
        if expected is bool and not isinstance(value, bool):
            raise SchemaError(f"{name}.{field}: expected bool")
        elif expected is str and not isinstance(value, str):
            raise SchemaError(f"{name}.{field}: expected string")
        elif expected is list and not isinstance(value, list):
            raise SchemaError(f"{name}.{field}: expected list")
        elif expected is (int, float) and isinstance(value, bool):
            raise SchemaError(f"{name}.{field}: expected number")
        elif expected is (int, float) and not isinstance(value, (int, float)):
            raise SchemaError(f"{name}.{field}: expected number")
    # normalize claim evidence types
    if name == "event_analysis" and isinstance(data.get("claims"), list):
        for claim in data["claims"]:
            if isinstance(claim, dict) and claim.get("evidence_type") not in EVIDENCE_TYPES:
                claim["evidence_type"] = "reported"
    return data
