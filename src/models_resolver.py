import logging
import re
from typing import Optional, List
from google import genai

logger = logging.getLogger("tommi.models")

_CACHED_RESOLVED_MODELS: Optional[List[str]] = None


MAX_CANDIDATE_MODELS = 3
FLASH_MODEL_PATTERN = re.compile(r"^gemini-(\d+(?:\.\d+)*)-flash$")


def clear_model_cache() -> None:
    """Clears the cached list of resolved candidate models."""
    global _CACHED_RESOLVED_MODELS
    _CACHED_RESOLVED_MODELS = None


def _parse_flash_version(model_name: str) -> Optional[tuple]:
    """Extract numeric version tuple from 'gemini-X.X-flash' model name, or None if not matching."""
    match = FLASH_MODEL_PATTERN.match(model_name)
    if match:
        try:
            return tuple(int(x) for x in match.group(1).split("."))
        except ValueError:
            return None
    return None


def resolve_candidate_models(client: genai.Client, configured_model: Optional[str] = "auto") -> List[str]:
    """
    Resolves a prioritized list of Gemini models to try.
    If a specific model name is provided, returns [configured_model].
    If 'auto' (or empty) is provided, queries the Gemini API for the 3 newest standard
    'gemini-X.X-flash' models.
    Fails if the model list cannot be retrieved or contains no matching Flash models.
    """
    global _CACHED_RESOLVED_MODELS

    if configured_model and configured_model.strip().lower() != "auto":
        return [configured_model.strip()]

    if _CACHED_RESOLVED_MODELS:
        return _CACHED_RESOLVED_MODELS

    logger.info("Discovering available Gemini Flash models via API...")
    models_pager = client.models.list()
    flash_candidates: List[tuple[tuple, str]] = []

    for model in models_pager:
        name = getattr(model, "name", "")
        if name.startswith("models/"):
            name = name[7:]

        ver = _parse_flash_version(name)
        if ver is not None:
            flash_candidates.append((ver, name))

    if not flash_candidates:
        raise ValueError("No standard 'gemini-X.X-flash' models found from Gemini API model list.")

    unique_candidates = {name: ver for ver, name in flash_candidates}
    sorted_models = sorted(unique_candidates.keys(), key=lambda m: unique_candidates[m], reverse=True)
    top_candidates = sorted_models[:MAX_CANDIDATE_MODELS]

    logger.info(f"Discovered {len(sorted_models)} standard Flash model(s) via API; selected top {len(top_candidates)}: {top_candidates}")
    _CACHED_RESOLVED_MODELS = top_candidates
    return _CACHED_RESOLVED_MODELS


def resolve_model_name(client: genai.Client, configured_model: Optional[str] = "auto") -> str:
    """
    Dynamically resolves the latest available Gemini model.
    If 'auto' (or empty) is provided, queries the Gemini API for the newest supported flash model.
    If a specific model name is provided, uses it directly.
    """
    candidates = resolve_candidate_models(client, configured_model)
    return candidates[0]

