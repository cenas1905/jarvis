"""Low-latency ElevenLabs speech output for JARVIS.

The API key is deliberately read from the local JARVIS configuration at run
time.  It is never embedded in source code, logs, or network error messages.
"""

from __future__ import annotations

import threading

import requests

from app_config import get_app_config_value
from actions.call_bridge import bridge_output_index


SAMPLE_RATE = 24_000
_playback_lock = threading.Lock()
_session_voice_enabled: bool | None = None


def set_session_voice_enabled(enabled: bool) -> None:
    """Set by the Live-session preflight to avoid retries for bad voice IDs."""
    global _session_voice_enabled
    _session_voice_enabled = bool(enabled)


def is_configured() -> bool:
    return bool(str(get_app_config_value("elevenlabs_api_key", "") or "").strip())


def check_voice_access() -> tuple[bool, str]:
    """Confirm that the selected voice is available to this API account."""
    key = str(get_app_config_value("elevenlabs_api_key", "") or "").strip()
    voice_id = str(get_app_config_value("elevenlabs_voice_id", "") or "").strip()
    if not key or not voice_id:
        return False, "ElevenLabs anahtarı veya Voice ID eksik"
    try:
        response = requests.get(
            f"https://api.elevenlabs.io/v1/voices/{voice_id}",
            headers={"xi-api-key": key}, timeout=(4, 10),
        )
        try:
            if response.ok:
                return True, ""
            return False, f"ElevenLabs Voice ID erişilemiyor (HTTP {response.status_code})"
        finally:
            response.close()
    except requests.RequestException:
        return False, "ElevenLabs bağlantısı kurulamadı"


def speak(text: str) -> bool:
    """Stream a short piece of speech as raw PCM.  Returns False on fallback."""
    if _session_voice_enabled is False:
        return False
    key = str(get_app_config_value("elevenlabs_api_key", "") or "").strip()
    voice_id = str(get_app_config_value("elevenlabs_voice_id", "") or "").strip()
    if not key or not voice_id or not text.strip():
        return False

    # One speaker at a time prevents overlapping wake acknowledgements.
    if not _playback_lock.acquire(blocking=False):
        return True
    stream = None
    bridge_stream = None
    response = None
    try:
        response = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream",
            params={"output_format": "pcm_24000"},
            headers={"xi-api-key": key, "Accept": "audio/pcm"},
            json={
                "text": text[:500],
                "model_id": str(get_app_config_value(
                    "elevenlabs_model", "eleven_flash_v2_5"
                ) or "eleven_flash_v2_5"),
                "voice_settings": {"stability": 0.45, "similarity_boost": 0.75},
            },
            stream=True,
            timeout=(4, 35),
        )
        if not response.ok:
            return False

        import pyaudio

        audio = pyaudio.PyAudio()
        try:
            stream = audio.open(
                format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE, output=True
            )
            if bool(get_app_config_value("phone_call_bridge_enabled", False)):
                try:
                    bridge_index = bridge_output_index(audio)
                    if bridge_index is not None:
                        bridge_stream = audio.open(
                            format=pyaudio.paInt16,
                            channels=1,
                            rate=SAMPLE_RATE,
                            output=True,
                            output_device_index=bridge_index,
                        )
                except Exception:
                    # Keep local speech alive even if the optional call route fails.
                    bridge_stream = None
            wrote_audio = False
            for chunk in response.iter_content(chunk_size=4096):
                if chunk:
                    stream.write(chunk)
                    if bridge_stream is not None:
                        try:
                            bridge_stream.write(chunk)
                        except Exception:
                            bridge_stream.close()
                            bridge_stream = None
                    wrote_audio = True
            return wrote_audio
        finally:
            if bridge_stream is not None:
                bridge_stream.close()
            if stream is not None:
                stream.close()
            audio.terminate()
    except Exception:
        # The caller deliberately falls back to the local Windows voice. Do
        # not expose HTTP details here: some services include request metadata.
        return False
    finally:
        if response is not None:
            response.close()
        _playback_lock.release()
