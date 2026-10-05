"""Small, secret-safe client for DeepSeek's OpenAI-compatible Chat API."""

from __future__ import annotations

import requests

from app_config import get_app_config_value


API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"


class DeepSeekAPIError(RuntimeError):
    """Safe error message that never includes credentials or response bodies."""


def get_deepseek_api_key() -> str:
    return str(get_app_config_value("deepseek_api_key", "") or "").strip()


def has_deepseek_api_key() -> bool:
    return bool(get_deepseek_api_key())


def chat(messages: list[dict], *, max_tokens: int = 1200, timeout: float = 35, json_output: bool = False) -> str:
    """Return a non-thinking, bounded DeepSeek response for short tasks."""
    key = get_deepseek_api_key()
    if not key:
        raise DeepSeekAPIError("DeepSeek API anahtarı ayarlı değil.")
    try:
        response = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": MODEL,
                "messages": messages,
                "thinking": {"type": "disabled"},
                "max_tokens": max(1, min(int(max_tokens), 8192)),
                "stream": False,
                **({"response_format": {"type": "json_object"}} if json_output else {}),
            },
            timeout=(5, timeout),
        )
    except requests.Timeout as exc:
        raise DeepSeekAPIError("DeepSeek yanıtı zaman aşımına uğradı.") from exc
    except requests.RequestException as exc:
        raise DeepSeekAPIError("DeepSeek servisine bağlanılamadı.") from exc

    if response.status_code >= 400:
        raise DeepSeekAPIError(f"DeepSeek isteği başarısız oldu (HTTP {response.status_code}).")
    try:
        payload = response.json()
        answer = payload["choices"][0]["message"].get("content")
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise DeepSeekAPIError("DeepSeek geçerli bir yanıt döndürmedi.") from exc
    if not isinstance(answer, str) or not answer.strip():
        raise DeepSeekAPIError("DeepSeek yanıtı boş döndü.")
    return answer.strip()
