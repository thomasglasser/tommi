import logging
import re
from typing import Optional, List
from google import genai

logger = logging.getLogger("tommi.models")

# Static fallback hierarchy in case dynamic API listing is restricted
FALLBACK_FLASH_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]

_CACHED_RESOLVED_MODEL: Optional[str] = None


def resolve_model_name(client: genai.Client, configured_model: Optional[str] = "auto") -> str:
    """
    Dynamically resolves the latest available Gemini model.
    If 'auto' (or empty) is provided, queries the Gemini API for the newest supported flash model.
    If a specific model name is provided, uses it directly.
    """
    global _CACHED_RESOLVED_MODEL

    if configured_model and configured_model.strip().lower() != "auto":
        return configured_model.strip()

    if _CACHED_RESOLVED_MODEL:
        return _CACHED_RESOLVED_MODEL

    logger.info("Discovering latest available Gemini Flash model via API...")
    try:
        models_pager = client.models.list()
        flash_candidates: List[str] = []

        for model in models_pager:
            name = getattr(model, "name", "")
            if name.startswith("models/"):
                name = name[7:]

            # Filter for standard generation gemini-*-flash models, excluding special purpose audio/video/eap
            excluded_keywords = ["embedding", "vision", "audio", "video", "thinking-preview", "exp-", "-eap", "-tts"]
            if "gemini" in name and "flash" in name and not any(x in name for x in excluded_keywords):
                flash_candidates.append(name)

        if flash_candidates:
            # Sort models by version numbers extracted from the name, preferring clean base names
            def sort_key(m_name: str):
                match = re.search(r"gemini-(\d+(?:\.\d+)?)", m_name)
                ver = float(match.group(1)) if match else 0.0
                is_exact = 1 if (match and m_name == f"gemini-{match.group(1)}-flash") else 0
                return (ver, is_exact)

            sorted_candidates = sorted(flash_candidates, key=sort_key, reverse=True)
            best_model = sorted_candidates[0]
            logger.info(f"Dynamically selected latest available model: '{best_model}' (from candidates: {sorted_candidates[:3]})")
            _CACHED_RESOLVED_MODEL = best_model
            return best_model

    except Exception as e:
        logger.warning(f"Could not list models from Gemini API: {e}. Falling back to default list.")

    # Fallback to standard preference list
    fallback = FALLBACK_FLASH_MODELS[0]
    logger.info(f"Using default fallback model: '{fallback}'")
    _CACHED_RESOLVED_MODEL = fallback
    return fallback
