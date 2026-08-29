import logging
import re
from typing import Optional, List
from google import genai

logger = logging.getLogger("tommi.models")

DEFAULT_FLASH_MODELS = [
    "gemini-3.7-flash",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]

_CACHED_RESOLVED_MODELS: Optional[List[str]] = None


def resolve_candidate_models(client: genai.Client, configured_model: Optional[str] = "auto") -> List[str]:
    """
    Resolves a prioritized list of Gemini models to try.
    If a specific model name is provided, returns [configured_model].
    If 'auto' (or empty) is provided, returns the prioritized Flash hierarchy starting with gemini-3.7-flash.
    """
    global _CACHED_RESOLVED_MODELS

    if configured_model and configured_model.strip().lower() != "auto":
        return [configured_model.strip()]

    if _CACHED_RESOLVED_MODELS:
        return _CACHED_RESOLVED_MODELS

    _CACHED_RESOLVED_MODELS = list(DEFAULT_FLASH_MODELS)
    logger.info(f"Using default prioritized model list: '{_CACHED_RESOLVED_MODELS[0]}' (fallbacks: {_CACHED_RESOLVED_MODELS[1:]})")
    return _CACHED_RESOLVED_MODELS


def resolve_model_name(client: genai.Client, configured_model: Optional[str] = "auto") -> str:
    """
    Dynamically resolves the latest available Gemini model.
    If 'auto' (or empty) is provided, queries the Gemini API for the newest supported flash model.
    If a specific model name is provided, uses it directly.
    """
    candidates = resolve_candidate_models(client, configured_model)
    return candidates[0]

