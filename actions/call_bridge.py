"""Windows Phone Link için JARVIS'in giden ses köprüsü.

Karşı tarafın sesi ayrı bir Windows speaker-loopback hattından alınır; aynı
VB-CABLE'ı iki yönde kullanıp görüşmede yankı oluşturmayız.
"""

from __future__ import annotations

from typing import Iterable

from app_config import get_app_config_value, save_app_config
from actions.platform_utils import IS_WIN


DEFAULT_OUTPUT_HINT = "cable input"


def _normalized(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def choose_bridge_output(devices: Iterable[dict], hint: str = DEFAULT_OUTPUT_HINT):
    """Choose a playback endpoint whose name matches the configured cable hint.

    PortAudio lists the same Windows endpoint once per host API. MME is the
    most conservative choice because the main JARVIS stream otherwise uses
    the Windows default MME device. The function is deliberately pure so it
    can be tested without touching the user's sound settings.
    """
    wanted = _normalized(hint) or DEFAULT_OUTPUT_HINT
    candidates = [
        item for item in devices
        if int(item.get("maxOutputChannels", 0) or 0) > 0
        and wanted in _normalized(item.get("name"))
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (
        0 if _normalized(item.get("host_api_name")) == "mme" else 1,
        int(item.get("index", 999999)),
    ))
    return candidates[0]


def list_audio_devices(owner) -> list[dict]:
    """Return a small safe description of output endpoints from a PyAudio owner."""
    items = []
    for index in range(owner.get_device_count()):
        try:
            info = dict(owner.get_device_info_by_index(index))
            if int(info.get("maxOutputChannels", 0) or 0) <= 0:
                continue
            try:
                info["host_api_name"] = owner.get_host_api_info_by_index(
                    int(info.get("hostApi", -1))
                ).get("name", "")
            except Exception:
                info["host_api_name"] = ""
            info["index"] = index
            items.append(info)
        except Exception:
            continue
    return items


def bridge_output_index(owner):
    """Return the selected CABLE Input output device index, if available."""
    picked = choose_bridge_output(
        list_audio_devices(owner),
        str(get_app_config_value("phone_call_bridge_output_hint", DEFAULT_OUTPUT_HINT) or ""),
    )
    return None if picked is None else int(picked["index"])


def phone_call_bridge(action: str = "status") -> str:
    """Inspect or toggle only JARVIS's local audio duplication preference."""
    if not IS_WIN:
        return "Telefon ses köprüsü yalnızca Windows'ta kullanılabilir."
    action = _normalized(action or "status")
    if action in {"enable", "aç", "ac"}:
        save_app_config({"phone_call_bridge_enabled": True})
        return (
            "Telefon ses köprüsü açıldı; JARVIS yanıtları normal hoparlörüne "
            "ve CABLE Input aygıtına birlikte yönlendirecek. Değişiklik anında uygulanır."
        )
    if action in {"disable", "kapat", "kapa"}:
        save_app_config({"phone_call_bridge_enabled": False})
        return "Telefon ses köprüsü kapatıldı; JARVIS yalnızca normal ses aygıtını kullanacak."
    enabled = bool(get_app_config_value("phone_call_bridge_enabled", False))
    state = "açık" if enabled else "kapalı"
    return (
        f"Telefon ses köprüsü {state}. Phone Link mikrofonu CABLE Output olmalı; "
        "karşı tarafın sesi Windows'un varsayılan hoparlör/kulaklık çıkışından ayrı yakalanır."
    )
