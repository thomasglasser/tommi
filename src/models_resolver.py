import logging
import re
from typing import Optional, List
from google import genai

logger = logging.getLogger("tommi.models")

_CACHED_RESOLVED_MODELS: Optional[List[str]] = None


def clear_model_cache() -> None:
    """Clears the cached list of resolved candidate models."""
    global _CACHED_RESOLVED_MODELS
    _CACHED_RESOLVED_MODELS = None


def _parse_model_version(model_name: str) -> tuple:
    """Extract numeric version tuple from a model name like 'gemini-3.8-flash' -> (3, 8)."""
    match = re.search(r"gemini-(\d+(?:\.\d+)*)", model_name)
    if match:
        try:
            return tuple(int(x) for x in match.group(1).split("."))
        except ValueError:
            return (0,)
    return (0,)


def _model_sort_key(model_name: str) -> tuple:
    """
    Sort key for models:
    1. Semantic version tuple descending (e.g. (3, 8) > (3, 7))
    2. Clean base model over variants (e.g. gemini-3.8-flash > gemini-3.8-flash-lite)
    """
    ver_tuple = _parse_model_version(model_name)
    ver_str = ".".join(str(x) for x in ver_tuple)

    if model_name == f"gemini-{ver_str}-flash":
        tier = 3
    elif model_name.startswith(f"gemini-{ver_str}-flash-preview"):
        tier = 2
    elif "lite" in model_name or "8b" in model_name:
        tier = 0
    else:
        tier = 1

    return (ver_tuple, tier)


def resolve_candidate_models(client: genai.Client, configured_model: Optional[str] = "auto") -> List[str]:
    """
    Resolves a prioritized list of Gemini models to try.
    If a specific model name is provided, returns [configured_model].
    If 'auto' (or empty) is provided, queries the Gemini API for supported Flash models
    ordered from newest to oldest. Fails if the model list cannot be retrieved or contains no Flash models.
    """
    global _CACHED_RESOLVED_MODELS

    if configured_model and configured_model.strip().lower() != "auto":
        return [configured_model.strip()]

    if _CACHED_RESOLVED_MODELS:
        return _CACHED_RESOLVED_MODELS

    logger.info("Discovering available Gemini Flash models via API...")
    models_pager = client.models.list()
    flash_candidates: List[str] = []

    excluded_keywords = [
        "embedding",
        "vision",
        "audio",
        "video",
        "tts",
        "eap",
        "realtime",
        "image",
        "thinking-preview",
        "-exp",
        "exp-",
    ]

    for model in models_pager:
        name = getattr(model, "name", "")
        if name.startswith("models/"):
            name = name[7:]

        if "gemini" in name and "flash" in name and not any(x in name for x in excluded_keywords):
            flash_candidates.append(name)

    if not flash_candidates:
        raise ValueError("No suitable Gemini Flash models found from Gemini API model list.")

    unique_candidates = list(dict.fromkeys(flash_candidates))
    sorted_candidates = sorted(unique_candidates, key=_model_sort_key, reverse=True)

    logger.info(f"Discovered {len(sorted_candidates)} Flash model(s) via API: {sorted_candidates}")
    _CACHED_RESOLVED_MODELS = sorted_candidates
    return _CACHED_RESOLVED_MODELS


def resolve_model_name(client: genai.Client, configured_model: Optional[str] = "auto") -> str:
    """
    Dynamically resolves the latest available Gemini model.
    If 'auto' (or empty) is provided, queries the Gemini API for the newest supported flash model.
    If a specific model name is provided, uses it directly.
    """
    candidates = resolve_candidate_models(client, configured_model)
    return candidates[0]

