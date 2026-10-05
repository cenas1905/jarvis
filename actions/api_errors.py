"""Safe classification of common Gemini API key, quota, and access failures."""

from __future__ import annotations


def classify_gemini_api_error(exc: Exception) -> str:
    """Classify an SDK exception without returning its potentially sensitive text."""
    message = str(exc or "").casefold()
    if any(marker in message for marker in (
        "resource_exhausted", "quota", "rate limit", "too many requests", "429",
    )):
        return "quota"
    if any(marker in message for marker in (
        "api_key_invalid", "invalid api key", "api key not valid", "unauthenticated",
        "api key expired", "key was revoked",
    )):
        return "invalid_key"
    if any(marker in message for marker in ("permission_denied", "403", "forbidden")):
        return "permission"
    if any(marker in message for marker in ("not found", "404", "unsupported")):
        return "model_access"
    return "other"
